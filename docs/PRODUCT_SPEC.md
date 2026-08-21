# Product Specification — Project Phoenix

## 1. Executive Summary & Problem Statement

In high-volume e-commerce and subscription checkouts, **10% to 30% of payment attempts fail**. These failures stem from diverse causes:
- Technical errors (bank server downtime, gateway timeouts, network dropouts)
- User friction (OTP expiration, wrong PIN entry, browser tab closure)
- Financial/Instrument issues (insufficient balance, card limit exceeded, inactive international cards)

### The Current State of Recovery
1. **Dumb Retries:** Automated systems blindly retry charges, aggravating bank fraud algorithms and annoying customers.
2. **Generic Spam:** Standard dunning emails arrive hours later with dead links or unhelpful instructions.
3. **Checkout Abandonment:** Over 70% of customers who experience a payment failure never return to complete the order on their own.

### The Phoenix Solution
Project Phoenix is an **Autonomous AI Revenue Recovery Orchestrator** for Razorpay merchants. It intercepts payment failures in real-time, diagnoses the root cause using failure telemetry, formulates a targeted recovery plan, verifies the plan against deterministic safety policies, and autonomously dispatches a dynamic, customized Razorpay Payment Link to recover the transaction.

---

## 2. Target User Personas

| Persona | Role | Primary Pain Point | Value from Phoenix |
| :--- | :--- | :--- | :--- |
| **Finance Operations Lead** | Revenue Assurance | Revenue leakage from payment drop-offs; lack of clear auditability on why payments failed. | Full visibility into failure taxonomy, automated recovery tracking, transparent audit trail. |
| **E-Commerce / Growth Manager** | Conversion Optimization | High cart abandonment due to failed payments; loss of high-value shoppers. | Dynamic payment links with personalized messaging and custom expiry sent instantly. |
| **Engineering / DevOps Lead** | Payment Reliability | Managing complex webhook integrations and ensuring idempotency without race conditions. | Robust, policy-governed orchestration layer with zero LLM security risks. |

---

## 3. Product Scope & Boundaries

### 3.1 In-Scope for MVP (The Golden Path)
- **Real-time Webhook Ingestion:** Ingesting `payment.failed` and `payment_link.paid` from Razorpay Test Mode.
- **HMAC Signature Verification:** Verifying all incoming webhook payloads securely.
- **Idempotent Ingestion & State Machine:** Tracking recovery cases across standardized lifecycle states.
- **Authoritative Reconciliation:** Checking Razorpay payment status (`GET /v1/payments/{id}`) before initiating recovery.
- **AI Failure Diagnosis:** Structured categorization of failure reasons (Technical, User Friction, Instrument Failure, Insufficient Funds).
- **Deterministic Policy Validation:** Guardrails enforcing maximum retry count, cooldown windows, link expiry times, and customer blacklists.
- **Autonomous Payment Link Generation:** Calling Razorpay's `POST /v1/payment_links` with contextual metadata.
- **Outcome Verification:** Closing the recovery loop when `payment_link.paid` webhook arrives.
- **Merchant Dashboard (Next.js):** Real-time recovery case pipeline view, AI reasoning inspector, audit log viewer, and manual override (HITL).

### 3.2 Explicitly Out-of-Scope for MVP
- Multi-tenant SaaS billing & auth (single-tenant merchant architecture for MVP).
- Auto-debit / recurring subscription mandate recovery (UPI Autopay / e-mandates).
- Multi-agent autonomous swarms.
- Custom machine learning models trained on historical merchant datasets (using structured LLM reasoning instead).
- Multi-channel outbound messaging infrastructure (e.g., Twilio WhatsApp API, SendGrid) — leveraging Razorpay's native SMS/Email notification capabilities within the Payment Link API for MVP.
- Complex portfolio risk optimization algorithms.

---

## 4. Key Workflows & User Journeys

### 4.1 Autonomous Recovery (The Golden Path)
1. Customer initiates an order of ₹4,999 on the merchant store.
2. The transaction fails due to `BAD_REQUEST_ERROR` / `PAYMENT_CANCELLED_BY_USER` or `GATEWAY_ERROR`.
3. Razorpay emits a `payment.failed` webhook.
4. Phoenix validates signature, logs event, and checks authoritative status.
5. Context Engine aggregates error code, order amount, and customer details.
6. AI Recovery Planner diagnoses the issue: *"Customer experienced a temporary UPI intent timeout."* It proposes: *"Generate a payment link with 30-minute validity and UPI/Card options enabled."*
7. Policy Engine verifies the proposed link is within limits (1 attempt, expiry >= 15 min, amount matches order).
8. Payment Link Executor invokes Razorpay `POST /v1/payment_links`.
9. Customer receives payment link, pays via Razorpay checkout.
10. Razorpay emits `payment_link.paid`.
11. Phoenix updates case status to `RECOVERED` and logs full audit history.

### 4.2 Human-in-the-Loop (HITL) Override
1. An operator opens the Phoenix Dashboard.
2. In the "Action Required / Escalated" tab, a case is flagged because the failure reason was ambiguous or violated a policy constraint.
3. The operator views the failure context, the AI's diagnostic reasoning, and the policy violation reason.
4. The operator can:
   - Click **"Approve Link Dispatch"** (manual override with customized expiry).
   - Click **"Mark as Non-Recoverable"** (cancels the case).
   - Click **"Re-run Diagnosis"** (re-triggers context evaluation).

---

## 5. Functional & Non-Functional Requirements

### 5.1 Functional Requirements
- **FR-01:** Webhook receiver must verify `X-Razorpay-Signature` using constant-time HMAC-SHA256 comparison.
- **FR-02:** Webhook ingestion must be idempotent; duplicate event IDs must return HTTP 200 without creating duplicate cases.
- **FR-03:** State reconciliation must confirm the transaction is genuinely failed before creating recovery tasks.
- **FR-04:** AI Recovery Planner must output pure JSON adhering to the `RecoveryPlan` schema.
- **FR-05:** Policy engine must execute synchronously and fail-closed if any invariant is violated.
- **FR-06:** Payment link creation must include unique reference IDs linking back to `recovery_case_id`.
- **FR-07:** Dashboard must display real-time status of all active recovery cases, diagnosis summaries, and recovery rates.

### 5.2 Non-Functional Requirements
- **NFR-01 (Performance):** Webhook acknowledgment response time $< 200\text{ ms}$.
- **NFR-02 (Reliability):** Zero double-charge or duplicate payment link creation on webhook replay.
- **NFR-03 (Security):** Zero leakage of Razorpay API keys or secrets in logs, prompts, or frontend bundles.
- **NFR-04 (Auditability):** 100% of state transitions and external API calls recorded in append-only audit tables.

---

## 6. Success Metrics & KPIs

| Metric | Target | Definition |
| :--- | :--- | :--- |
| **Recovery Conversion Rate** | $> 25\%$ | Recovered revenue cases / Total recoverable failed cases |
| **Orchestration Latency** | $< 3.5\text{ s}$ | Time from `payment.failed` receipt to Payment Link creation |
| **Policy Violation Catch Rate** | $100\%$ | Unsafe or non-compliant AI recommendations blocked |
| **Duplicate Prevention** | $100\%$ | Duplicate webhooks processed with zero redundant links |
