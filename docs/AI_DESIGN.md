# AI Recovery Planner & Diagnostic Design — Project Phoenix

## 1. Architectural Role & AI Boundaries

In Project Phoenix, the AI component is strictly an **Advisory Diagnostic Engine**.

### Invariants Governing the AI:
1. **Zero Financial Access:** The AI has no API keys, credentials, or network tools to contact payment gateways or execute financial transactions.
2. **Structured Outputs Only:** The AI must return strictly validated JSON matching the `RecoveryPlan` schema. Free-form text responses are rejected.
3. **No Multi-Agent Swarms:** Phoenix uses a single-agent deterministic prompt pipeline with zero autonomous loops.
4. **Provider-Agnostic Interface:** All interactions are routed through an abstract `LLMClient` interface, allowing hot-swapping between OpenAI, Anthropic, Google Gemini, or local models.

---

## 2. Provider-Agnostic Abstraction

```
┌────────────────────────────────────────────────────────────┐
│                    LLMProvider (Abstract)                  │
│  + generate_recovery_plan(context: DiagnosticContext) ->   │
│    RecoveryPlan                                            │
└─────────────────────────────▲──────────────────────────────┘
                              │
     ┌────────────────────────┼────────────────────────┐
     │                        │                        │
┌────┴────────────┐  ┌────────┴────────┐  ┌────────────┴────────┐
│ OpenAIProvider  │  │ GeminiProvider  │  │  AnthropicProvider  │
│ (JSON Schema)   │  │ (Structured Out)│  │  (Tool Call / JSON) │
└─────────────────┘  └─────────────────┘  └─────────────────────┘
```

---

## 3. Failure Diagnostic Taxonomy

The AI maps each payment failure into one of five standardized diagnostic categories:

| Category | Typical Causes | Recovery Action Strategy |
| :--- | :--- | :--- |
| `USER_FRICTION` | OTP expired, user closed app, user cancelled MPIN | Instant payment link with short TTL (15–30 min) and 1-click checkout options. |
| `TECHNICAL_GATEWAY_ERROR` | Bank downtime, gateway timeout, network drop | Payment link with medium TTL (1–2 hr), prioritizing alternative payment methods. |
| `INSUFFICIENT_FUNDS` | Account balance low, daily UPI limit breached | Payment link with longer TTL (12–24 hr) allowing time for customer fund transfer. |
| `INSTRUMENT_INVALID` | Card expired, international card blocked, invalid VPA | Payment link enabling universal payment methods (Netbanking, Cards, UPI). |
| `TERMINAL_OR_SUSPICIOUS` | Stolen card block, fraudulent velocity, blacklisted entity | **DO_NOT_RECOVER** (Halt case, escalate to merchant review). |

---

## 4. Input & Output Schemas

### 4.1 Input Schema (`DiagnosticContext`)
```json
{
  "payment_id": "pay_O7f84mK12nABcd",
  "order_id": "order_O7f81xP89mKLno",
  "amount_paise": 499900,
  "currency": "INR",
  "payment_method": "upi",
  "payment_details": {
    "vpa": "customer@oksbi",
    "bank": null,
    "wallet": null
  },
  "error_telemetry": {
    "error_code": "BAD_REQUEST_ERROR",
    "error_description": "Payment was cancelled by the user on the bank/UPI app",
    "error_source": "customer",
    "error_step": "payment_authentication",
    "error_reason": "payment_cancelled"
  },
  "customer_history": {
    "prior_failures_today": 0,
    "lifetime_recoveries": 2,
    "is_repeat_customer": true
  },
  "merchant_constraints": {
    "min_link_expiry_minutes": 15,
    "max_link_expiry_minutes": 1440,
    "allowed_actions": ["DISPATCH_PAYMENT_LINK", "DO_NOT_RECOVER"]
  }
}
```

### 4.2 Output Schema (`RecoveryPlan` Pydantic Model)
```json
{
  "type": "object",
  "properties": {
    "root_cause_category": {
      "type": "string",
      "enum": [
        "USER_FRICTION",
        "TECHNICAL_GATEWAY_ERROR",
        "INSUFFICIENT_FUNDS",
        "INSTRUMENT_INVALID",
        "TERMINAL_OR_SUSPICIOUS"
      ]
    },
    "confidence_score": {
      "type": "number",
      "minimum": 0.0,
      "maximum": 1.0,
      "description": "Confidence score between 0.0 and 1.0"
    },
    "diagnostic_summary": {
      "type": "string",
      "maxLength": 300,
      "description": "Concise explanation of why the payment failed based on telemetry"
    },
    "recommended_action": {
      "type": "string",
      "enum": ["DISPATCH_PAYMENT_LINK", "DO_NOT_RECOVER"]
    },
    "urgency": {
      "type": "string",
      "enum": ["HIGH", "MEDIUM", "LOW"]
    },
    "link_expiry_minutes": {
      "type": "integer",
      "minimum": 15,
      "maximum": 1440,
      "description": "Proposed expiration time for the payment link in minutes"
    },
    "customer_facing_message": {
      "type": "string",
      "maxLength": 160,
      "description": "Friendly, contextual note explaining how to complete the payment"
    }
  },
  "required": [
    "root_cause_category",
    "confidence_score",
    "diagnostic_summary",
    "recommended_action",
    "urgency",
    "link_expiry_minutes",
    "customer_facing_message"
  ],
  "additionalProperties": false
}
```

---

## 5. System Prompt Specification

```text
You are the Diagnostic Reasoning Core for Project Phoenix, an enterprise payment recovery orchestrator for Razorpay merchants.

Your task is to analyze failed payment telemetry and synthesize a precise, policy-compliant recovery plan.

CORE RULES:
1. Always base your diagnosis strictly on the provided error_telemetry and payment_details.
2. Distinguish accurately between transient user friction (e.g. cancelled MPIN / timeout) vs hard financial/technical failures.
3. If the failure indicates fraud, invalid credentials, or blacklisted instruments, select TERMINAL_OR_SUSPICIOUS and recommended_action = "DO_NOT_RECOVER".
4. Recommend payment link expiry durations that match the root cause (e.g., 15-30m for user friction, 24h for insufficient funds).
5. Customer-facing messages must be professional, reassuring, and concise (under 160 characters).
6. Output MUST strictly match the provided JSON schema. No conversational prose or markdown formatting outside the JSON object.
```

---

## 6. Resilience & Malformation Handling

1. **Schema Validation:** The response is parsed via Pydantic (`RecoveryPlan.model_validate_json(raw_text)`).
2. **Retry Mechanism:** On validation failure, the prompt is re-submitted with the validation error up to 1 retry.
3. **Fail-Closed Fallback:** If the LLM fails after retry or is unreachable, the system triggers the deterministic fallback:
   - Case is transitioned to `ESCALATED`.
   - Audit log records `AI_PLAN_PARSING_FAILURE`.
   - Zero financial links are generated without human review.
