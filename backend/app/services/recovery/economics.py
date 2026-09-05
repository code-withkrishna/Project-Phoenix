"""Economic Decisioning Engine for Project Phoenix.

Calculates Expected Net Recovery (ENR):
    Expected Net Recovery = (P(Recovery) * Recoverable Amount)
                           - Action Cost
                           - Risk Penalty
                           - Customer Friction Penalty
                           - Retry Cost Penalty
"""

from dataclasses import dataclass, field
from typing import Sequence

from app.schemas.ai import DiagnosticContext, RecommendedAction, RootCauseCategory


@dataclass(frozen=True)
class EconomicEvaluation:
    """Detailed breakdown of economic recovery calculations."""

    recovery_probability: float
    amount_paise: int
    gross_expected_recovery_paise: int
    action_cost_paise: int
    risk_penalty_paise: int
    customer_friction_paise: int
    retry_penalty_paise: int
    expected_net_recovery_paise: int
    is_economically_viable: bool
    recommended_action: str
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert evaluation to serializable dictionary."""
        return {
            "recovery_probability": round(self.recovery_probability, 4),
            "amount_paise": self.amount_paise,
            "gross_expected_recovery_paise": self.gross_expected_recovery_paise,
            "action_cost_paise": self.action_cost_paise,
            "risk_penalty_paise": self.risk_penalty_paise,
            "customer_friction_paise": self.customer_friction_paise,
            "retry_penalty_paise": self.retry_penalty_paise,
            "expected_net_recovery_paise": self.expected_net_recovery_paise,
            "is_economically_viable": self.is_economically_viable,
            "recommended_action": self.recommended_action,
            "reasons": self.reasons,
        }


class EconomicDecisionEngine:
    """Deterministic, explainable economic evaluator for payment recoveries."""

    DEFAULT_LINK_ACTION_COST_PAISE: int = 300
    DEFAULT_RETRY_LATER_COST_PAISE: int = 150
    DEFAULT_CUSTOMER_ACTION_COST_PAISE: int = 250
    MIN_PROFITABILITY_THRESHOLD_PAISE: int = 500  # ₹5 minimum expected recovery

    def evaluate(
        self,
        context: DiagnosticContext,
        root_cause: RootCauseCategory,
        raw_confidence: float,
        proposed_action: RecommendedAction | str,
    ) -> EconomicEvaluation:
        """Compute the expected net recovery and validate economic viability."""
        amount = context.amount_paise
        p_recovery = max(0.0, min(1.0, raw_confidence))
        action_str = proposed_action.value if hasattr(proposed_action, "value") else str(proposed_action)

        reasons: list[str] = []

        # 1. Base action cost
        if action_str in ("DISPATCH_PAYMENT_LINK", "CREATE_PAYMENT_LINK"):
            action_cost = self.DEFAULT_LINK_ACTION_COST_PAISE
        elif action_str == "RETRY_LATER":
            action_cost = self.DEFAULT_RETRY_LATER_COST_PAISE
        elif action_str == "CUSTOMER_ACTION":
            action_cost = self.DEFAULT_CUSTOMER_ACTION_COST_PAISE
        else:
            action_cost = 0

        # 2. Risk penalty based on root cause
        if root_cause == RootCauseCategory.TERMINAL_OR_SUSPICIOUS:
            risk_penalty = int(amount * 0.90)
            reasons.append("High fraud/security risk flagged: severe risk penalty applied")
        elif root_cause == RootCauseCategory.INSTRUMENT_INVALID:
            risk_penalty = int(amount * 0.15)
            reasons.append("Invalid instrument: moderate retry risk penalty")
        elif root_cause == RootCauseCategory.INSUFFICIENT_FUNDS:
            risk_penalty = int(amount * 0.10)
            reasons.append("Insufficient funds: temporal liquidity risk factored")
        else:
            risk_penalty = int(amount * 0.02)
            reasons.append("Standard transaction risk: low risk penalty")

        # 3. Customer friction penalty (prior attempts today)
        prior_failures = getattr(context.customer_history, "prior_failures_today", 0) if context.customer_history else 0
        is_repeat = getattr(context.customer_history, "is_repeat_customer", False) if context.customer_history else False
        lifetime_recoveries = getattr(context.customer_history, "lifetime_recoveries", 0) if context.customer_history else 0

        friction_penalty = prior_failures * 300  # ₹3 penalty per prior failure today
        if is_repeat and lifetime_recoveries > 0:
            friction_penalty = max(0, friction_penalty - 200)
            reasons.append(f"Repeat customer with {lifetime_recoveries} past recoveries: friction reduced")
        elif prior_failures > 1:
            reasons.append(f"{prior_failures} previous failures today: increased customer friction penalty")

        # 4. Retry penalty
        retry_penalty = prior_failures * 200

        # 5. Gross & Net Expected Recovery Calculation
        gross_expected = int(p_recovery * amount)
        net_expected = gross_expected - (action_cost + risk_penalty + friction_penalty + retry_penalty)

        # 6. Economic viability and action adjustment
        is_viable = (
            net_expected >= self.MIN_PROFITABILITY_THRESHOLD_PAISE
            and root_cause != RootCauseCategory.TERMINAL_OR_SUSPICIOUS
            and action_str != "DO_NOT_RECOVER"
        )

        final_action = action_str
        if not is_viable and action_str != "DO_NOT_RECOVER":
            if root_cause == RootCauseCategory.TERMINAL_OR_SUSPICIOUS:
                final_action = "DO_NOT_RECOVER"
                reasons.append("Terminal error or security risk: recovery halted to prevent financial loss")
            elif net_expected < self.MIN_PROFITABILITY_THRESHOLD_PAISE:
                final_action = "DO_NOT_RECOVER"
                reasons.append(
                    f"Expected net recovery (₹{net_expected / 100:.2f}) below profitability threshold (₹{self.MIN_PROFITABILITY_THRESHOLD_PAISE / 100:.2f})"
                )
        else:
            reasons.append(
                f"Positive expected net recovery (₹{net_expected / 100:.2f}) with {p_recovery * 100:.0f}% confidence"
            )

        return EconomicEvaluation(
            recovery_probability=p_recovery,
            amount_paise=amount,
            gross_expected_recovery_paise=gross_expected,
            action_cost_paise=action_cost,
            risk_penalty_paise=risk_penalty,
            customer_friction_paise=friction_penalty,
            retry_penalty_paise=retry_penalty,
            expected_net_recovery_paise=net_expected,
            is_economically_viable=is_viable,
            recommended_action=final_action,
            reasons=reasons,
        )
