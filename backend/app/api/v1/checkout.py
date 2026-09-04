"""Checkout and test mode verification endpoints."""

import logging
from typing import Any
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.services.razorpay.client import RazorpayAPIError, RazorpayClient

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
    finally:
        await client.close()


@router.get("/portal", response_model=None, response_class=HTMLResponse)
async def get_test_checkout_portal(
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    """Interactive Razorpay Test Mode E2E Portal for live checkout demonstration."""
    key_id = settings.razorpay_key_id
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Project Phoenix — Razorpay Test Mode E2E Portal</title>
    <script src="https://checkout.razorpay.com/v1/checkout.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-primary: #0a0e17;
            --bg-card: #121826;
            --bg-surface: #1a2234;
            --primary: #3b82f6;
            --primary-glow: rgba(59, 130, 246, 0.4);
            --accent-green: #10b981;
            --accent-amber: #f59e0b;
            --accent-red: #ef4444;
            --text-main: #f3f4f6;
            --text-muted: #9ca3af;
            --border-col: #27354f;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background: radial-gradient(circle at top right, #172554, var(--bg-primary) 60%);
            color: var(--text-main);
            font-family: 'Outfit', sans-serif;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
            padding: 30px 20px;
        }}
        .header {{
            text-align: center;
            margin-bottom: 25px;
        }}
        .badge {{
            display: inline-block;
            padding: 6px 14px;
            background: rgba(59, 130, 246, 0.15);
            border: 1px solid var(--primary);
            color: #93c5fd;
            border-radius: 9999px;
            font-size: 0.85rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 12px;
        }}
        h1 {{
            font-size: 2.2rem;
            font-weight: 800;
            background: linear-gradient(135deg, #ffffff 0%, #93c5fd 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 6px;
        }}
        p.subtitle {{
            color: var(--text-muted);
            font-size: 1rem;
        }}
        .grid {{
            display: grid;
            grid-template-columns: 1fr 1.2fr;
            gap: 24px;
            width: 100%;
            max-width: 1100px;
        }}
        .card {{
            background: var(--bg-card);
            border: 1px solid var(--border-col);
            border-radius: 16px;
            padding: 24px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.5);
        }}
        .card-title {{
            font-size: 1.25rem;
            font-weight: 700;
            margin-bottom: 16px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .form-group {{
            margin-bottom: 14px;
        }}
        label {{
            display: block;
            font-size: 0.85rem;
            font-weight: 600;
            color: var(--text-muted);
            margin-bottom: 6px;
            text-transform: uppercase;
        }}
        input, select {{
            width: 100%;
            padding: 10px 14px;
            background: var(--bg-surface);
            border: 1px solid var(--border-col);
            border-radius: 8px;
            color: #fff;
            font-family: inherit;
            font-size: 0.95rem;
        }}
        .btn {{
            width: 100%;
            padding: 14px;
            border-radius: 10px;
            font-size: 1rem;
            font-weight: 700;
            cursor: pointer;
            border: none;
            transition: all 0.2s ease;
            margin-top: 10px;
        }}
        .btn-fail {{
            background: linear-gradient(135deg, #ef4444, #b91c1c);
            color: white;
            box-shadow: 0 4px 15px rgba(239, 68, 68, 0.4);
        }}
        .btn-fail:hover {{
            opacity: 0.95;
            transform: translateY(-1px);
        }}
        .btn-recover {{
            background: linear-gradient(135deg, #10b981, #047857);
            color: white;
            box-shadow: 0 4px 15px rgba(16, 185, 129, 0.4);
            margin-top: 15px;
            display: none;
        }}
        .btn-recover:hover {{
            opacity: 0.95;
            transform: translateY(-1px);
        }}
        .terminal {{
            background: #060911;
            border: 1px solid var(--border-col);
            border-radius: 12px;
            padding: 16px;
            height: 380px;
            overflow-y: auto;
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.82rem;
            color: #a7f3d0;
        }}
        .log-entry {{
            margin-bottom: 8px;
            line-height: 1.4;
        }}
        .log-ts {{ color: var(--text-muted); }}
        .log-info {{ color: #60a5fa; }}
        .log-warn {{ color: #fbbf24; }}
        .log-success {{ color: #34d399; }}
        .log-error {{ color: #f87171; }}
        .status-tracker {{
            margin-top: 16px;
            padding: 14px;
            background: var(--bg-surface);
            border-radius: 10px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .status-pill {{
            padding: 4px 12px;
            border-radius: 9999px;
            font-weight: 700;
            font-size: 0.85rem;
        }}
        .status-idle {{ background: #374151; color: #d1d5db; }}
        .status-detected {{ background: #92400e; color: #fef3c7; }}
        .status-diagnosing {{ background: #1e40af; color: #dbeafe; }}
        .status-awaiting {{ background: #b45309; color: #fef3c7; }}
        .status-recovered {{ background: #065f46; color: #d1fae5; }}
    </style>
</head>
<body>
    <div class="header">
        <div class="badge">Razorpay Test Mode E2E Verification</div>
        <h1>Project Phoenix Autonomous Recovery</h1>
        <p class="subtitle">Autonomous AI Revenue Recovery Pipeline in Real-Time</p>
    </div>

    <div class="grid">
        <!-- Action Card -->
        <div class="card">
            <div class="card-title">1. Trigger Real Checkout Failure</div>
            <div class="form-group">
                <label>Order Amount (Paise)</label>
                <input type="number" id="inp-amount" value="299900" />
            </div>
            <div class="form-group">
                <label>Customer Name</label>
                <input type="text" id="inp-name" value="Aditya Sharma" />
            </div>
            <div class="form-group">
                <label>Customer Email</label>
                <input type="email" id="inp-email" value="aditya.sharma@example.com" />
            </div>
            <div class="form-group">
                <label>Customer Phone</label>
                <input type="text" id="inp-phone" value="+919876543210" />
            </div>

            <button class="btn btn-fail" id="btn-checkout" onclick="startTestCheckout()">
                ⚡ Open Razorpay Test Checkout (Trigger Failure)
            </button>

            <a id="btn-pay-link" class="btn btn-recover" target="_blank" href="#">
                🎉 Pay Generated Phoenix Recovery Link
            </a>

            <div class="status-tracker">
                <div>
                    <div style="font-size:0.75rem; color:var(--text-muted); font-weight:600;">ACTIVE CASE STATUS</div>
                    <div id="case-id-display" style="font-size:0.85rem; font-family:'JetBrains Mono'; color:#93c5fd;">No Active Case</div>
                </div>
                <div id="status-badge" class="status-pill status-idle">IDLE</div>
            </div>
        </div>

        <!-- Terminal & Observability -->
        <div class="card">
            <div class="card-title">2. Autonomous Pipeline Observability</div>
            <div class="terminal" id="terminal">
                <div class="log-entry"><span class="log-ts">[00:00:00]</span> <span class="log-info">System initialized in Razorpay Test Mode.</span></div>
                <div class="log-entry"><span class="log-ts">[00:00:00]</span> <span class="log-info">Ready to process live webhooks & autonomous orchestration.</span></div>
            </div>
        </div>
    </div>

    <script>
        const KEY_ID = "{key_id}";
        let currentOrderId = null;
        let pollInterval = null;

        function log(msg, type = "info") {{
            const term = document.getElementById("terminal");
            const d = new Date();
            const ts = d.toTimeString().split(" ")[0];
            const entry = document.createElement("div");
            entry.className = "log-entry";
            entry.innerHTML = `<span class="log-ts">[${{ts}}]</span> <span class="log-${{type}}">${{msg}}</span>`;
            term.appendChild(entry);
            term.scrollTop = term.scrollHeight;
        }}

        function setStatus(statusText, styleClass) {{
            const badge = document.getElementById("status-badge");
            badge.innerText = statusText;
            badge.className = "status-pill " + styleClass;
        }}

        async function startTestCheckout() {{
            const amount = parseInt(document.getElementById("inp-amount").value);
            const name = document.getElementById("inp-name").value;
            const email = document.getElementById("inp-email").value;
            const phone = document.getElementById("inp-phone").value;

            log(`Creating Razorpay Test Mode Order for ₹${{(amount/100).toFixed(2)}}...`, "info");
            
            try {{
                const res = await fetch("/api/v1/checkout/create-order", {{
                    method: "POST",
                    headers: {{ "Content-Type": "application/json" }},
                    body: JSON.stringify({{
                        amount: amount,
                        customer_name: name,
                        customer_email: email,
                        customer_phone: phone
                    }})
                }});
                
                if (!res.ok) {{
                    const err = await res.json();
                    log(`Order creation failed: ${{JSON.stringify(err)}}`, "error");
                    return;
                }}

                const orderData = await res.json();
                currentOrderId = orderData.order_id;
                log(`Order created successfully: ${{orderData.order_id}}`, "success");
                log(`Launching Razorpay Checkout popup...`, "info");

                const options = {{
                    key: orderData.key_id,
                    amount: orderData.amount,
                    currency: orderData.currency,
                    name: "Phoenix Store",
                    description: "Order #" + orderData.receipt,
                    order_id: orderData.order_id,
                    prefill: {{
                        name: name,
                        email: email,
                        contact: phone
                    }},
                    notes: {{
                        merchant_order_id: orderData.receipt
                    }},
                    theme: {{
                        color: "#3b82f6"
                    }},
                    modal: {{
                        ondismiss: function() {{
                            log(`Checkout dismissed by user. Polling for payment failure webhook...`, "warn");
                            startPollingCases();
                        }}
                    }},
                    handler: function(response) {{
                        log(`Payment successful: ${{response.razorpay_payment_id}}`, "success");
                    }}
                }};

                const rzp = new Razorpay(options);
                rzp.on('payment.failed', function (resp) {{
                    log(`Razorpay Gateway reported payment.failed: ${{resp.error.code}} - ${{resp.error.description}}`, "error");
                    log(`Razorpay Payment ID: ${{resp.error.metadata.payment_id}}`, "warn");
                    startPollingCases(resp.error.metadata.payment_id);
                }});
                rzp.open();

            }} catch (e) {{
                log(`Unexpected error: ${{e.message}}`, "error");
            }}
        }}

        function startPollingCases(paymentId = null) {{
            if (pollInterval) clearInterval(pollInterval);
            log("Polling Phoenix Recovery Cases API for real-time orchestration updates...", "info");
            
            pollInterval = setInterval(async () => {{
                try {{
                    const res = await fetch("/api/v1/recovery-cases?limit=5");
                    if (!res.ok) return;
                    const data = await res.json();
                    const cases = data.items || data;
                    if (!cases || cases.length === 0) return;

                    const activeCase = cases[0];
                    document.getElementById("case-id-display").innerText = activeCase.id.slice(0, 18) + "...";

                    if (activeCase.status === "DETECTED") {{
                        setStatus("DETECTED", "status-detected");
                        log(`Case ${{activeCase.id.slice(0,8)}} entered DETECTED via live webhook!`, "warn");
                    }} else if (activeCase.status === "DIAGNOSING" || activeCase.status === "PLAN_GENERATED" || activeCase.status === "POLICY_APPROVED" || activeCase.status === "EXECUTING") {{
                        setStatus(activeCase.status, "status-diagnosing");
                        log(`Orchestrator Stage: ${{activeCase.status}}`, "info");
                    }} else if (activeCase.status === "AWAITING_PAYMENT") {{
                        setStatus("AWAITING_PAYMENT", "status-awaiting");
                        log(`🎯 Payment Link successfully created in Razorpay Test Mode!`, "success");
                        // Fetch case detail to get the short_url from recovery_actions
                        const caseDetailRes = await fetch(`/api/v1/recovery-cases/${{activeCase.id}}`);
                        if (caseDetailRes.ok) {{
                            const caseDetail = await caseDetailRes.json();
                            const actions = caseDetail.recovery_actions || [];
                            const actionWithLink = actions.find(a => a.payment_link_url) || (actions.length > 0 ? actions[0] : null);
                            if (actionWithLink && actionWithLink.payment_link_url) {{
                                const linkUrl = actionWithLink.payment_link_url;
                                const btnLink = document.getElementById("btn-pay-link");
                                btnLink.href = linkUrl;
                                btnLink.style.display = "block";
                                btnLink.innerText = "🎉 Pay Generated Phoenix Recovery Link";
                                log(`Recovery Link URL: ${{linkUrl}}`, "success");
                            }}
                        }}
                    }} else if (activeCase.status === "RECOVERED") {{
                        setStatus("RECOVERED", "status-recovered");
                        log(`✨ Case ${{activeCase.id.slice(0,8)}} is FULLY RECOVERED via payment_link.paid webhook! Amount: ₹${{(activeCase.recovered_amount/100).toFixed(2)}}`, "success");
                        clearInterval(pollInterval);
                    }}
                }} catch (err) {{
                    console.error(err);
                }}
            }}, 2000);
        }}
    </script>
</body>
</html>"""
    return HTMLResponse(content=html_content)
