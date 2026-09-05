"""Policy Engine data structures and schemas."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class DecisionType(str, Enum):
    """Possible outcomes of policy evaluation."""

    ALLOW = "ALLOW"
    REJECT = "REJECT"
    ESCALATE = "ESCALATE"


class PolicyRuleId(str, Enum):
    """Approved policy rule identifiers POL-001 through POL-008."""

    POL_001 = "POL-001"  # Action Whitelist
    POL_002 = "POL-002"  # Recovery Eligibility
    POL_003 = "POL-003"  # Link Expiry Bounds
    POL_004 = "POL-004"  # Amount Limits
    POL_005 = "POL-005"  # Customer Cooldown
    POL_006 = "POL-006"  # Retry Limits
    POL_007 = "POL-007"  # Merchant Constraints
    POL_008 = "POL-008"  # High-Value & Low-Confidence Escalation


@dataclass(frozen=True)
class MerchantPolicy:
    """Merchant-defined guardrails and policy configuration."""

    merchant_id: str = "default_merchant"
    max_retry_attempts: int = 2
    min_link_expiry_minutes: int = 15
    max_link_expiry_minutes: int = 1440
    customer_cooldown_hours: int = 2
    min_amount_paise: int = 10000  # ₹100
    max_amount_paise: int = 50000000  # ₹500,000
    high_value_escalation_paise: int = 1000000  # ₹10,000 triggers human review
    min_confidence_score: float = 0.50
    auto_execute_enabled: bool = True


@dataclass
class PolicyViolation:
    """Detailed policy violation report."""

    rule_id: str
    code: str
    message: str


@dataclass
class PolicyRuleResult:
    """Result of evaluating an individual policy rule."""

    rule_id: str
    rule_name: str
    passed: bool
    details: str
    code: str | None = None


@dataclass
class PolicyDecision:
    """Immutable deterministic policy evaluation decision."""

    decision: DecisionType
    action: str
    reason_codes: list[str] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    evaluated_rules: list[PolicyRuleResult] = field(default_factory=list)
    violations: list[PolicyViolation] = field(default_factory=list)

    @property
    def is_allowed(self) -> bool:
        """Helper to check if execution is permitted."""
        return self.decision == DecisionType.ALLOW
