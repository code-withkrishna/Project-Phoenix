# Deterministic Policy Engine Specification — Project Phoenix

## 1. Role & Architectural Invariants

The **Deterministic Policy Engine** is the non-negotiable safety gatekeeper of Project Phoenix. It stands between the AI Recovery Planner (stochastic reasoning) and the Razorpay Execution Guard & Client (financial API calls).

```text
                    AI LAYER
                       │
                       │ RecoveryPlan
                       ▼
              ┌─────────────────┐
              │ POLICY ENGINE   │
              │ deterministic   │
              └────────┬────────┘
                       │
                  PolicyDecision
                       ▼
              ┌─────────────────┐
              │ EXECUTION GUARD │
              │ deterministic   │
              └────────┬────────┘
                       │
                  RecoveryAction
                       ▼
              ┌─────────────────┐
              │ RAZORPAY CLIENT │
              └─────────────────┘
```

### Invariant Principles
1. **Zero LLM Authority Over Rules:** The LLM cannot alter, override, or relax any policy rule.
2. **Fail-Closed by Design:** If a policy check encounters an unhandled exception, corrupt data, or missing parameter, it evaluates to `REJECTED` / `ESCALATED` and halts execution.
3. **Deterministic Evaluation:** Given the same context, merchant policy, prior actions, and recovery plan, the policy engine always produces the exact same deterministic decision.
4. **Authoritative Financial Data:** Financial amounts and currencies are strictly sourced from authoritative database records (case / payment record), never from LLM suggestions.

---

## 2. Policy Rule Hierarchy & Invariants (`POL-001` - `POL-007`)

| Rule ID | Rule Name | Description | Failure Behavior | Machine Reason Code |
| :--- | :--- | :--- | :--- | :--- |
| `POL-001` | **Action Whitelist** | Proposed action must be explicitly permitted (`CREATE_PAYMENT_LINK` or non-financial `DO_NOT_RECOVER`). | Immediate Reject | `ACTION_NOT_ALLOWED` |
| `POL-002` | **Recovery Eligibility** | Case must be eligible for recovery (not already recovered, valid payment data, not terminally invalid). | Immediate Reject | `RECOVERY_ALREADY_COMPLETED` / `INELIGIBLE_CASE_STATE` |
| `POL-003` | **Link Expiry Bounds** | Link expiry must be between $15\text{m}$ (mandated by Razorpay) and $1440\text{m}$ (24h). | Reject / Clamp per policy | `EXPIRY_BELOW_MINIMUM` / `EXPIRY_ABOVE_MAXIMUM` |
| `POL-004` | **Amount Limits** | Case authoritative amount must satisfy ₹100 ($10,000\text{ paise}$) $\le \text{amount} \le$ ₹500,000 ($50,000,000\text{ paise}$). | Immediate Reject | `AMOUNT_BELOW_MINIMUM` / `AMOUNT_LIMIT_EXCEEDED` |
| `POL-005` | **Customer Cooldown** | No recovery action dispatched to the same customer within cooldown window (Default: 2 hours). | Immediate Reject | `COOLDOWN_ACTIVE` |
| `POL-006` | **Retry Limits** | Number of completed/issued recovery actions must be $< \text{max\_retry\_attempts}$ (Default: 2). | Immediate Reject | `RETRY_LIMIT_EXCEEDED` |
| `POL-007` | **Merchant Constraints** | Apply merchant-specific constraints. If merchant policy is unavailable or invalid, fail closed. | Immediate Reject | `INVALID_MERCHANT_POLICY` |

---

## 3. Evaluation Structures & Algorithm

### Policy Decision Object
```json
{
  "decision": "ALLOW",
  "action": "CREATE_PAYMENT_LINK",
  "reason_codes": [],
  "constraints": {
    "expiry_minutes": 30
  }
}
```

Decisions:
- `ALLOW`: Permitted to proceed to Execution Guard.
- `REJECT`: Prohibited from execution.
- `ESCALATE`: Requires human-in-the-loop inspection.

Rejection codes include:
- `ACTION_NOT_ALLOWED`
- `RECOVERY_ALREADY_COMPLETED`
- `INELIGIBLE_CASE_STATE`
- `EXPIRY_BELOW_MINIMUM`
- `EXPIRY_ABOVE_MAXIMUM`
- `AMOUNT_BELOW_MINIMUM`
- `AMOUNT_LIMIT_EXCEEDED`
- `COOLDOWN_ACTIVE`
- `RETRY_LIMIT_EXCEEDED`
- `INVALID_MERCHANT_POLICY`

---

## 4. Fail-Closed Error Handling

If any unhandled exception occurs inside `PolicyEngine.evaluate()`:
1. The error is trapped and logged with full context.
2. The engine returns:
   ```json
   {
     "decision": "REJECT",
     "action": "CREATE_PAYMENT_LINK",
     "reason_codes": ["POLICY_ENGINE_INTERNAL_EXCEPTION"],
     "constraints": {}
   }
   ```
3. Execution stops immediately; no external Razorpay call is made.

