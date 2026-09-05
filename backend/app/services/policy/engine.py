"""Deterministic Policy Engine implementation."""

from datetime import datetime, timezone, timedelta
import logging
from typing import Sequence

from app.models.recovery_action import RecoveryAction
from app.models.recovery_case import RecoveryCase
from app.schemas.ai import RecoveryPlan
from app.services.policy.models import (
    DecisionType,
    MerchantPolicy,
    PolicyDecision,
    PolicyRuleId,
    PolicyRuleResult,
    PolicyViolation,
)

logger = logging.getLogger(__name__)

WHITELISTED_ACTIONS = frozenset({
    "CREATE_PAYMENT_LINK",
    "DISPATCH_PAYMENT_LINK",
    "RETRY_LATER",
    "CUSTOMER_ACTION",
    "HUMAN_REVIEW",
    "DO_NOT_RECOVER",
})


class PolicyEngine:
    """Deterministic, fail-closed evaluation engine for AI recovery plans."""

    def evaluate(
        self,
        case: RecoveryCase,
        plan: RecoveryPlan,
        policy: MerchantPolicy | None = None,
        prior_actions: Sequence[RecoveryAction] | None = None,
        current_time: datetime | None = None,
    ) -> PolicyDecision:
        """Deterministically evaluate a proposed RecoveryPlan against merchant and system guardrails."""
        now = current_time or datetime.now(timezone.utc)
        evaluated_rules: list[PolicyRuleResult] = []
        violations: list[PolicyViolation] = []
        reason_codes: list[str] = []

        try:
            # POL-007: Merchant Constraints & Policy Validation
            if policy is None:
                violations.append(
                    PolicyViolation(
                        rule_id=PolicyRuleId.POL_007.value,
                        code="INVALID_MERCHANT_POLICY",
                        message="Merchant policy is missing or unavailable.",
                    )
                )
                reason_codes.append("INVALID_MERCHANT_POLICY")
                evaluated_rules.append(
                    PolicyRuleResult(
                        rule_id=PolicyRuleId.POL_007.value,
                        rule_name="Merchant Constraints",
                        passed=False,
                        details="Merchant policy is missing; fail-closed triggered.",
                        code="INVALID_MERCHANT_POLICY",
                    )
                )
                return PolicyDecision(
                    decision=DecisionType.REJECT,
                    action="CREATE_PAYMENT_LINK",
                    reason_codes=reason_codes,
                    constraints={},
                    evaluated_rules=evaluated_rules,
                    violations=violations,
                )

            # Validate merchant policy numerical invariants
            if (
                policy.min_link_expiry_minutes < 15
                or policy.max_link_expiry_minutes > 1440
                or policy.min_link_expiry_minutes > policy.max_link_expiry_minutes
                or policy.min_amount_paise < 0
                or policy.max_amount_paise < policy.min_amount_paise
                or policy.max_retry_attempts < 1
            ):
                violations.append(
                    PolicyViolation(
                        rule_id=PolicyRuleId.POL_007.value,
                        code="INVALID_MERCHANT_POLICY",
                        message="Merchant policy parameters violate system boundary constraints.",
                    )
                )
                reason_codes.append("INVALID_MERCHANT_POLICY")
                evaluated_rules.append(
                    PolicyRuleResult(
                        rule_id=PolicyRuleId.POL_007.value,
                        rule_name="Merchant Constraints",
                        passed=False,
                        details="Merchant policy invariants violated.",
                        code="INVALID_MERCHANT_POLICY",
                    )
                )
            else:
                evaluated_rules.append(
                    PolicyRuleResult(
                        rule_id=PolicyRuleId.POL_007.value,
                        rule_name="Merchant Constraints",
                        passed=True,
                        details="Merchant policy valid.",
                    )
                )

            # POL-001: Action Whitelist
            raw_action = plan.recommended_action.value if hasattr(plan.recommended_action, "value") else str(plan.recommended_action)
            canonical_action = raw_action
            if canonical_action == "DISPATCH_PAYMENT_LINK":
                canonical_action = "CREATE_PAYMENT_LINK"

            if raw_action not in WHITELISTED_ACTIONS:
                violations.append(
                    PolicyViolation(
                        rule_id=PolicyRuleId.POL_001.value,
                        code="ACTION_NOT_ALLOWED",
                        message=f"Action '{raw_action}' is not in whitelisted actions.",
                    )
                )
                reason_codes.append("ACTION_NOT_ALLOWED")
                evaluated_rules.append(
                    PolicyRuleResult(
                        rule_id=PolicyRuleId.POL_001.value,
                        rule_name="Action Whitelist",
                        passed=False,
                        details=f"Action '{raw_action}' prohibited.",
                        code="ACTION_NOT_ALLOWED",
                    )
                )
            else:
                evaluated_rules.append(
                    PolicyRuleResult(
                        rule_id=PolicyRuleId.POL_001.value,
                        rule_name="Action Whitelist",
                        passed=True,
                        details=f"Action '{raw_action}' allowed (canonical: '{canonical_action}').",
                    )
                )

            # POL-002: Recovery Eligibility
            is_eligible = True
            if case.is_recovered or case.status == "RECOVERED":
                is_eligible = False
                violations.append(
                    PolicyViolation(
                        rule_id=PolicyRuleId.POL_002.value,
                        code="RECOVERY_ALREADY_COMPLETED",
                        message="Case has already been successfully recovered.",
                    )
                )
                reason_codes.append("RECOVERY_ALREADY_COMPLETED")
                evaluated_rules.append(
                    PolicyRuleResult(
                        rule_id=PolicyRuleId.POL_002.value,
                        rule_name="Recovery Eligibility",
                        passed=False,
                        details="Case already recovered.",
                        code="RECOVERY_ALREADY_COMPLETED",
                    )
                )
            elif case.status in {"RESOLVED_EXTERNALLY", "CANCELLED"} or case.amount <= 0 or case.currency != "INR":
                is_eligible = False
                violations.append(
                    PolicyViolation(
                        rule_id=PolicyRuleId.POL_002.value,
                        code="INELIGIBLE_CASE_STATE",
                        message=f"Case status '{case.status}' or payment telemetry is ineligible for recovery.",
                    )
                )
                reason_codes.append("INELIGIBLE_CASE_STATE")
                evaluated_rules.append(
                    PolicyRuleResult(
                        rule_id=PolicyRuleId.POL_002.value,
                        rule_name="Recovery Eligibility",
                        passed=False,
                        details=f"Ineligible state: status={case.status}, amount={case.amount}, currency={case.currency}",
                        code="INELIGIBLE_CASE_STATE",
                    )
                )
            else:
                evaluated_rules.append(
                    PolicyRuleResult(
                        rule_id=PolicyRuleId.POL_002.value,
                        rule_name="Recovery Eligibility",
                        passed=True,
                        details="Case is eligible for recovery.",
                    )
                )

            # If action is DO_NOT_RECOVER, we permit the plan to close without financial execution
            if canonical_action == "DO_NOT_RECOVER":
                decision = DecisionType.ALLOW if not violations else DecisionType.REJECT
                return PolicyDecision(
                    decision=decision,
                    action="DO_NOT_RECOVER",
                    reason_codes=reason_codes,
                    constraints={},
                    evaluated_rules=evaluated_rules,
                    violations=violations,
                )

            # POL-003: Link Expiry Bounds
            expiry_minutes = plan.link_expiry_minutes
            if expiry_minutes is None or expiry_minutes < policy.min_link_expiry_minutes:
                violations.append(
                    PolicyViolation(
                        rule_id=PolicyRuleId.POL_003.value,
                        code="EXPIRY_BELOW_MINIMUM",
                        message=f"Expiry {expiry_minutes}m is below minimum {policy.min_link_expiry_minutes}m.",
                    )
                )
                reason_codes.append("EXPIRY_BELOW_MINIMUM")
                evaluated_rules.append(
                    PolicyRuleResult(
                        rule_id=PolicyRuleId.POL_003.value,
                        rule_name="Link Expiry Bounds",
                        passed=False,
                        details=f"Expiry {expiry_minutes}m < {policy.min_link_expiry_minutes}m",
                        code="EXPIRY_BELOW_MINIMUM",
                    )
                )
            elif expiry_minutes > policy.max_link_expiry_minutes:
                violations.append(
                    PolicyViolation(
                        rule_id=PolicyRuleId.POL_003.value,
                        code="EXPIRY_ABOVE_MAXIMUM",
                        message=f"Expiry {expiry_minutes}m exceeds maximum {policy.max_link_expiry_minutes}m.",
                    )
                )
                reason_codes.append("EXPIRY_ABOVE_MAXIMUM")
                evaluated_rules.append(
                    PolicyRuleResult(
                        rule_id=PolicyRuleId.POL_003.value,
                        rule_name="Link Expiry Bounds",
                        passed=False,
                        details=f"Expiry {expiry_minutes}m > {policy.max_link_expiry_minutes}m",
                        code="EXPIRY_ABOVE_MAXIMUM",
                    )
                )
            else:
                evaluated_rules.append(
                    PolicyRuleResult(
                        rule_id=PolicyRuleId.POL_003.value,
                        rule_name="Link Expiry Bounds",
                        passed=True,
                        details=f"Expiry {expiry_minutes}m within [{policy.min_link_expiry_minutes}m, {policy.max_link_expiry_minutes}m].",
                    )
                )

            # POL-004: Amount Limits (Authoritative from RecoveryCase)
            authoritative_amount = case.amount
            if authoritative_amount < policy.min_amount_paise:
                violations.append(
                    PolicyViolation(
                        rule_id=PolicyRuleId.POL_004.value,
                        code="AMOUNT_BELOW_MINIMUM",
                        message=f"Amount {authoritative_amount} paise is below merchant minimum {policy.min_amount_paise} paise.",
                    )
                )
                reason_codes.append("AMOUNT_BELOW_MINIMUM")
                evaluated_rules.append(
                    PolicyRuleResult(
                        rule_id=PolicyRuleId.POL_004.value,
                        rule_name="Amount Limits",
                        passed=False,
                        details=f"Amount {authoritative_amount} < {policy.min_amount_paise}",
                        code="AMOUNT_BELOW_MINIMUM",
                    )
                )
            elif authoritative_amount > policy.max_amount_paise:
                violations.append(
                    PolicyViolation(
                        rule_id=PolicyRuleId.POL_004.value,
                        code="AMOUNT_LIMIT_EXCEEDED",
                        message=f"Amount {authoritative_amount} paise exceeds merchant maximum {policy.max_amount_paise} paise.",
                    )
                )
                reason_codes.append("AMOUNT_LIMIT_EXCEEDED")
                evaluated_rules.append(
                    PolicyRuleResult(
                        rule_id=PolicyRuleId.POL_004.value,
                        rule_name="Amount Limits",
                        passed=False,
                        details=f"Amount {authoritative_amount} > {policy.max_amount_paise}",
                        code="AMOUNT_LIMIT_EXCEEDED",
                    )
                )
            else:
                evaluated_rules.append(
                    PolicyRuleResult(
                        rule_id=PolicyRuleId.POL_004.value,
                        rule_name="Amount Limits",
                        passed=True,
                        details=f"Amount {authoritative_amount} paise within limit.",
                    )
                )

            # POL-005: Customer Cooldown
            actions = list(prior_actions or [])
            if actions:
                # Find most recent action
                latest_action = max(actions, key=lambda a: a.created_at)
                action_created_at = latest_action.created_at
                if action_created_at.tzinfo is None:
                    action_created_at = action_created_at.replace(tzinfo=timezone.utc)
                
                cooldown_window = timedelta(hours=policy.customer_cooldown_hours)
                if (now - action_created_at) < cooldown_window:
                    violations.append(
                        PolicyViolation(
                            rule_id=PolicyRuleId.POL_005.value,
                            code="COOLDOWN_ACTIVE",
                            message=f"Customer is within cooldown period ({policy.customer_cooldown_hours}h).",
                        )
                    )
                    reason_codes.append("COOLDOWN_ACTIVE")
                    evaluated_rules.append(
                        PolicyRuleResult(
                            rule_id=PolicyRuleId.POL_005.value,
                            rule_name="Customer Cooldown",
                            passed=False,
                            details=f"Last action at {action_created_at.isoformat()} is within cooldown.",
                            code="COOLDOWN_ACTIVE",
                        )
                    )
                else:
                    evaluated_rules.append(
                        PolicyRuleResult(
                            rule_id=PolicyRuleId.POL_005.value,
                            rule_name="Customer Cooldown",
                            passed=True,
                            details="Cooldown window satisfied.",
                        )
                    )
            else:
                evaluated_rules.append(
                    PolicyRuleResult(
                        rule_id=PolicyRuleId.POL_005.value,
                        rule_name="Customer Cooldown",
                        passed=True,
                        details="No prior actions recorded.",
                    )
                )

            # POL-006: Retry Limits
            issued_or_active_actions = [
                a for a in actions if a.status in {"PENDING", "ISSUED", "PAID"}
            ]
            if len(issued_or_active_actions) >= policy.max_retry_attempts:
                violations.append(
                    PolicyViolation(
                        rule_id=PolicyRuleId.POL_006.value,
                        code="RETRY_LIMIT_EXCEEDED",
                        message=f"Maximum recovery attempts reached ({len(issued_or_active_actions)}/{policy.max_retry_attempts}).",
                    )
                )
                reason_codes.append("RETRY_LIMIT_EXCEEDED")
                evaluated_rules.append(
                    PolicyRuleResult(
                        rule_id=PolicyRuleId.POL_006.value,
                        rule_name="Retry Limits",
                        passed=False,
                        details=f"Active/issued attempts {len(issued_or_active_actions)} >= max {policy.max_retry_attempts}.",
                        code="RETRY_LIMIT_EXCEEDED",
                    )
                )
            else:
                evaluated_rules.append(
                    PolicyRuleResult(
                        rule_id=PolicyRuleId.POL_006.value,
                        rule_name="Retry Limits",
                        passed=True,
                        details=f"Attempts {len(issued_or_active_actions)} < max {policy.max_retry_attempts}.",
                    )
                )

            # POL-008: High-Value & Low-Confidence Escalation
            is_escalation_required = False
            escalation_details = []
            if canonical_action == "HUMAN_REVIEW":
                is_escalation_required = True
                escalation_details.append("Action specifies manual human review")
            if policy.high_value_escalation_paise > 0 and authoritative_amount >= policy.high_value_escalation_paise:
                is_escalation_required = True
                escalation_details.append(f"Amount {authoritative_amount} paise >= high-value threshold {policy.high_value_escalation_paise} paise")
            if plan.confidence_score < getattr(policy, "min_confidence_score", 0.50):
                is_escalation_required = True
                escalation_details.append(f"Confidence score {plan.confidence_score:.2f} < threshold {getattr(policy, 'min_confidence_score', 0.50):.2f}")

            if is_escalation_required and not violations:
                reason_codes.append("REQUIRES_HUMAN_REVIEW")
                evaluated_rules.append(
                    PolicyRuleResult(
                        rule_id=PolicyRuleId.POL_008.value,
                        rule_name="High-Value & Low-Confidence Escalation",
                        passed=False,
                        details="; ".join(escalation_details),
                        code="REQUIRES_HUMAN_REVIEW",
                    )
                )
            else:
                evaluated_rules.append(
                    PolicyRuleResult(
                        rule_id=PolicyRuleId.POL_008.value,
                        rule_name="High-Value & Low-Confidence Escalation",
                        passed=True,
                        details="Within autonomous limits.",
                    )
                )

            # Final Decision Resolution
            if violations:
                decision = DecisionType.REJECT
            elif is_escalation_required:
                decision = DecisionType.ESCALATE
            else:
                decision = DecisionType.ALLOW

            constraints = {
                "expiry_minutes": expiry_minutes,
                "amount": authoritative_amount,
                "currency": case.currency,
            }

            return PolicyDecision(
                decision=decision,
                action=canonical_action,
                reason_codes=reason_codes,
                constraints=constraints,
                evaluated_rules=evaluated_rules,
                violations=violations,
            )

        except Exception as exc:
            logger.error("PolicyEngine encountered unhandled exception: %s", exc, exc_info=True)
            return PolicyDecision(
                decision=DecisionType.REJECT,
                action="CREATE_PAYMENT_LINK",
                reason_codes=["POLICY_ENGINE_INTERNAL_EXCEPTION"],
                constraints={},
                evaluated_rules=evaluated_rules,
                violations=[
                    PolicyViolation(
                        rule_id="POL-000",
                        code="POLICY_ENGINE_INTERNAL_EXCEPTION",
                        message=f"Fail-closed on exception: {exc}",
                    )
                ],
            )
