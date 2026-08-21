# Deterministic Policy Engine Specification — Project Phoenix

## 1. Role & Architectural Invariants

The **Deterministic Policy Engine** is the non-negotiable safety gatekeeper of Project Phoenix. It stands between the AI Recovery Planner (stochastic reasoning) and the Razorpay Execution Engine (financial API calls).

```
┌─────────────────────────┐          ┌─────────────────────────┐          ┌─────────────────────────┐
│   AI Recovery Planner   │ ───────► │   Deterministic Policy  │ ───────► │    Razorpay Executor    │
│  (Stochastic Reasoning) │   Plan   │    Engine (Gatekeeper)  │  Action  │  (Financial Execution)  │
└─────────────────────────┘          └────────────┬────────────┘          └─────────────────────────┘
                                                  │ (Violation)
                                                  ▼
                                     ┌─────────────────────────┐
                                     │     POLICY_REJECTED     │
                                     │     / ESCALATED         │
                                     └─────────────────────────┘
```

### Invariant Principles
1. **Zero LLM Authority Over Rules:** The LLM cannot alter, override, or relax any policy rule.
2. **Fail-Closed by Design:** If a policy check encounters an unhandled exception, corrupt data, or missing parameter, it evaluates to `REJECTED` and halts execution.
3. **Deterministic Evaluation:** Given the same context and recovery plan, the policy engine must always produce the exact same boolean pass/fail decision.

---

## 2. Policy Rule Hierarchy & Invariants

### 2.1 Hard Rules (Mandatory & Non-Bypassable)

| Rule ID | Rule Name | Description | Failure Behavior |
| :--- | :--- | :--- | :--- |
| `POL-001` | **Action Whitelist** | Proposed action must be in `['DISPATCH_PAYMENT_LINK', 'DO_NOT_RECOVER']`. | Immediate Reject (`POLICY_REJECTED`) |
| `POL-002` | **Max Retries Per Order** | Total recovery links issued for an order $\le \text{max\_attempts}$ (Default: 2). | Immediate Reject (`POLICY_REJECTED`) |
| `POL-003` | **Link Expiry Bounds** | Link expiry must be between $\text{min\_expiry}$ ($\ge 15\text{m}$, mandated by Razorpay) and $\text{max\_expiry}$ (1440m). | Clamped to 15m or Rejected |
| `POL-004` | **Customer Cooldown** | No recovery links sent to the same phone/email within $\text{cooldown\_window}$ (Default: 2 hours). | Immediate Reject (`POLICY_REJECTED`) |
| `POL-005` | **Amount Bounds** | Case amount must be between $\text{min\_amount}$ (₹100) and $\text{max\_amount}$ (₹500,000). | Escalate to Human (`ESCALATED`) |
| `POL-006` | **Fraud / Terminal Guard** | If AI flags `TERMINAL_OR_SUSPICIOUS`, action must be `DO_NOT_RECOVER`. | Immediate Reject if action is link generation |
| `POL-007` | **Customer Note Length** | Customer facing note must not exceed 160 characters and must not contain forbidden phrases/links. | Sanitize or Reject |

---

## 3. Evaluation Algorithm (Python Reference)

```python
from dataclasses import dataclass
from typing import List, Optional
from datetime import datetime, timedelta

@dataclass
class PolicyRuleResult:
    rule_id: str
    rule_name: str
    passed: bool
    details: str

@dataclass
class PolicyEvaluationResult:
    decision: str  # 'PASSED', 'REJECTED', 'ESCALATED'
    evaluated_rules: List[PolicyRuleResult]
    violations: List[str]

class PolicyEngine:
    def evaluate(
        self,
        case: "RecoveryCase",
        plan: "RecoveryPlan",
        policy: "MerchantPolicy",
        prior_actions: List["RecoveryAction"]
    ) -> PolicyEvaluationResult:
        results: List[PolicyRuleResult] = []
        violations: List[str] = []

        # Rule 1: Action Whitelist
        if plan.recommended_action not in ["DISPATCH_PAYMENT_LINK", "DO_NOT_RECOVER"]:
            violations.append(f"Action '{plan.recommended_action}' is not whitelisted.")
            results.append(PolicyRuleResult("POL-001", "Action Whitelist", False, "Invalid action"))
        else:
            results.append(PolicyRuleResult("POL-001", "Action Whitelist", True, "Action allowed"))

        # If action is DO_NOT_RECOVER, pass policy to cleanly close
        if plan.recommended_action == "DO_NOT_RECOVER":
            return PolicyEvaluationResult("PASSED", results, violations)

        # Rule 2: Max Retries
        completed_links_count = len([a for a in prior_actions if a.status in ["ISSUED", "PAID"]])
        if completed_links_count >= policy.max_retry_attempts:
            violations.append(f"Max retries exceeded: {completed_links_count}/{policy.max_retry_attempts}")
            results.append(PolicyRuleResult("POL-002", "Max Retries", False, "Limit exceeded"))
        else:
            results.append(PolicyRuleResult("POL-002", "Max Retries", True, "Within limit"))

        # Rule 3: Expiry Bounds
        if not (policy.min_link_expiry_minutes <= plan.link_expiry_minutes <= policy.max_link_expiry_minutes):
            violations.append(
                f"Expiry {plan.link_expiry_minutes}m outside bounds [{policy.min_link_expiry_minutes}m, {policy.max_link_expiry_minutes}m]"
            )
            results.append(PolicyRuleResult("POL-003", "Expiry Bounds", False, "Expiry invalid"))
        else:
            results.append(PolicyRuleResult("POL-003", "Expiry Bounds", True, "Expiry valid"))

        # Rule 4: Customer Cooldown
        if prior_actions:
            latest_action = max(prior_actions, key=lambda a: a.created_at)
            cooldown_delta = timedelta(hours=policy.customer_cooldown_hours)
            if datetime.utcnow() - latest_action.created_at < cooldown_delta:
                violations.append(f"Customer in cooldown window ({policy.customer_cooldown_hours}h)")
                results.append(PolicyRuleResult("POL-004", "Customer Cooldown", False, "In cooldown"))
            else:
                results.append(PolicyRuleResult("POL-004", "Customer Cooldown", True, "Cooldown satisfied"))
        else:
            results.append(PolicyRuleResult("POL-004", "Customer Cooldown", True, "No prior actions"))

        # Rule 5: Amount Bounds
        if not (policy.min_amount_paise <= case.amount <= policy.max_amount_paise):
            violations.append(f"Amount {case.amount} paise outside allowable range")
            results.append(PolicyRuleResult("POL-005", "Amount Bounds", False, "Amount out of range"))
        else:
            results.append(PolicyRuleResult("POL-005", "Amount Bounds", True, "Amount valid"))

        # Rule 6: Fraud / Terminal Guard
        if plan.root_cause_category == "TERMINAL_OR_SUSPICIOUS" and plan.recommended_action == "DISPATCH_PAYMENT_LINK":
            violations.append("Cannot dispatch link for suspicious/fraudulent failure")
            results.append(PolicyRuleResult("POL-006", "Fraud Guard", False, "Blocked by fraud guard"))
        else:
            results.append(PolicyRuleResult("POL-006", "Fraud Guard", True, "Guard satisfied"))

        # Final Decision
        if violations:
            decision = "REJECTED"
        else:
            decision = "PASSED"

        return PolicyEvaluationResult(decision=decision, evaluated_rules=results, violations=violations)
```

---

## 4. Fail-Closed Error Handling

If any unhandled exception occurs inside `evaluate()`:
1. The error is trapped and logged with full stack trace.
2. An audit log entry `POLICY_ENGINE_SYSTEM_ERROR` is recorded.
3. The engine returns:
   ```json
   {
     "decision": "REJECTED",
     "violations": ["POLICY_ENGINE_INTERNAL_EXCEPTION: Fail-closed triggered."]
   }
   ```
4. Execution stops immediately; no payment link is created.
