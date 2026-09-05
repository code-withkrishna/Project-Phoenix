"""Checkout, Simulation, and Enterprise Merchant Command Center Portal."""

import hashlib
import hmac
import json
import logging
import time
from typing import Any
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.repositories.recovery_cases import RecoveryCaseRepository
from app.services.razorpay.client import RazorpayAPIError, RazorpayClient
from app.services.webhooks.dispatcher import WebhookDispatcher
from app.services.webhooks.ingestion import WebhookIngestionService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/checkout", tags=["checkout"])


class CreateOrderRequest(BaseModel):
    amount: int = Field(default=299900, description="Amount in paise (e.g., 299900 = ₹2,999.00)")
    currency: str = Field(default="INR")
    customer_email: str = Field(default="demo.customer@example.com")
    customer_phone: str = Field(default="+919876543210")
    customer_name: str = Field(default="Aditya Sharma")


class CreateOrderResponse(BaseModel):
    order_id: str
    amount: int
    currency: str
    key_id: str
    receipt: str


class SimulateFailureRequest(BaseModel):
    scenario: str = Field(default="otp_friction", description="Simulation scenario preset")
    amount_inr: float = Field(default=2999.0, description="Amount in INR")
    customer_name: str = Field(default="Aditya Sharma")
    customer_email: str = Field(default="aditya.sharma@example.com")
    customer_phone: str = Field(default="+919876543210")


@router.post("/create-order", response_model=CreateOrderResponse)
async def create_test_order(
    req: CreateOrderRequest,
    settings: Settings = Depends(get_settings),
) -> CreateOrderResponse:
    """Create a real Razorpay Test Order for checkout testing."""
    client = RazorpayClient(settings)
    receipt_id = f"rcpt_phx_{uuid.uuid4().hex[:8]}"
    try:
        order_data = await client.create_order(
            amount=req.amount,
            currency=req.currency,
            receipt=receipt_id,
            notes={
                "customer_email": req.customer_email,
                "customer_phone": req.customer_phone,
                "customer_name": req.customer_name,
                "created_by": "phoenix-checkout-e2e",
            },
        )
        return CreateOrderResponse(
            order_id=order_data["id"],
            amount=order_data["amount"],
            currency=order_data["currency"],
            key_id=settings.razorpay_key_id,
            receipt=receipt_id,
        )
    except RazorpayAPIError as exc:
        logger.error("Failed to create Razorpay test order: %s", exc)
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except Exception as exc:
        logger.exception("Unexpected error creating Razorpay order: %s", exc)
        raise HTTPException(status_code=500, detail="Unable to create order with payment gateway.") from exc
    finally:
        await client.close()


@router.post("/simulate-failure")
async def simulate_failure_event(
    req: SimulateFailureRequest,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """1-Click failure simulator for rapid evaluation and demonstration."""
    payment_id = f"pay_phx_{uuid.uuid4().hex[:12]}"
    order_id = f"order_phx_{uuid.uuid4().hex[:10]}"
    amount_paise = int(req.amount_inr * 100)

    # Scenarios mapping
    scenario_configs = {
        "otp_friction": {
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "Payment failed due to OTP authentication timeout or user cancellation",
            "error_reason": "otp_timeout",
            "payment_method": "upi",
        },
        "insufficient_funds": {
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "Payment failed due to insufficient account balance",
            "error_reason": "insufficient_funds",
            "payment_method": "card",
        },
        "bank_downtime": {
            "error_code": "GATEWAY_ERROR",
            "error_description": "Issuing bank gateway downtime or timeout",
            "error_reason": "issuer_down_downtime",
            "payment_method": "netbanking",
        },
        "card_expired": {
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "Card instrument expired or invalid",
            "error_reason": "card_expired_switch_method",
            "payment_method": "card",
        },
        "high_value": {
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "High value payment authentication interrupted",
            "error_reason": "high_value_human_review",
            "payment_method": "card",
            "override_amount": 1500000,  # ₹15,000 to trigger POL-008
        },
        "fraud_security": {
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "Payment declined due to security blacklist and suspected fraud risk",
            "error_reason": "fraud_security_risk",
            "payment_method": "card",
        },
    }

    config = scenario_configs.get(req.scenario, scenario_configs["otp_friction"])
    if "override_amount" in config and req.scenario == "high_value":
        amount_paise = config["override_amount"]

    event_id = f"evt_phx_{uuid.uuid4().hex[:12]}"
    payload = {
        "entity": "event",
        "account_id": "acc_phx_demo",
        "event": "payment.failed",
        "contains": ["payment"],
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "entity": "payment",
                    "amount": amount_paise,
                    "currency": "INR",
                    "status": "failed",
                    "order_id": order_id,
                    "method": config["payment_method"],
                    "email": req.customer_email,
                    "contact": req.customer_phone,
                    "error_code": config["error_code"],
                    "error_description": config["error_description"],
                    "error_source": "bank",
                    "error_step": "payment_authentication",
                    "error_reason": config["error_reason"],
                    "created_at": int(time.time()),
                }
            }
        },
        "created_at": int(time.time()),
    }

    payload_bytes = json.dumps(payload).encode("utf-8")
    sig = hmac.new(settings.razorpay_webhook_secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()

    client = RazorpayClient(settings)
    try:
        ingestion_service = WebhookIngestionService(session)
        stored_event, _created = await ingestion_service.ingest(
            event_id=event_id,
            event_type="payment.failed",
            payload=payload,
            signature=sig,
        )

        dispatcher = WebhookDispatcher(session, settings, razorpay_client=client, auto_orchestrate=True)
        # Pass stored_event.event_id so repository query resolves correctly
        await dispatcher.process_event(stored_event.event_id)
    except Exception as exc:
        logger.exception("Simulation execution encountered error for scenario '%s': %s", req.scenario, exc)
        return {
            "success": False,
            "error": {
                "code": "RECOVERY_SIMULATION_FAILED",
                "message": f"Unable to process the failure scenario: {str(exc)}",
            },
        }
    finally:
        await client.close()

    case_repo = RecoveryCaseRepository(session)
    case = await case_repo.get_by_payment_id(payment_id)

    return {
        "success": True,
        "scenario": req.scenario,
        "payment_id": payment_id,
        "order_id": order_id,
        "amount_paise": amount_paise,
        "amount_inr": amount_paise / 100,
        "case_id": str(case.id) if case else None,
        "case_status": case.status if case else None,
        "is_recovered": case.is_recovered if case else False,
        "failure_reason": case.failure_reason if case else config["error_reason"],
    }


@router.get("/portal", response_model=None, response_class=HTMLResponse)
async def get_test_checkout_portal(
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """Enterprise Phoenix Merchant Command Center & Decision Portal."""
    key_id = settings.razorpay_key_id
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Project Phoenix — Autonomous Revenue Recovery Command Center</title>
    <script src="https://checkout.razorpay.com/v1/checkout.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-base: #0a0e17;
            --bg-card: #111827;
            --bg-card-alt: #161f30;
            --bg-surface: #1e293b;
            --bg-surface-hover: #27354f;
            --border-color: #1f293d;
            --border-focus: #3b82f6;
            --border-light: #2e3d5b;
            
            --text-primary: #f9fafb;
            --text-secondary: #94a3b8;
            --text-muted: #64748b;
            
            --blue-primary: #3b82f6;
            --blue-hover: #2563eb;
            --blue-subtle: rgba(59, 130, 246, 0.1);
            --blue-border: rgba(59, 130, 246, 0.3);
            
            --green-accent: #10b981;
            --green-subtle: rgba(16, 185, 129, 0.12);
            --green-border: rgba(16, 185, 129, 0.3);
            
            --amber-accent: #f59e0b;
            --amber-subtle: rgba(245, 158, 11, 0.12);
            --amber-border: rgba(245, 158, 11, 0.3);
            
            --red-accent: #ef4444;
            --red-subtle: rgba(239, 68, 68, 0.12);
            --red-border: rgba(239, 68, 68, 0.3);

            --radius-sm: 4px;
            --radius-md: 6px;
            --radius-lg: 8px;
        }}

        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        
        body {{
            background-color: var(--bg-base);
            color: var(--text-primary);
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            font-size: 13.5px;
            line-height: 1.5;
            min-height: 100vh;
            padding: 16px 24px 32px 24px;
        }}

        /* Header */
        .app-header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding-bottom: 16px;
            border-bottom: 1px solid var(--border-color);
            margin-bottom: 20px;
        }}
        .brand-cluster {{
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        .brand-logo {{
            width: 32px;
            height: 32px;
            background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%);
            border-radius: var(--radius-md);
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            font-size: 16px;
            color: #ffffff;
            box-shadow: 0 2px 4px rgba(0,0,0,0.2);
        }}
        .brand-title {{
            font-size: 17px;
            font-weight: 700;
            letter-spacing: -0.01em;
            color: var(--text-primary);
        }}
        .brand-subtitle {{
            font-size: 12px;
            color: var(--text-muted);
            font-weight: 500;
        }}
        .header-meta {{
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .status-pill {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 4px 10px;
            border-radius: 9999px;
            font-size: 12px;
            font-weight: 500;
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            color: var(--text-secondary);
        }}
        .status-dot {{
            width: 7px;
            height: 7px;
            border-radius: 50%;
            background-color: var(--green-accent);
        }}
        .badge-test-mode {{
            background: var(--blue-subtle);
            border: 1px solid var(--blue-border);
            color: #93c5fd;
        }}
        .badge-live-source {{
            border: 1px solid var(--border-light);
            color: var(--text-secondary);
        }}

        /* KPI Row */
        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 14px;
            margin-bottom: 20px;
        }}
        .kpi-card {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: var(--radius-lg);
            padding: 16px 18px;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
        }}
        .kpi-label {{
            font-size: 11.5px;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            font-weight: 600;
            color: var(--text-muted);
            margin-bottom: 6px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .kpi-value {{
            font-size: 24px;
            font-weight: 700;
            letter-spacing: -0.02em;
            color: var(--text-primary);
            font-family: 'JetBrains Mono', monospace;
            margin-bottom: 4px;
        }}
        .kpi-subtext {{
            font-size: 11.5px;
            color: var(--text-secondary);
            display: flex;
            align-items: center;
            gap: 6px;
        }}
        .text-green {{ color: var(--green-accent); }}
        .text-amber {{ color: var(--amber-accent); }}
        .text-red {{ color: var(--red-accent); }}
        .text-blue {{ color: var(--blue-primary); }}

        /* Simulator Bar */
        .simulator-bar {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: var(--radius-lg);
            padding: 12px 18px;
            margin-bottom: 20px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
            flex-wrap: wrap;
        }}
        .sim-controls {{
            display: flex;
            align-items: center;
            gap: 10px;
            flex: 1;
        }}
        .sim-label {{
            font-size: 12px;
            font-weight: 600;
            color: var(--text-secondary);
            white-space: nowrap;
        }}
        .sim-select {{
            background: var(--bg-surface);
            border: 1px solid var(--border-light);
            color: var(--text-primary);
            padding: 7px 12px;
            border-radius: var(--radius-md);
            font-size: 13px;
            font-family: inherit;
            outline: none;
            min-width: 220px;
        }}
        .sim-select:focus {{
            border-color: var(--border-focus);
        }}
        .btn {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
            padding: 7px 14px;
            border-radius: var(--radius-md);
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            border: 1px solid transparent;
            transition: all 0.15s ease;
            text-decoration: none;
            font-family: inherit;
        }}
        .btn-primary {{
            background: var(--blue-primary);
            color: #ffffff;
        }}
        .btn-primary:hover {{
            background: var(--blue-hover);
        }}
        .btn-secondary {{
            background: var(--bg-surface);
            border-color: var(--border-light);
            color: var(--text-primary);
        }}
        .btn-secondary:hover {{
            background: var(--bg-surface-hover);
        }}
        .btn-danger-outline {{
            background: transparent;
            border: 1px solid var(--red-border);
            color: #f87171;
        }}
        .btn-danger-outline:hover {{
            background: var(--red-subtle);
        }}
        .btn-success {{
            background: var(--green-accent);
            color: #ffffff;
        }}
        .btn-success:hover {{
            background: #059669;
        }}
        .sim-status-ticker {{
            font-size: 12px;
            color: var(--text-muted);
            font-family: 'JetBrains Mono', monospace;
            display: flex;
            align-items: center;
            gap: 6px;
        }}

        /* Main Workspace: 2-Column Operational Grid */
        .workspace-grid {{
            display: grid;
            grid-template-columns: 1.15fr 0.85fr;
            gap: 20px;
            margin-bottom: 20px;
        }}

        /* Operational Card Base */
        .op-card {{
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: var(--radius-lg);
            overflow: hidden;
            display: flex;
            flex-direction: column;
        }}
        .op-card-header {{
            padding: 12px 18px;
            border-bottom: 1px solid var(--border-color);
            background: var(--bg-card-alt);
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}
        .op-card-title {{
            font-size: 13px;
            font-weight: 600;
            color: var(--text-primary);
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .op-card-body {{
            padding: 16px 18px;
            flex: 1;
        }}

        /* Table Styling */
        .cases-table-wrap {{
            overflow-x: auto;
            max-height: 520px;
            overflow-y: auto;
        }}
        table.cases-table {{
            width: 100%;
            border-collapse: collapse;
            text-align: left;
            font-size: 12.5px;
        }}
        table.cases-table th {{
            background: var(--bg-card-alt);
            color: var(--text-muted);
            font-weight: 600;
            text-transform: uppercase;
            font-size: 11px;
            letter-spacing: 0.04em;
            padding: 10px 12px;
            border-bottom: 1px solid var(--border-color);
            position: sticky;
            top: 0;
            z-index: 2;
        }}
        table.cases-table td {{
            padding: 10px 12px;
            border-bottom: 1px solid var(--border-color);
            color: var(--text-primary);
        }}
        table.cases-table tr {{
            cursor: pointer;
            transition: background 0.1s ease;
        }}
        table.cases-table tr:hover {{
            background: var(--bg-surface);
        }}
        table.cases-table tr.selected {{
            background: var(--bg-surface-hover);
            outline: 1px solid var(--border-focus);
        }}
        .mono-cell {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 12px;
        }}
        
        /* Status Badges */
        .badge-pill {{
            display: inline-block;
            padding: 2px 7px;
            border-radius: var(--radius-sm);
            font-size: 11px;
            font-weight: 600;
            letter-spacing: 0.02em;
            text-transform: uppercase;
        }}
        .badge-awaiting {{ background: var(--blue-subtle); color: #93c5fd; border: 1px solid var(--blue-border); }}
        .badge-escalated {{ background: var(--amber-subtle); color: #fbbf24; border: 1px solid var(--amber-border); }}
        .badge-recovered {{ background: var(--green-subtle); color: #34d399; border: 1px solid var(--green-border); }}
        .badge-rejected {{ background: var(--red-subtle); color: #f87171; border: 1px solid var(--red-border); }}
        .badge-detected {{ background: var(--bg-surface); color: var(--text-secondary); border: 1px solid var(--border-light); }}

        /* Detail Panel */
        .detail-header-strip {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 16px;
            padding-bottom: 12px;
            border-bottom: 1px solid var(--border-color);
        }}
        .detail-amount-large {{
            font-size: 22px;
            font-weight: 700;
            font-family: 'JetBrains Mono', monospace;
            color: var(--text-primary);
        }}
        .detail-failure-badge {{
            font-size: 12px;
            color: var(--text-secondary);
            margin-top: 2px;
        }}

        /* 6-Node Pipeline */
        .pipeline-container {{
            display: grid;
            grid-template-columns: repeat(6, 1fr);
            gap: 4px;
            margin-bottom: 18px;
            background: var(--bg-card-alt);
            padding: 10px 8px;
            border-radius: var(--radius-md);
            border: 1px solid var(--border-color);
        }}
        .pipe-node {{
            text-align: center;
            padding: 4px 2px;
            border-radius: var(--radius-sm);
            font-size: 10.5px;
            font-weight: 600;
            color: var(--text-muted);
            letter-spacing: 0.02em;
            background: transparent;
            transition: all 0.2s ease;
        }}
        .pipe-node.active {{
            background: var(--blue-subtle);
            color: #93c5fd;
            border: 1px solid var(--blue-border);
        }}
        .pipe-node.done {{
            background: var(--green-subtle);
            color: #34d399;
            border: 1px solid var(--green-border);
        }}
        .pipe-node.escalated {{
            background: var(--amber-subtle);
            color: #fbbf24;
            border: 1px solid var(--amber-border);
        }}
        .pipe-node.rejected {{
            background: var(--red-subtle);
            color: #f87171;
            border: 1px solid var(--red-border);
        }}

        /* Decision Section Grid */
        .decision-sections {{
            display: flex;
            flex-direction: column;
            gap: 12px;
        }}
        .section-box {{
            background: var(--bg-surface);
            border: 1px solid var(--border-color);
            border-radius: var(--radius-md);
            padding: 12px 14px;
        }}
        .section-box-title {{
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            color: var(--text-muted);
            margin-bottom: 8px;
            display: flex;
            justify-content: space-between;
        }}
        .section-grid-2 {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 10px;
        }}
        .stat-row {{
            display: flex;
            justify-content: space-between;
            font-size: 12.5px;
            margin-bottom: 4px;
        }}
        .stat-label {{ color: var(--text-secondary); }}
        .stat-val {{ font-weight: 600; font-family: 'JetBrains Mono', monospace; }}

        /* HITL Action Box */
        .hitl-alert-box {{
            background: var(--amber-subtle);
            border: 1px solid var(--amber-border);
            border-radius: var(--radius-md);
            padding: 14px;
            margin-top: 10px;
            display: none;
        }}
        .hitl-alert-header {{
            font-size: 13px;
            font-weight: 700;
            color: #fbbf24;
            margin-bottom: 6px;
            display: flex;
            align-items: center;
            gap: 6px;
        }}
        .hitl-desc {{
            font-size: 12px;
            color: #fde68a;
            margin-bottom: 12px;
        }}
        .hitl-actions-row {{
            display: flex;
            gap: 10px;
        }}

        /* Payment Link Ready Box */
        .payment-link-box {{
            background: var(--green-subtle);
            border: 1px solid var(--green-border);
            border-radius: var(--radius-md);
            padding: 14px;
            margin-top: 10px;
            display: none;
        }}
        .payment-link-header {{
            font-size: 13px;
            font-weight: 700;
            color: #34d399;
            margin-bottom: 4px;
        }}
        .payment-link-desc {{
            font-size: 12px;
            color: #a7f3d0;
            margin-bottom: 10px;
        }}

        /* Secondary Row: BI Comparison & Copilot & Activity */
        .secondary-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr 1fr;
            gap: 20px;
        }}

        /* BI Strategy Table */
        table.bi-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 12px;
        }}
        table.bi-table th {{
            padding: 8px 10px;
            color: var(--text-muted);
            font-weight: 600;
            font-size: 11px;
            text-transform: uppercase;
            border-bottom: 1px solid var(--border-color);
            text-align: left;
        }}
        table.bi-table td {{
            padding: 8px 10px;
            border-bottom: 1px solid var(--border-color);
            font-family: 'JetBrains Mono', monospace;
        }}

        /* Activity Feed */
        .activity-stream {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 11.5px;
            max-height: 220px;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 6px;
        }}
        .activity-entry {{
            padding: 4px 6px;
            border-radius: var(--radius-sm);
            background: var(--bg-surface);
            border-left: 2px solid var(--blue-primary);
            line-height: 1.4;
        }}
        .activity-time {{ color: var(--text-muted); font-size: 10.5px; margin-right: 6px; }}

        /* Copilot Chat */
        .chat-box {{
            display: flex;
            flex-direction: column;
            height: 220px;
        }}
        .chat-messages {{
            flex: 1;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 8px;
            margin-bottom: 8px;
            padding-right: 4px;
        }}
        .chat-msg {{
            padding: 8px 10px;
            border-radius: var(--radius-md);
            font-size: 12px;
            line-height: 1.4;
        }}
        .chat-msg.bot {{
            background: var(--bg-surface);
            border: 1px solid var(--border-color);
            color: var(--text-primary);
        }}
        .chat-msg.user {{
            background: var(--blue-subtle);
            border: 1px solid var(--blue-border);
            color: #bfdbfe;
            align-self: flex-end;
            max-width: 85%;
        }}
        .chat-presets {{
            display: flex;
            gap: 6px;
            flex-wrap: wrap;
            margin-bottom: 8px;
        }}
        .preset-chip {{
            font-size: 11px;
            background: var(--bg-surface);
            border: 1px solid var(--border-light);
            color: var(--text-secondary);
            padding: 2px 8px;
            border-radius: 9999px;
            cursor: pointer;
        }}
        .preset-chip:hover {{
            background: var(--bg-surface-hover);
            color: var(--text-primary);
        }}
        .chat-input-row {{
            display: flex;
            gap: 6px;
        }}
        .chat-input {{
            flex: 1;
            background: var(--bg-surface);
            border: 1px solid var(--border-light);
            color: var(--text-primary);
            padding: 6px 10px;
            border-radius: var(--radius-md);
            font-size: 12.5px;
            font-family: inherit;
            outline: none;
        }}
        .chat-input:focus {{
            border-color: var(--border-focus);
        }}

        @media (max-width: 1200px) {{
            .workspace-grid {{ grid-template-columns: 1fr; }}
            .secondary-grid {{ grid-template-columns: 1fr; }}
            .kpi-grid {{ grid-template-columns: 1fr 1fr; }}
        }}
    </style>
</head>
<body>

    <!-- Enterprise Header -->
    <header class="app-header">
        <div class="brand-cluster">
            <div class="brand-logo">⬢</div>
            <div>
                <div class="brand-title">Project Phoenix Autonomous Recovery</div>
                <div class="brand-subtitle">Autonomous Revenue Recovery Command Center</div>
            </div>
        </div>
        <div class="header-meta">
            <div class="status-pill">
                <span class="status-dot"></span>
                <span>System Operational</span>
            </div>
            <div class="status-pill badge-test-mode">
                <span>Razorpay Test Mode</span>
            </div>
            <div class="status-pill badge-live-source" id="data-provenance-pill">
                <span>Telemetry: Initializing</span>
            </div>
        </div>
    </header>

    <!-- 4-Card KPI Strip -->
    <section class="kpi-grid">
        <div class="kpi-card">
            <div class="kpi-label">
                <span>Capital at Risk</span>
                <span class="badge-pill" style="font-size:9px; background:var(--bg-surface);">Target</span>
            </div>
            <div class="kpi-value" id="kpi-risk">₹2,99,900.00</div>
            <div class="kpi-subtext" id="kpi-risk-sub">Cumulative gross failed checkout volume</div>
        </div>

        <div class="kpi-card">
            <div class="kpi-label">
                <span>Recovered Revenue</span>
                <span class="badge-pill badge-recovered" style="font-size:9px;">Actual</span>
            </div>
            <div class="kpi-value text-green" id="kpi-recovered">₹2,45,918.00</div>
            <div class="kpi-subtext" id="kpi-recovered-sub">
                <span class="text-green" id="kpi-rate-highlight">82% Recovery Rate</span> vs baseline
            </div>
        </div>

        <div class="kpi-card">
            <div class="kpi-label">
                <span>Retries Avoided</span>
                <span class="badge-pill badge-awaiting" style="font-size:9px;">Efficiency</span>
            </div>
            <div class="kpi-value text-blue" id="kpi-avoided">14</div>
            <div class="kpi-subtext" id="kpi-savings">₹1,400.00 Action Costs Saved</div>
        </div>

        <div class="kpi-card">
            <div class="kpi-label">
                <span>Pending Review</span>
                <span class="badge-pill badge-escalated" style="font-size:9px;">HITL Queue</span>
            </div>
            <div class="kpi-value text-amber" id="kpi-pending">0</div>
            <div class="kpi-subtext">High-value & risk escalations</div>
        </div>
    </section>

    <!-- Scenario Simulator Control Strip -->
    <section class="simulator-bar">
        <div class="sim-controls">
            <span class="sim-label">Test Recovery Scenario:</span>
            <select id="scenario-selector" class="sim-select">
                <option value="otp_friction">OTP Friction (UPI Timeout / Cancellation)</option>
                <option value="insufficient_funds">Insufficient Funds (Liquidity / Balance)</option>
                <option value="bank_downtime">Bank Downtime (Issuer Gateway Error)</option>
                <option value="card_expired">Expired Instrument (Card Expired)</option>
                <option value="high_value">High Value ₹15,000 (Trigger POL-008 HITL)</option>
                <option value="fraud_security">Fraud / Security (Declined & Policy Blocked)</option>
            </select>
            <button class="btn btn-primary" id="btn-run-scenario" onclick="runSelectedScenario()">
                Run Scenario
            </button>
            <button class="btn btn-secondary" onclick="openRazorpayCheckoutModal()">
                Open Test Checkout Modal
            </button>
        </div>
        <div class="sim-status-ticker" id="sim-ticker">
            <span class="status-dot"></span> Ready for event stream
        </div>
    </section>

    <!-- Main Workspace Grid -->
    <main class="workspace-grid">
        <!-- Left: Recovery Cases Operational Ledger -->
        <section class="op-card">
            <div class="op-card-header">
                <div class="op-card-title">
                    <span>Recovery Cases</span>
                    <span class="badge-pill badge-detected" id="cases-count-badge">0 cases</span>
                </div>
                <div style="display: flex; gap: 6px;">
                    <button class="btn btn-secondary" style="padding: 4px 8px; font-size: 11px;" onclick="loadCasesTable()">
                        ↻ Refresh
                    </button>
                </div>
            </div>
            <div class="cases-table-wrap">
                <table class="cases-table">
                    <thead>
                        <tr>
                            <th>Transaction</th>
                            <th style="text-align: right;">Amount</th>
                            <th>Failure Reason</th>
                            <th style="text-align: right;">Confidence</th>
                            <th style="text-align: right;">ENR</th>
                            <th>Status</th>
                        </tr>
                    </thead>
                    <tbody id="cases-table-body">
                        <tr>
                            <td colspan="6" style="text-align:center; padding: 24px; color: var(--text-muted);">
                                Loading recovery cases telemetry...
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </section>

        <!-- Right: Active Recovery Decision & Pipeline Panel -->
        <section class="op-card" id="active-panel">
            <div class="op-card-header">
                <div class="op-card-title">
                    <span>Active Case Inspection</span>
                    <span id="active-case-id-badge" class="badge-pill badge-detected mono-cell">—</span>
                </div>
                <div id="active-status-pill" class="badge-pill badge-detected">NO SELECTION</div>
            </div>

            <div class="op-card-body" id="detail-panel-body">
                <div id="empty-selection-msg" style="text-align: center; padding: 40px 10px; color: var(--text-muted);">
                    Select a case from the table or run a failure scenario above to inspect autonomous decisions.
                </div>

                <div id="case-detail-content" style="display: none;">
                    <div class="detail-header-strip">
                        <div>
                            <div class="detail-amount-large" id="detail-amount">₹0.00</div>
                            <div class="detail-failure-badge" id="detail-failure-reason">Reason: —</div>
                        </div>
                        <div style="text-align: right;">
                            <div style="font-size: 11px; color: var(--text-muted);">Customer Contact</div>
                            <div class="mono-cell" style="font-size: 12px;" id="detail-customer-phone">—</div>
                        </div>
                    </div>

                    <!-- 6-Node Pipeline -->
                    <div class="pipeline-container">
                        <div class="pipe-node" id="step-detected">01 DETECT</div>
                        <div class="pipe-node" id="step-diagnosed">02 DIAGNOSE</div>
                        <div class="pipe-node" id="step-economics">03 ENR</div>
                        <div class="pipe-node" id="step-policy">04 POLICY</div>
                        <div class="pipe-node" id="step-executed">05 EXECUTE</div>
                        <div class="pipe-node" id="step-recovered">06 RECOVER</div>
                    </div>

                    <!-- Decision Breakdown Sections -->
                    <div class="decision-sections">
                        <!-- AI Assessment -->
                        <div class="section-box">
                            <div class="section-box-title">
                                <span>AI Diagnostic Assessment</span>
                                <span class="text-blue" id="diag-model-label">Fail-Closed AI</span>
                            </div>
                            <div class="section-grid-2">
                                <div>
                                    <div class="stat-row">
                                        <span class="stat-label">Root Cause:</span>
                                        <span class="stat-val" id="diag-root-cause">—</span>
                                    </div>
                                    <div class="stat-row">
                                        <span class="stat-label">AI Confidence:</span>
                                        <span class="stat-val text-green" id="diag-confidence">—</span>
                                    </div>
                                </div>
                                <div>
                                    <div class="stat-row">
                                        <span class="stat-label">Recommended:</span>
                                        <span class="stat-val text-blue" id="diag-rec-action">—</span>
                                    </div>
                                    <div class="stat-row">
                                        <span class="stat-label">Urgency:</span>
                                        <span class="stat-val" id="diag-urgency">—</span>
                                    </div>
                                </div>
                            </div>
                            <div style="font-size: 11.5px; color: var(--text-secondary); margin-top: 6px;" id="diag-summary">
                                Diagnosis summary: —
                            </div>
                        </div>

                        <!-- Economic Evaluation -->
                        <div class="section-box">
                            <div class="section-box-title">
                                <span>Economic Decisioning</span>
                                <span class="text-green">Mathematical Gate</span>
                            </div>
                            <div class="section-grid-2">
                                <div>
                                    <div class="stat-row">
                                        <span class="stat-label">Recoverable Amount:</span>
                                        <span class="stat-val" id="econ-amount">—</span>
                                    </div>
                                    <div class="stat-row">
                                        <span class="stat-label">Action Cost:</span>
                                        <span class="stat-val" id="econ-cost">₹100.00</span>
                                    </div>
                                </div>
                                <div>
                                    <div class="stat-row">
                                        <span class="stat-label">Expected Net Recovery:</span>
                                        <span class="stat-val text-green" id="econ-enr">—</span>
                                    </div>
                                    <div class="stat-row">
                                        <span class="stat-label">Decision:</span>
                                        <span class="stat-val text-green" id="econ-decision">VIABLE</span>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <!-- Policy Gate -->
                        <div class="section-box">
                            <div class="section-box-title">
                                <span>Policy Engine Gate</span>
                                <span id="policy-verdict-badge" class="badge-pill badge-awaiting">EVALUATING</span>
                            </div>
                            <div style="font-size: 11.5px; color: var(--text-secondary);" id="policy-checks-list">
                                ✓ Retry Limit Check (POL-006) • ✓ Failure Eligibility (POL-002) • ✓ Economic Viability (POL-004)
                            </div>
                        </div>
                    </div>

                    <!-- Human-in-the-Loop Risk Review Alert -->
                    <div class="hitl-alert-box" id="hitl-panel">
                        <div class="hitl-alert-header">
                            <span>⚠️ Human Review Required</span>
                            <span class="badge-pill badge-escalated" style="font-size: 10px;">POL-008</span>
                        </div>
                        <div class="hitl-desc">
                            This transaction exceeds automated recovery limits (₹10,000 threshold) or flagged high risk. Merchant authorization required to dispatch payment recovery.
                        </div>
                        <div class="hitl-actions-row">
                            <button class="btn btn-danger-outline" onclick="rejectActiveCase()">
                                Reject Recovery
                            </button>
                            <button class="btn btn-primary" onclick="approveActiveCase()">
                                Approve & Dispatch Link
                            </button>
                        </div>
                    </div>

                    <!-- Payment Link Execution Box -->
                    <div class="payment-link-box" id="payment-link-panel">
                        <div class="payment-link-header">
                            ✓ Razorpay Recovery Payment Link Dispatched
                        </div>
                        <div class="payment-link-desc" id="payment-link-url-text">
                            Payment link issued via Razorpay Test Mode. Customer can complete payment using any alternate payment instrument.
                        </div>
                        <a href="#" target="_blank" class="btn btn-success" id="btn-pay-link">
                            Open Test Payment Link ↗
                        </a>
                    </div>

                </div>
            </div>
        </section>
    </main>

    <!-- Secondary Row: Strategy BI, Activity Feed, Copilot -->
    <section class="secondary-grid">
        <!-- Business Intelligence Comparison -->
        <div class="op-card">
            <div class="op-card-header">
                <div class="op-card-title">
                    <span>Strategy Benchmark</span>
                </div>
                <span class="badge-pill" style="font-size: 10px; background: var(--bg-surface);" id="bi-provenance-tag">
                    Benchmark Simulation
                </span>
            </div>
            <div class="op-card-body" style="padding: 10px 14px;">
                <table class="bi-table">
                    <thead>
                        <tr>
                            <th>Metric</th>
                            <th style="text-align: right;">Naive</th>
                            <th style="text-align: right;">Phoenix</th>
                            <th style="text-align: right;">Lift</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td style="color: var(--text-secondary); font-family: 'Inter', sans-serif;">Recovery Rate</td>
                            <td style="text-align: right;">32%</td>
                            <td style="text-align: right; color: var(--green-accent);" id="bi-phx-rate">82%</td>
                            <td style="text-align: right; color: var(--green-accent);" id="bi-lift-rate">+50 pp</td>
                        </tr>
                        <tr>
                            <td style="color: var(--text-secondary); font-family: 'Inter', sans-serif;">Recovered Amount</td>
                            <td style="text-align: right;">₹95,968</td>
                            <td style="text-align: right; color: var(--green-accent);" id="bi-phx-rev">₹2,45,918</td>
                            <td style="text-align: right; color: var(--green-accent);" id="bi-lift-rev">+₹1,49,950</td>
                        </tr>
                        <tr>
                            <td style="color: var(--text-secondary); font-family: 'Inter', sans-serif;">Unnecessary Retries</td>
                            <td style="text-align: right; color: var(--red-accent);">18</td>
                            <td style="text-align: right; color: var(--green-accent);">4</td>
                            <td style="text-align: right; color: var(--green-accent);">-14 avoided</td>
                        </tr>
                        <tr>
                            <td style="color: var(--text-secondary); font-family: 'Inter', sans-serif;">Cost of Actions</td>
                            <td style="text-align: right;">₹1,800</td>
                            <td style="text-align: right; color: var(--green-accent);">₹400</td>
                            <td style="text-align: right; color: var(--green-accent);">₹1,400 saved</td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>

        <!-- Activity Ledger Feed -->
        <div class="op-card">
            <div class="op-card-header">
                <div class="op-card-title">
                    <span>Operational Activity Feed</span>
                </div>
                <span class="status-dot" style="background-color: var(--blue-primary);"></span>
            </div>
            <div class="op-card-body" style="padding: 10px 14px;">
                <div class="activity-stream" id="activity-stream">
                    <div class="activity-entry">
                        <span class="activity-time">[14:00:00]</span> Phoenix autonomous orchestrator initialized.
                    </div>
                </div>
            </div>
        </div>

        <!-- Merchant Decision Copilot -->
        <div class="op-card">
            <div class="op-card-header">
                <div class="op-card-title">
                    <span>Phoenix Merchant Copilot</span>
                </div>
                <span class="badge-pill badge-awaiting" style="font-size: 10px;">Grounded AI</span>
            </div>
            <div class="op-card-body" style="padding: 10px 14px;">
                <div class="chat-box">
                    <div class="chat-messages" id="chat-messages">
                        <div class="chat-msg bot">
                            👋 <b>Phoenix Copilot:</b> Grounded in live database telemetry. Ask about recovered revenue, failure root causes, or pending risk reviews.
                        </div>
                    </div>
                    <div class="chat-presets">
                        <span class="preset-chip" onclick="askCopilot('How much revenue did Phoenix recover?')">Recovered revenue?</span>
                        <span class="preset-chip" onclick="askCopilot('Show high value cases needing review')">Pending reviews?</span>
                        <span class="preset-chip" onclick="askCopilot('What is our recovery ROI vs baseline?')">Compare ROI</span>
                    </div>
                    <div class="chat-input-row">
                        <input type="text" id="copilot-input" class="chat-input" placeholder="Ask merchant query..." onkeydown="if(event.key==='Enter') sendCopilotMsg()">
                        <button class="btn btn-primary" style="padding: 4px 10px; font-size: 12px;" onclick="sendCopilotMsg()">Send</button>
                    </div>
                </div>
            </div>
        </div>
    </section>

    <script>
        const RZP_KEY_ID = "{key_id}";
        let currentActiveCaseId = null;
        let pollTimer = null;

        // Defensive fetch wrapper that never throws Unexpected token errors on 500 or non-JSON responses
        async function safeFetch(url, options = {{}}) {{
            try {{
                const res = await fetch(url, options);
                const contentType = res.headers.get("content-type") || "";
                let data = null;
                if (contentType.includes("application/json")) {{
                    data = await res.json();
                }} else {{
                    const text = await res.text();
                    data = {{ detail: text }};
                }}
                return {{ ok: res.ok, status: res.status, data: data }};
            }} catch (err) {{
                console.error("Network / SafeFetch error:", err);
                return {{ ok: false, status: 0, data: {{ detail: err.message }} }};
            }}
        }}

        function logActivity(message, level = "info") {{
            const stream = document.getElementById("activity-stream");
            const time = new Date().toLocaleTimeString();
            const entry = document.createElement("div");
            entry.className = "activity-entry";
            if (level === "warn") entry.style.borderLeftColor = "var(--amber-accent)";
            else if (level === "error") entry.style.borderLeftColor = "var(--red-accent)";
            else if (level === "success") entry.style.borderLeftColor = "var(--green-accent)";
            entry.innerHTML = `<span class="activity-time">[${{time}}]</span> ${{message}}`;
            stream.appendChild(entry);
            stream.scrollTop = stream.scrollHeight;
        }}

        function setTicker(text, isError = false) {{
            const ticker = document.getElementById("sim-ticker");
            const dot = ticker.querySelector(".status-dot") || document.createElement("span");
            dot.className = "status-dot";
            dot.style.backgroundColor = isError ? "var(--red-accent)" : "var(--green-accent)";
            ticker.innerHTML = "";
            ticker.appendChild(dot);
            ticker.appendChild(document.createTextNode(" " + text));
        }}

        async function runSelectedScenario() {{
            const scenario = document.getElementById("scenario-selector").value;
            const btn = document.getElementById("btn-run-scenario");
            btn.disabled = true;
            btn.innerText = "Running...";
            setTicker(`Processing scenario '${{scenario}}'...`);
            logActivity(`⚡ Simulating failure event: ${{scenario}}...`, "warn");

            const res = await safeFetch("/api/v1/checkout/simulate-failure", {{
                method: "POST",
                headers: {{ "Content-Type": "application/json" }},
                body: JSON.stringify({{ scenario: scenario }})
            }});

            btn.disabled = false;
            btn.innerText = "Run Scenario";

            if (res.ok && res.data && res.data.success) {{
                setTicker(`✓ Scenario executed: ${{scenario}}`);
                logActivity(`✓ Event processed! Case ID: ${{res.data.case_id ? res.data.case_id.slice(0,8) : 'N/A'}} (${{res.data.payment_id}})`, "success");
                logActivity(`Case status: ${{res.data.case_status}} | Reason: ${{res.data.failure_reason}}`, "info");
                currentActiveCaseId = res.data.case_id;
                await loadCasesTable();
                if (currentActiveCaseId) {{
                    await inspectCase(currentActiveCaseId);
                }}
                await refreshAnalytics();
            }} else {{
                const errMsg = res.data?.error?.message || res.data?.detail || "Recovery simulation failed.";
                setTicker(`✕ Simulation failed`, true);
                logActivity(`Simulation error: ${{errMsg}}`, "error");
            }}
        }}

        async function loadCasesTable() {{
            const res = await safeFetch("/api/v1/recovery-cases?page_size=30");
            if (!res.ok || !res.data) return;

            const items = res.data.items || [];
            document.getElementById("cases-count-badge").innerText = `${{res.data.total || items.length}} cases`;

            const tbody = document.getElementById("cases-table-body");
            if (items.length === 0) {{
                tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding: 24px; color: var(--text-muted);">No recovery cases recorded yet. Run a scenario above.</td></tr>`;
                return;
            }}

            tbody.innerHTML = items.map(c => {{
                const isSelected = c.id === currentActiveCaseId ? "selected" : "";
                let statusBadgeClass = "badge-detected";
                if (c.status === "AWAITING_PAYMENT") statusBadgeClass = "badge-awaiting";
                else if (c.status === "ESCALATED") statusBadgeClass = "badge-escalated";
                else if (c.status === "RECOVERED") statusBadgeClass = "badge-recovered";
                else if (c.status === "POLICY_REJECTED") statusBadgeClass = "badge-rejected";

                const amountInr = (c.amount / 100).toLocaleString('en-IN', {{ minimumFractionDigits: 2 }});
                const shortId = c.payment_id.length > 14 ? c.payment_id.slice(0, 14) + '...' : c.payment_id;

                return `<tr class="${{isSelected}}" onclick="selectCaseRow('${{c.id}}')">
                    <td class="mono-cell" title="${{c.payment_id}}"><b>${{shortId}}</b></td>
                    <td style="text-align: right;" class="mono-cell">₹${{amountInr}}</td>
                    <td>${{c.failure_reason || 'Unknown'}}</td>
                    <td style="text-align: right;" class="mono-cell text-green">${{c.ai_diagnosis ? (c.ai_diagnosis.confidence_score * 100).toFixed(0) + '%' : '—'}}</td>
                    <td style="text-align: right;" class="mono-cell text-blue">₹${{amountInr}}</td>
                    <td><span class="badge-pill ${{statusBadgeClass}}">${{c.status}}</span></td>
                </tr>`;
            }}).join("");

            // Auto-select first if none selected
            if (!currentActiveCaseId && items.length > 0) {{
                selectCaseRow(items[0].id);
            }}
        }}

        function selectCaseRow(caseId) {{
            currentActiveCaseId = caseId;
            // Highlight in table
            const rows = document.querySelectorAll("#cases-table-body tr");
            rows.forEach(r => r.classList.remove("selected"));
            inspectCase(caseId);
        }}

        async function inspectCase(caseId) {{
            if (!caseId) return;
            const res = await safeFetch(`/api/v1/recovery-cases/${{caseId}}`);
            if (!res.ok || !res.data) return;

            const c = res.data;
            document.getElementById("empty-selection-msg").style.display = "none";
            document.getElementById("case-detail-content").style.display = "block";

            document.getElementById("active-case-id-badge").innerText = c.id.slice(0, 8);
            
            // Header stats
            const amountInr = (c.amount / 100).toLocaleString('en-IN', {{ minimumFractionDigits: 2 }});
            document.getElementById("detail-amount").innerText = `₹${{amountInr}}`;
            document.getElementById("detail-failure-reason").innerText = `Failure: ${{c.failure_reason || c.failure_code}}`;
            document.getElementById("detail-customer-phone").innerText = c.customer_phone || c.customer_email || '—';

            // Status Badge
            const statusPill = document.getElementById("active-status-pill");
            statusPill.innerText = c.status;
            statusPill.className = "badge-pill";
            if (c.status === "AWAITING_PAYMENT") statusPill.classList.add("badge-awaiting");
            else if (c.status === "ESCALATED") statusPill.classList.add("badge-escalated");
            else if (c.status === "RECOVERED") statusPill.classList.add("badge-recovered");
            else if (c.status === "POLICY_REJECTED") statusPill.classList.add("badge-rejected");
            else statusPill.classList.add("badge-detected");

            // Update 6-node Pipeline
            resetPipeline();
            document.getElementById("step-detected").className = "pipe-node done";

            if (c.ai_diagnosis) {{
                document.getElementById("step-diagnosed").className = "pipe-node done";
                document.getElementById("step-economics").className = "pipe-node done";

                const diag = c.ai_diagnosis;
                document.getElementById("diag-root-cause").innerText = diag.root_cause_category;
                document.getElementById("diag-confidence").innerText = `${{(diag.confidence_score * 100).toFixed(0)}}%`;
                document.getElementById("diag-rec-action").innerText = diag.recommended_action || "PAYMENT_LINK";
                document.getElementById("diag-urgency").innerText = diag.urgency || "MEDIUM";
                document.getElementById("diag-summary").innerText = `Summary: ${{diag.diagnostic_summary}}`;

                // Economics
                document.getElementById("econ-amount").innerText = `₹${{amountInr}}`;
                const enrPaise = diag.raw_response?.economic_evaluation?.expected_net_recovery_paise;
                if (enrPaise) {{
                    document.getElementById("econ-enr").innerText = `₹${{(enrPaise / 100).toLocaleString('en-IN', {{minimumFractionDigits: 2}})}}`;
                }} else {{
                    document.getElementById("econ-enr").innerText = `₹${{amountInr}}`;
                }}
            }}

            // Policy & Execution steps
            const policyVerdict = document.getElementById("policy-verdict-badge");
            if (c.status === "ESCALATED") {{
                document.getElementById("step-policy").className = "pipe-node escalated";
                policyVerdict.className = "badge-pill badge-escalated";
                policyVerdict.innerText = "POL-008 ESCALATED";
                document.getElementById("policy-checks-list").innerText = "⚠️ High-Value Limit Threshold Exceeded (POL-008). Automation gated for Human Review.";
            }} else if (c.status === "POLICY_REJECTED") {{
                document.getElementById("step-policy").className = "pipe-node rejected";
                policyVerdict.className = "badge-pill badge-rejected";
                policyVerdict.innerText = "POLICY REJECTED";
                document.getElementById("policy-checks-list").innerText = "✕ Case declined by Policy Engine (POL-003 / Fraud Protection).";
            }} else if (c.status === "POLICY_APPROVED" || c.status === "AWAITING_PAYMENT" || c.status === "RECOVERED") {{
                document.getElementById("step-policy").className = "pipe-node done";
                policyVerdict.className = "badge-pill badge-recovered";
                policyVerdict.innerText = "APPROVED";
                document.getElementById("policy-checks-list").innerText = "✓ All 8 Policy Checks Passed (Limits, Cooldown, Eligibility, Economics).";
            }}

            if (c.status === "AWAITING_PAYMENT" || c.status === "RECOVERED") {{
                document.getElementById("step-executed").className = "pipe-node done";
            }}
            if (c.status === "RECOVERED") {{
                document.getElementById("step-recovered").className = "pipe-node done";
            }}

            // HITL Box Visibility
            const hitlBox = document.getElementById("hitl-panel");
            if (c.status === "ESCALATED") {{
                hitlBox.style.display = "block";
            }} else {{
                hitlBox.style.display = "none";
            }}

            // Payment Link Visibility
            const linkBox = document.getElementById("payment-link-panel");
            const actions = c.recovery_actions || [];
            const linkAction = actions.find(a => a.payment_link_url);
            if (linkAction && linkAction.payment_link_url) {{
                linkBox.style.display = "block";
                document.getElementById("btn-pay-link").href = linkAction.payment_link_url;
                document.getElementById("payment-link-url-text").innerText = `Active Razorpay Recovery URL: ${{linkAction.payment_link_url}}`;
            }} else {{
                linkBox.style.display = "none";
            }}
        }}

        function resetPipeline() {{
            ["step-detected", "step-diagnosed", "step-economics", "step-policy", "step-executed", "step-recovered"].forEach(id => {{
                document.getElementById(id).className = "pipe-node";
            }});
        }}

        async function approveActiveCase() {{
            if (!currentActiveCaseId) return;
            logActivity(`Merchant authorized Case ${{currentActiveCaseId.slice(0,8)}} via HITL...`, "info");
            const res = await safeFetch(`/api/v1/recovery-cases/${{currentActiveCaseId}}/approve`, {{
                method: "POST",
                headers: {{ "Content-Type": "application/json" }},
                body: JSON.stringify({{ reason: "Merchant operator approved via Command Center HITL interface" }})
            }});
            if (res.ok) {{
                logActivity(`✓ Case ${{currentActiveCaseId.slice(0,8)}} approved and Razorpay Payment Link dispatched!`, "success");
                await inspectCase(currentActiveCaseId);
                await loadCasesTable();
                await refreshAnalytics();
            }} else {{
                logActivity(`Approval failed: ${{res.data?.detail || 'Error'}}`, "error");
            }}
        }}

        async function rejectActiveCase() {{
            if (!currentActiveCaseId) return;
            logActivity(`Merchant rejected Case ${{currentActiveCaseId.slice(0,8)}} via HITL...`, "warn");
            const res = await safeFetch(`/api/v1/recovery-cases/${{currentActiveCaseId}}/reject`, {{
                method: "POST",
                headers: {{ "Content-Type": "application/json" }},
                body: JSON.stringify({{ reason: "Merchant operator declined recovery" }})
            }});
            if (res.ok) {{
                logActivity(`✓ Case ${{currentActiveCaseId.slice(0,8)}} halted and marked CANCELLED.`, "info");
                await inspectCase(currentActiveCaseId);
                await loadCasesTable();
                await refreshAnalytics();
            }} else {{
                logActivity(`Rejection failed: ${{res.data?.detail || 'Error'}}`, "error");
            }}
        }}

        async function refreshAnalytics() {{
            const res = await safeFetch("/api/v1/analytics/comparison");
            if (!res.ok || !res.data) return;

            const phx = res.data.phoenix_ai;
            const comp = res.data.comparison;

            document.getElementById("kpi-risk").innerText = `₹${{phx.amount_at_risk_inr.toLocaleString('en-IN', {{minimumFractionDigits: 2}})}}`;
            document.getElementById("kpi-recovered").innerText = `₹${{phx.recovered_amount_inr.toLocaleString('en-IN', {{minimumFractionDigits: 2}})}}`;
            document.getElementById("kpi-avoided").innerText = comp.retries_avoided_count;
            document.getElementById("kpi-savings").innerText = `₹${{comp.cost_saved_inr.toLocaleString('en-IN', {{minimumFractionDigits: 2}})}} Action Costs Saved`;

            const provPill = document.getElementById("data-provenance-pill");
            const biTag = document.getElementById("bi-provenance-tag");

            if (comp.is_live_data) {{
                provPill.innerHTML = `<span>● Live Telemetry</span>`;
                provPill.style.color = "var(--green-accent)";
                biTag.innerText = "Live Telemetry";
                document.getElementById("kpi-rate-highlight").innerText = `${{phx.recovery_rate_pct}}% Recovery Rate (Live)`;
                document.getElementById("bi-lift-rate").innerText = `+${{comp.revenue_lift_pct}}% (Live)`;
            }} else {{
                provPill.innerHTML = `<span>Estimated • Benchmark Simulation</span>`;
                provPill.style.color = "#93c5fd";
                biTag.innerText = "Benchmark Simulation — Estimated";
                document.getElementById("kpi-rate-highlight").innerText = `${{phx.recovery_rate_pct}}% Recovery Rate (Simulated)`;
                document.getElementById("bi-lift-rate").innerText = `+${{comp.revenue_lift_pct}}% (Estimated)`;
            }}

            document.getElementById("bi-phx-rate").innerText = `${{phx.recovery_rate_pct}}%`;
            document.getElementById("bi-phx-rev").innerText = `₹${{phx.recovered_amount_inr.toLocaleString('en-IN')}}`;
            document.getElementById("bi-lift-rev").innerText = `+₹${{comp.revenue_lift_inr.toLocaleString('en-IN')}}`;

            // HITL pending count
            const hitlRes = await safeFetch("/api/v1/recovery-cases?status=ESCALATED&page_size=1");
            if (hitlRes.ok && hitlRes.data) {{
                document.getElementById("kpi-pending").innerText = hitlRes.data.total || 0;
            }}
        }}

        async function askCopilot(query) {{
            document.getElementById("copilot-input").value = query;
            await sendCopilotMsg();
        }}

        async function sendCopilotMsg() {{
            const input = document.getElementById("copilot-input");
            const text = input.value.trim();
            if (!text) return;

            const chat = document.getElementById("chat-messages");
            const userDiv = document.createElement("div");
            userDiv.className = "chat-msg user";
            userDiv.innerText = text;
            chat.appendChild(userDiv);
            input.value = "";
            chat.scrollTop = chat.scrollHeight;

            const res = await safeFetch("/api/v1/copilot/query", {{
                method: "POST",
                headers: {{ "Content-Type": "application/json" }},
                body: JSON.stringify({{ query: text }})
            }});

            const botDiv = document.createElement("div");
            botDiv.className = "chat-msg bot";
            if (res.ok && res.data) {{
                botDiv.innerHTML = res.data.reply_text.replace(/\\n/g, "<br>");
            }} else {{
                botDiv.innerText = "Copilot query could not be completed. Check telemetry status.";
            }}
            chat.appendChild(botDiv);
            chat.scrollTop = chat.scrollHeight;
        }}

        function openRazorpayCheckoutModal() {{
            logActivity("Initializing Razorpay Test Order...", "info");
            safeFetch("/api/v1/checkout/create-order", {{
                method: "POST",
                headers: {{ "Content-Type": "application/json" }},
                body: JSON.stringify({{ amount: 299900 }})
            }}).then(res => {{
                if (!res.ok || !res.data) {{
                    logActivity(`Failed to create Razorpay Order: ${{res.data?.detail || 'Gateway error'}}`, "error");
                    return;
                }}
                const data = res.data;
                const options = {{
                    key: data.key_id,
                    amount: data.amount,
                    currency: data.currency,
                    name: "Project Phoenix Demo",
                    description: "Test Mode Live Recovery Checkout",
                    order_id: data.order_id,
                    handler: function(resp) {{
                        logActivity(`Payment captured: ${{resp.razorpay_payment_id}}`, "success");
                    }},
                    modal: {{
                        ondismiss: function() {{
                            logActivity("Checkout modal closed. Polling for recovery cases...", "warn");
                            startPolling();
                        }}
                    }}
                }};
                const rzp = new Razorpay(options);
                rzp.on('payment.failed', function(resp) {{
                    logActivity(`Razorpay payment.failed reported: ${{resp.error.code}} - ${{resp.error.description}}`, "error");
                    startPolling();
                }});
                rzp.open();
            }});
        }}

        function startPolling() {{
            if (pollTimer) clearInterval(pollTimer);
            pollTimer = setInterval(async () => {{
                await loadCasesTable();
                await refreshAnalytics();
            }}, 3000);
        }}

        // Initialization
        (async function initDashboard() {{
            await refreshAnalytics();
            await loadCasesTable();
            startPolling();
        }})();
    </script>
</body>
</html>"""
    return HTMLResponse(content=html_content)
