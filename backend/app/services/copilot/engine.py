"""Tool-Constrained Merchant Copilot Engine for Project Phoenix.

Grounded in live PostgreSQL queries and deterministic business logic.
Guarantees zero hallucinated metrics by querying actual database tables.
"""

from dataclasses import dataclass
import logging
import re
from typing import Any
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_diagnosis import AIDiagnosis
from app.models.recovery_action import RecoveryAction
from app.models.recovery_case import RecoveryCase
from app.services.analytics.comparison import StrategyAnalyticsEngine

logger = logging.getLogger(__name__)


@dataclass
class CopilotResponse:
    """Structured response from Merchant Copilot."""

    intent: str
    reply_text: str
    data_payload: dict[str, Any]
    suggested_actions: list[str]


class MerchantCopilotEngine:
    """Natural Language Assistant grounded in deterministic database tools."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._analytics = StrategyAnalyticsEngine(session)

    async def query(self, query_text: str) -> CopilotResponse:
        """Parse merchant intent, execute safe DB tool, and synthesize structured reply."""
        text = (query_text or "").strip().lower()

        # 1. Intent: Baseline vs AI Comparison / ROI
        if any(w in text for w in ["baseline", "roi", "lift", "comparison", "compare", "strategy", "savings"]):
            return await self._handle_strategy_comparison()

        # 2. Intent: High-Value / Escalated / Pending Reviews
        if any(w in text for w in ["high value", "review", "pending", "escalat", "hitl", "urgent", "need review"]):
            return await self._handle_pending_reviews()

        # 3. Intent: Failure Root Causes & Trends
        if any(w in text for w in ["why", "fail", "reason", "cause", "trend", "breakdown", "error"]):
            # Check if querying a specific case or general breakdown
            case_match = re.search(r"([a-f0-9\-]{8,36}|pay_[a-z0-9]+)", text)
            if case_match:
                return await self._handle_case_explanation(case_match.group(1))
            return await self._handle_root_cause_breakdown()

        # 4. Intent: Revenue / Recovery Performance Overview
        if any(w in text for w in ["revenue", "how much", "recovered", "rate", "performance", "metric", "overview", "total"]):
            return await self._handle_revenue_overview()

        # Default: Executive Summary & Capabilities
        return await self._handle_executive_summary()

    async def _handle_revenue_overview(self) -> CopilotResponse:
        """Tool 1: Aggregate live revenue recovery metrics."""
        stmt = sa.select(RecoveryCase)
        res = await self._session.execute(stmt)
        cases = list(res.scalars().all())

        total_cases = len(cases)
        recovered_cases = [c for c in cases if c.is_recovered or c.status == "RECOVERED"]
        recovered_amount = sum(c.recovered_amount if c.recovered_amount > 0 else c.amount for c in recovered_cases)
        total_at_risk = sum(c.amount for c in cases)
        recovery_rate = (len(recovered_cases) / total_cases * 100.0) if total_cases > 0 else 0.0

        reply = (
            f"📈 **Revenue Recovery Overview**:\n"
            f"- **Recovered Revenue**: ₹{recovered_amount / 100:,.2f} across **{len(recovered_cases)}** successfully recovered cases.\n"
            f"- **Overall Recovery Rate**: **{recovery_rate:.1f}%** ({len(recovered_cases)} of {total_cases} failed payments).\n"
            f"- **Total Capital at Risk**: ₹{total_at_risk / 100:,.2f}."
        )

        return CopilotResponse(
            intent="REVENUE_OVERVIEW",
            reply_text=reply,
            data_payload={
                "total_cases": total_cases,
                "recovered_cases": len(recovered_cases),
                "recovered_amount_inr": recovered_amount / 100,
                "total_at_risk_inr": total_at_risk / 100,
                "recovery_rate_pct": round(recovery_rate, 2),
            },
            suggested_actions=["Show cases needing human review", "Compare with baseline strategy", "Show failure root causes"],
        )

    async def _handle_pending_reviews(self) -> CopilotResponse:
        """Tool 2: Fetch cases requiring Human-in-the-Loop review."""
        stmt = sa.select(RecoveryCase).where(RecoveryCase.status == "ESCALATED").order_by(RecoveryCase.created_at.desc()).limit(5)
        res = await self._session.execute(stmt)
        escalated_cases = list(res.scalars().all())

        if not escalated_cases:
            reply = "✅ **Zero Pending Reviews**: All active recovery cases are currently within autonomous execution boundaries or completed."
            return CopilotResponse(
                intent="PENDING_REVIEWS",
                reply_text=reply,
                data_payload={"pending_count": 0, "cases": []},
                suggested_actions=["Show total revenue recovered", "Show failure root causes"],
            )

        items_summary = []
        for c in escalated_cases:
            items_summary.append(
                f"- **Case `{str(c.id)[:8]}`** ({c.payment_id}): ₹{c.amount / 100:,.2f} — Reason: `{c.failure_reason or c.failure_code or 'Policy Escalation'}`"
            )

        reply = (
            f"⚠️ **{len(escalated_cases)} High-Priority Cases Requiring Review**:\n"
            + "\n".join(items_summary)
            + "\n\nYou can approve or reject these directly from the HITL queue on the dashboard."
        )

        return CopilotResponse(
            intent="PENDING_REVIEWS",
            reply_text=reply,
            data_payload={
                "pending_count": len(escalated_cases),
                "cases": [
                    {"id": str(c.id), "payment_id": c.payment_id, "amount_inr": c.amount / 100, "status": c.status}
                    for c in escalated_cases
                ],
            },
            suggested_actions=["Approve top case", "Explain decision for highest value case"],
        )

    async def _handle_root_cause_breakdown(self) -> CopilotResponse:
        """Tool 3: Analyze failure root cause distributions."""
        stmt = sa.select(RecoveryCase.failure_code, sa.func.count(RecoveryCase.id)).group_by(RecoveryCase.failure_code)
        res = await self._session.execute(stmt)
        counts = dict(res.all())

        if not counts:
            return CopilotResponse(
                intent="ROOT_CAUSE_BREAKDOWN",
                reply_text="📊 **Failure Telemetry**: No failed payments recorded yet in the system.",
                data_payload={},
                suggested_actions=["Simulate test payment failure"],
            )

        breakdown_lines = [f"- **{code or 'UNKNOWN'}**: {cnt} cases" for code, cnt in counts.items()]
        reply = "🔍 **Failure Root Cause Distribution**:\n" + "\n".join(breakdown_lines)

        return CopilotResponse(
            intent="ROOT_CAUSE_BREAKDOWN",
            reply_text=reply,
            data_payload={"distribution": counts},
            suggested_actions=["How much revenue was recovered?", "Show cases needing human review"],
        )

    async def _handle_case_explanation(self, identifier: str) -> CopilotResponse:
        """Tool 4: Detailed explanation for a specific case."""
        stmt = sa.select(RecoveryCase).where(
            sa.or_(
                RecoveryCase.payment_id == identifier,
                sa.cast(RecoveryCase.id, sa.String).like(f"{identifier}%"),
            )
        )
        res = await self._session.execute(stmt)
        case = res.scalars().first()

        if not case:
            return CopilotResponse(
                intent="CASE_EXPLANATION",
                reply_text=f"Could not find a recovery case matching `{identifier}`.",
                data_payload={"found": False},
                suggested_actions=["Show all cases", "Show revenue overview"],
            )

        diag_stmt = sa.select(AIDiagnosis).where(AIDiagnosis.case_id == case.id).order_by(AIDiagnosis.created_at.desc())
        diag_res = await self._session.execute(diag_stmt)
        diag = diag_res.scalars().first()

        reply = (
            f"📋 **Case Diagnostic Breakdown (`{str(case.id)[:8]}`)**:\n"
            f"- **Payment ID**: `{case.payment_id}`\n"
            f"- **Amount**: ₹{case.amount / 100:,.2f} ({case.currency})\n"
            f"- **Current State**: `{case.status}` (Recovered: {case.is_recovered})\n"
        )
        if diag:
            reply += (
                f"- **Root Cause**: `{diag.root_cause_category}`\n"
                f"- **Model Confidence**: {diag.confidence_score * 100:.0f}%\n"
                f"- **Diagnostic Summary**: {diag.diagnostic_summary}\n"
                f"- **Recommended Action**: `{diag.recommended_action}`\n"
            )

        return CopilotResponse(
            intent="CASE_EXPLANATION",
            reply_text=reply,
            data_payload={"case_id": str(case.id), "status": case.status, "amount_inr": case.amount / 100},
            suggested_actions=["Approve case", "Show all pending reviews"],
        )

    async def _handle_strategy_comparison(self) -> CopilotResponse:
        """Tool 5: Return quantitative ROI and strategy comparison."""
        report = await self._analytics.generate_comparison()
        rep_dict = report.to_dict()
        comp = rep_dict["comparison"]
        phx = rep_dict["phoenix_ai"]
        base = rep_dict["baseline"]

        if comp.get("is_live_data"):
            header = "⚡ **Phoenix AI vs. Naive Baseline Strategy (Live Database Telemetry)**:"
            note = ""
            lift_label = "Measured Net Revenue Lift"
        else:
            header = "⚡ **Phoenix AI vs. Naive Baseline Strategy (Simulated Benchmark)**:"
            note = "*Note: Metrics reflect our 100-failure benchmark simulation model before live event accumulation.*\n"
            lift_label = "Estimated Net Revenue Lift"

        reply = (
            f"{header}\n"
            f"{note}"
            f"- **{lift_label}**: **+{comp['revenue_lift_pct']}%** (+₹{comp['revenue_lift_inr']:,.2f})\n"
            f"- **Phoenix Recovery Rate**: **{phx['recovery_rate_pct']}%** (vs {base['recovery_rate_pct']}% baseline)\n"
            f"- **Unnecessary Retries Avoided**: **{comp['retries_avoided_count']}** brute-force retries avoided\n"
            f"- **Execution Cost Saved**: ₹{comp['cost_saved_inr']:,.2f}\n"
            f"- **Net Value Added**: **₹{comp['net_value_added_inr']:,.2f}**"
        )

        return CopilotResponse(
            intent="STRATEGY_COMPARISON",
            reply_text=reply,
            data_payload=rep_dict,
            suggested_actions=["Show total revenue recovered", "Show pending reviews"],
        )

    async def _handle_executive_summary(self) -> CopilotResponse:
        """Fallback: Executive assistant summary."""
        reply = (
            "👋 **Phoenix Merchant Copilot** is ready. You can ask me:\n"
            "- *'How much revenue did we recover?'*\n"
            "- *'Show high-value cases needing review'*\n"
            "- *'Why did payment X fail?'*\n"
            "- *'What is our recovery ROI vs baseline strategy?'*\n"
            "- *'Show failure root causes'*."
        )
        return CopilotResponse(
            intent="GENERAL_HELP",
            reply_text=reply,
            data_payload={},
            suggested_actions=["How much revenue did we recover?", "Compare with baseline strategy", "Show pending reviews"],
        )
