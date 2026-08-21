# API Contracts & Interface Specifications — Project Phoenix

This document defines the formal API contracts for the **Inbound Webhook Gateway**, **Internal Dashboard REST Endpoints**, and **Simulation Test Endpoints**.

---

## 1. Inbound Webhook Gateway

### 1.1 `POST /api/v1/webhooks/razorpay`

Receives asynchronous lifecycle events from Razorpay.

#### Headers Required
| Header Name | Type | Description |
| :--- | :--- | :--- |
| `X-Razorpay-Signature` | `string` | **Mandatory.** Hex-encoded HMAC-SHA256 signature calculated over raw request body using `RAZORPAY_WEBHOOK_SECRET`. |
| `X-Razorpay-Event-Id` | `string` | *(Optional if present in payload)* Unique Razorpay event identifier. |
| `Content-Type` | `string` | `application/json` |

#### Supported Events
- `payment.failed`
- `payment_link.paid`
- `payment_link.cancelled`
- `payment_link.expired`
- `order.paid`

#### Success Response
- **Status:** `200 OK`
- **Body:**
```json
{
  "status": "acknowledged",
  "event_id": "event_NfG71p9Xyz1234",
  "event_type": "payment.failed",
  "action_taken": "INGESTED" // or "DEDUPLICATED"
}
```

#### Error Responses
- **400 Bad Request:** Signature header missing or invalid JSON body.
- **401 Unauthorized:** HMAC signature verification failed.

---

## 2. Internal Dashboard REST Endpoints

### 2.1 List Recovery Cases
`GET /api/v1/cases`

Retrieves a paginated list of recovery cases with filtering.

#### Query Parameters
- `status`: Filter by state (e.g. `DETECTED`, `AWAITING_PAYMENT`, `RECOVERED`, `POLICY_REJECTED`, `FAILED`, `ESCALATED`)
- `search`: Search by `payment_id`, `order_id`, customer email or phone
- `page`: Integer (default: 1)
- `page_size`: Integer (default: 20, max: 100)

#### Response (`200 OK`)
```json
{
  "total": 45,
  "page": 1,
  "page_size": 20,
  "items": [
    {
      "id": "case_01J8F9X2Q9Z8K3V01N5A7B8C9D",
      "payment_id": "pay_O7f84mK12nABcd",
      "order_id": "order_O7f81xP89mKLno",
      "amount": 499900,
      "currency": "INR",
      "customer_email": "customer@example.com",
      "customer_phone": "+919876543210",
      "failure_code": "BAD_REQUEST_ERROR",
      "failure_reason": "PAYMENT_CANCELLED_BY_USER",
      "status": "RECOVERED",
      "recovery_strategy": "DISPATCH_PAYMENT_LINK_WITH_EXPIRY",
      "recovered_amount": 499900,
      "created_at": "2026-08-21T08:30:00Z",
      "updated_at": "2026-08-21T08:42:15Z"
    }
  ]
}
```

---

### 2.2 Get Single Recovery Case Detail
`GET /api/v1/cases/{case_id}`

Retrieves complete 360-degree context for a recovery case, including raw payment telemetry, AI diagnostic reasoning, policy validation report, generated payment link, and audit events.

#### Response (`200 OK`)
```json
{
  "id": "case_01J8F9X2Q9Z8K3V01N5A7B8C9D",
  "payment_id": "pay_O7f84mK12nABcd",
  "order_id": "order_O7f81xP89mKLno",
  "amount": 499900,
  "currency": "INR",
  "status": "RECOVERED",
  "customer": {
    "email": "customer@example.com",
    "phone": "+919876543210",
    "prior_failures_count": 1,
    "lifetime_recovered_count": 0
  },
  "failure_telemetry": {
    "error_code": "BAD_REQUEST_ERROR",
    "error_description": "Payment was cancelled by the user on the bank/UPI app",
    "error_source": "customer",
    "error_step": "payment_authentication",
    "error_reason": "payment_cancelled",
    "payment_method": "upi",
    "vpa": "customer@oksbi"
  },
  "ai_diagnosis": {
    "root_cause_category": "USER_FRICTION",
    "confidence_score": 0.94,
    "diagnostic_summary": "User initiated UPI payment but cancelled before entering MPIN due to timeout friction.",
    "recommended_action": "DISPATCH_PAYMENT_LINK",
    "urgency": "HIGH",
    "suggested_expiry_minutes": 30,
    "suggested_customer_note": "Complete your order with instant 1-click payment."
  },
  "policy_evaluation": {
    "decision": "PASSED",
    "evaluated_rules": [
      { "rule": "MAX_RETRY_PER_ORDER", "passed": true, "details": "1/2 attempts used" },
      { "rule": "LINK_EXPIRY_BOUNDS", "passed": true, "details": "30m between [15m, 1440m]" },
      { "rule": "CUSTOMER_COOLDOWN", "passed": true, "details": "Last contact was >4h ago" },
      { "rule": "AMOUNT_THRESHOLD", "passed": true, "details": "₹4,999 is within recoverable range" }
    ],
    "violations": []
  },
  "recovery_action": {
    "action_type": "CREATE_PAYMENT_LINK",
    "payment_link_id": "plink_O7f92zL34mUVwx",
    "payment_link_url": "https://rzp.io/i/Xyz12345",
    "reference_id": "PHX_7F4A21C9_01",
    "status": "PAID",
    "expires_at": "2026-08-21T09:00:00Z",
    "created_at": "2026-08-21T08:30:05Z",
    "paid_at": "2026-08-21T08:42:10Z"
  },
  "audit_trail": [
    {
      "id": "aud_01",
      "from_state": null,
      "to_state": "DETECTED",
      "trigger": "WEBHOOK_PAYMENT_FAILED",
      "actor": "SYSTEM_WEBHOOK_GATEWAY",
      "timestamp": "2026-08-21T08:30:00.102Z"
    },
    {
      "id": "aud_02",
      "from_state": "DETECTED",
      "to_state": "DIAGNOSING",
      "trigger": "RECONCILIATION_VERIFIED_FAILED",
      "actor": "RECONCILIATION_WORKER",
      "timestamp": "2026-08-21T08:30:01.400Z"
    },
    {
      "id": "aud_03",
      "from_state": "DIAGNOSING",
      "to_state": "PLAN_GENERATED",
      "trigger": "AI_PLAN_SYNTHESIZED",
      "actor": "AI_RECOVERY_PLANNER",
      "timestamp": "2026-08-21T08:30:03.150Z"
    },
    {
      "id": "aud_04",
      "from_state": "PLAN_GENERATED",
      "to_state": "POLICY_APPROVED",
      "trigger": "POLICY_GATE_PASSED",
      "actor": "DETERMINISTIC_POLICY_ENGINE",
      "timestamp": "2026-08-21T08:30:03.200Z"
    },
    {
      "id": "aud_05",
      "from_state": "POLICY_APPROVED",
      "to_state": "AWAITING_PAYMENT",
      "trigger": "PAYMENT_LINK_DISPATCHED",
      "actor": "RAZORPAY_EXECUTOR",
      "timestamp": "2026-08-21T08:30:05.000Z"
    },
    {
      "id": "aud_06",
      "from_state": "AWAITING_PAYMENT",
      "to_state": "RECOVERED",
      "trigger": "WEBHOOK_PAYMENT_LINK_PAID",
      "actor": "SYSTEM_WEBHOOK_GATEWAY",
      "timestamp": "2026-08-21T08:42:15.000Z"
    }
  ]
}
```

---

### 2.3 Manual Human-in-the-Loop Override
`POST /api/v1/cases/{case_id}/override`

Allows human operator to force an action or cancel a case.

#### Request Body
```json
{
  "action": "FORCE_APPROVE_LINK", // Options: "FORCE_APPROVE_LINK", "CANCEL_CASE", "RETRY_DIAGNOSIS"
  "reason": "Operator verified customer contacted support via chat requesting retry link.",
  "custom_expiry_minutes": 60
}
```

#### Response (`200 OK`)
```json
{
  "case_id": "case_01J8F9X2Q9Z8K3V01N5A7B8C9D",
  "status": "AWAITING_PAYMENT",
  "message": "Manual override executed successfully. Payment link generated.",
  "payment_link_url": "https://rzp.io/i/Manual123"
}
```

---

### 2.4 Recovery Metrics Overview
`GET /api/v1/metrics/overview`

Provides top-line performance metrics for the merchant recovery dashboard.

#### Response (`200 OK`)
```json
{
  "time_window": "LAST_30_DAYS",
  "total_failed_payments": 1280,
  "total_failed_amount": 63980000,
  "total_cases_opened": 1150,
  "total_cases_recovered": 368,
  "total_recovered_amount": 18396000,
  "recovery_rate_percentage": 32.0,
  "mean_time_to_recovery_seconds": 645,
  "failures_by_category": {
    "USER_FRICTION": 512,
    "TECHNICAL_GATEWAY_ERROR": 340,
    "INSUFFICIENT_FUNDS": 256,
    "INSTRUMENT_EXPIRED_OR_INVALID": 172
  }
}
```

---

### 2.5 Policy Configuration
`GET /api/v1/policies` & `PUT /api/v1/policies`

View or update merchant guardrails.

#### Request Body (`PUT /api/v1/policies`)
```json
{
  "max_recovery_attempts_per_order": 2,
  "min_link_expiry_minutes": 15,
  "max_link_expiry_minutes": 1440,
  "customer_cooldown_hours": 2,
  "min_case_amount_inr": 100,
  "max_case_amount_inr": 500000,
  "auto_execute_on_high_confidence": true,
  "confidence_threshold": 0.85
}
```

---

## 3. Simulation Test Endpoints (Local Testing & CI/CD Only)

> [!NOTE]
> **Strict Usage Boundary:**
> - These endpoints exist strictly for local backend testing, unit/integration test suites, and edge-case failure injection in CI/CD.
> - The live Buildathon demonstration uses genuine Razorpay Test Mode checkout interactions, genuine webhooks signed with `RAZORPAY_WEBHOOK_SECRET`, and real Razorpay Payment Links.

### 3.1 Simulate Payment Failure Event
`POST /api/v1/simulation/simulate-failure`

Generates a valid, signed Razorpay `payment.failed` webhook and posts it to the internal webhook gateway for automated testing.

#### Request Body
```json
{
  "amount_inr": 2999,
  "customer_email": "demo_shopper@example.com",
  "customer_phone": "+919988776655",
  "failure_scenario": "UPI_TIMEOUT" // Options: "UPI_TIMEOUT", "INSUFFICIENT_FUNDS", "BANK_DOWNTIME", "CARD_AUTH_FAILED"
}
```

#### Response (`201 Created`)
```json
{
  "simulated_payment_id": "pay_sim_01J8F9X2",
  "simulated_event_id": "event_sim_01J8F9X2",
  "case_id": "case_01J8F9X2Q9Z8K3V01N5A7B8C9D",
  "status": "DIAGNOSING"
}
```
