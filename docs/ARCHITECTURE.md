# System Architecture — Project Phoenix

## 1. High-Level Architecture Overview

Project Phoenix is designed as a modular, asynchronous, policy-governed revenue recovery orchestration platform. The system operates on a clean separation of concerns where **stochastic AI planning is decoupled from deterministic financial execution**.

```
                           ┌─────────────────────────────────────────────────────────┐
                           │                   RAZORPAY GATEWAY                      │
                           │                      (Test Mode)                        │
                           └───────────────▲─────────────────────────┬───────────────┘
                                           │                         │
                                    Payment Links             Webhook Events
                              (POST /v1/payment_links)   (payment.failed, payment_link.paid)
                                           │                         │
                                           │                         ▼
┌──────────────────────────────────────────┼─────────────────────────────────────────────────────────┐
│ PHOENIX BACKEND (FastAPI / Python)       │                                                         │
│                                          │             ┌─────────────────────────┐                 │
│                                          │             │ 1. Webhook Gateway      │                 │
│                                          │             │   - Signature Validator │                 │
│                                          │             └───────────┬─────────────┘                 │
│                                          │                         │                               │
│                                          │                         ▼                               │
│                                          │             ┌─────────────────────────┐                 │
│                                          │             │ 2. Ingestion & Event    │                 │
│                                          │             │    Idempotency Store    │                 │
│                                          │             └───────────┬─────────────┘                 │
│                                          │                         │                               │
│                                          │                         ▼                               │
│                                          │             ┌─────────────────────────┐                 │
│                                          │             │ 3. State Reconciliation │                 │
│                                          │             │    (GET /v1/payments)   │                 │
│                                          │             └───────────┬─────────────┘                 │
│                                          │                         │                               │
│                                          │                         ▼                               │
│                                          │             ┌─────────────────────────┐                 │
│                                          │             │ 4. Context Engine       │                 │
│                                          │             │    (Extract features)   │                 │
│                                          │             └───────────┬─────────────┘                 │
│                                          │                         │                               │
│                                          │                         ▼                               │
│  ┌─────────────────────────┐             │             ┌─────────────────────────┐                 │
│  │ 7. Razorpay Executor    │             │             │ 5. AI Recovery Planner  │                 │
│  │   - Deterministic API   │◄──────────────────────────┤   - Structured JSON Out │                 │
│  │   - Idempotency Key Gen │             │             │   - Diagnostic Reasoner │                 │
│  └───────────┬─────────────┘             │             └───────────┬─────────────┘                 │
│              │                           │                         │                               │
│              │                           │                         ▼                               │
│              │                           │             ┌─────────────────────────┐                 │
│              │                           │             │ 6. Deterministic Policy │                 │
│              │                           │             │    Engine (Fail-Closed) │                 │
│              │                           │             └─────────────────────────┘                 │
│              │                                                                                     │
│              ▼                                                                                     │
│  ┌─────────────────────────┐                           ┌─────────────────────────┐                 │
│  │ 8. State Machine &      │                           │ 9. Audit Trail & Log    │                 │
│  │    Case Lifecycle Mgr   ├──────────────────────────►│    Store (Immutable)    │                 │
│  └─────────────────────────┘                           └─────────────────────────┘                 │
│                                                                                                    │
└──────────────────────────────────────────┬─────────────────────────────────────────────────────────┘
                                           │
                                    REST API Queries
                                           │
                                           ▼
┌────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ PHOENIX FRONTEND (Next.js / TypeScript / shadcn/ui)                                               │
│                                                                                                    │
│  ┌───────────────────────────────┐  ┌───────────────────────────────┐  ┌─────────────────────────┐ │
│  │ Recovery Pipeline Dashboard   │  │ Case Details & Reasoner View  │  │ HITL Action / Overrides │ │
│  └───────────────────────────────┘  └───────────────────────────────┘  └─────────────────────────┘ │
└────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Core Subsystems & Components

### 2.1 Webhook Gateway
- **Function:** Ingests raw HTTP POST webhooks from Razorpay.
- **Security Check:** Computes HMAC-SHA256 of raw request body using `RAZORPAY_WEBHOOK_SECRET` and compares in constant time against header `X-Razorpay-Signature`.
- **Response SLA:** Responds immediately with HTTP 200 OK after signature verification and persistence to prevent gateway timeouts/retries.

### 2.2 Event Ingestion & Idempotency Store
- **Function:** Guarantees exact-once processing semantics.
- **Mechanism:** Stores the raw payload in `raw_webhook_events`. Enforces a unique constraint on the official `x-razorpay-event-id` header (stored as `event_id`).
- **Deduplication:** If a duplicate event arrives (e.g., webhook retry), it is logged and immediately acknowledged with HTTP 200 without re-triggering the orchestration pipeline.

### 2.3 State Reconciliation Engine
- **Function:** Eliminates race conditions and false failure assumptions.
- **Mechanism:** When a `payment.failed` event is received, Phoenix queries `GET /v1/payments/{payment_id}` or checks linked order state before taking action.
- **Protection:** Protects against scenarios where a user retried immediately on checkout and succeeded, or where webhooks arrived out of order.

### 2.4 Context Engine
- **Function:** Assembles a comprehensive diagnostic payload for the AI planner.
- **Context Gathered:**
  - Payment entity details: `amount`, `currency`, `method` (card, upi, netbanking), `bank`, `wallet`, `vpa`.
  - Error entity details: `error_code`, `error_description`, `error_source`, `error_step`, `error_reason`.
  - Customer context: historical failure count, past recoveries, lifetime value tier.
  - Merchant rules: allowable recovery channels, max allowable discounts, maximum retry attempts.

### 2.5 AI Recovery Planner (Advisory Layer)
- **Function:** Diagnoses the root cause of failure and formulates an optimal recovery strategy.
- **Design:** Provider-independent client wrapping structured outputs (OpenAI Functions/Structured Outputs, Gemini Function Calling, or Anthropic Tool Calling).
- **Output:** Validated against a strict Pydantic model (`RecoveryPlan`).
- **Constraint:** Zero access to environment variables, credentials, or network tools. Pure cognitive input $\to$ structured output.

### 2.6 Deterministic Policy Engine (Guardrail Layer)
- **Function:** Validates the AI's proposed `RecoveryPlan` against merchant-defined invariants.
- **Enforcement Rules:**
  - Maximum retry attempts per customer/order.
  - Mandatory minimum cooldown intervals between communications.
  - Expiry constraints on generated payment links (strictly minimum 15 mins, maximum 72 hours).
  - Discount/incentive caps (e.g., cannot offer incentives exceeding merchant threshold).
- **Safety Mode:** **Fails closed**. If validation fails or the AI output is malformed, the plan is rejected, the case is marked `POLICY_VIOLATION` or `ESCALATED`, and no financial API is called.

### 2.7 Payment Link Execution Engine
- **Function:** Deterministic worker that takes an approved `RecoveryPlan` and calls Razorpay REST API.
- **Action:** Generates a targeted Razorpay Payment Link (`POST /v1/payment_links`) with metadata binding it to the `recovery_case_id`.
- **Idempotency:** Generates and passes a unique `reference_id` to Razorpay to prevent duplicate link generation.

### 2.8 State Machine & Case Lifecycle Manager
- **Function:** Governs the lifecycle transitions of a `RecoveryCase`.
- **States:** `DETECTED` $\to$ `DIAGNOSING` $\to$ `PLAN_GENERATED` $\to$ `POLICY_APPROVED` $\to$ `EXECUTING` $\to$ `AWAITING_PAYMENT` $\to$ `RECOVERED` (or terminal failure states: `EXPIRED`, `CANCELLED`, `POLICY_REJECTED`, `FAILED`).
- **Invariants:** Disallows illegal transitions (e.g., cannot transition from `RECOVERED` to `FAILED`).

### 2.9 Immutable Audit Engine
- **Function:** Records an immutable ledger entry for every state transition, policy check, LLM request/response, and gateway API call.
- **Storage:** Appended to PostgreSQL table `audit_logs`.

---

## 3. End-to-End Golden Path Sequence

```mermaid
sequenceDiagram
    autonumber
    actor Customer
    participant RZP as Razorpay Gateway (Test)
    participant WG as Webhook Gateway
    participant DB as PostgreSQL Store
    participant REC as Reconciliation Engine
    participant CE as Context Engine
    participant AI as AI Recovery Planner
    participant POL as Deterministic Policy Engine
    participant EXE as Payment Link Executor

    Customer->>RZP: Attempts Payment (fails)
    RZP->>WG: POST /api/v1/webhooks/razorpay (payment.failed)
    Note over WG: Verify X-Razorpay-Signature (HMAC-SHA256)
    WG->>DB: Persist raw event (Idempotent INSERT via x-razorpay-event-id)
    WG-->>RZP: HTTP 200 OK
    
    WG->>REC: Trigger Reconciliation for payment_id
    REC->>RZP: GET /v1/payments/{payment_id}
    RZP-->>REC: Return authoritative payment status
    
    alt Payment is already captured / resolved
        REC->>DB: Mark Case RESOLVED_EXTERNALLY (Halt)
    else Payment confirmed failed
        REC->>DB: Create/Update RecoveryCase (DETECTED)
        DB->>CE: Assemble Diagnostic Context
        CE-->>AI: Provide Context (Error code, metadata, customer history)
        Note over AI: Diagnose failure & synthesize recovery strategy
        AI-->>POL: Submit RecoveryPlan (Strict JSON Schema)
        
        Note over POL: Evaluate Deterministic Invariants & Merchant Rules (Min 15m Expiry)
        alt Policy Validation Fails
            POL->>DB: Transition Case to POLICY_REJECTED (Halt)
        else Policy Validation Passes
            POL->>DB: Transition Case to POLICY_APPROVED
            POL->>EXE: Dispatch Approved Plan
            EXE->>RZP: POST /v1/payment_links (with reference_id)
            RZP-->>EXE: Return Payment Link Entity (id, short_url)
            EXE->>DB: Record RecoveryAction & Set Case AWAITING_PAYMENT
            
            Customer->>RZP: Customer opens short_url & completes payment
            RZP->>WG: POST /api/v1/webhooks/razorpay (payment_link.paid)
            Note over WG: Verify Signature & Persist
            WG->>DB: Match reference_id to RecoveryCase
            DB->>DB: Transition Case to RECOVERED
            DB->>DB: Append Immutable Audit Trail Entry
        end
    end
```

---

## 4. Security & Credential Isolation Architecture

1. **Zero Credential Exposure to LLM:**
   - LLM prompts are sanitized. PII (phone numbers, emails) is tokenized/masked where appropriate.
   - API secrets (`RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`) never enter LLM prompt contexts.
2. **Environment Variable Segregation:**
   - `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET` are read only by backend modules `razorpay_client.py`.
   - Frontend Next.js app communicates only with Phoenix internal APIs, never with Razorpay directly.
3. **Fail-Closed Networking:**
   - External calls from the backend are strictly limited to Razorpay REST endpoints (`https://api.razorpay.com/v1/...`) and the configured AI Provider endpoint.

---

## 5. Two-Tier Idempotency & Concurrency Strategy

> [!IMPORTANT]
> **The Idempotency Formula:**  
> $$\text{Phoenix Application DB State Lock} + \text{Razorpay Unique reference\_id} = \text{Safe Execution}$$  
> Relying solely on `reference_id` is insufficient because Razorpay rejects already-used reference IDs. Phoenix must guard its own state machine at the database level before contacting Razorpay.

1. **Inbound Webhook Deduplication:**
   - Evaluated on the official `x-razorpay-event-id` header against a unique database index on `raw_webhook_events.event_id`.
   - Concurrent duplicate deliveries are absorbed at the PostgreSQL insertion layer with `ON CONFLICT (event_id) DO NOTHING`.

2. **State Transition Concurrency Control:**
   - Atomic state transitions use conditional row-level matching:  
     `UPDATE recovery_cases SET status = 'EXECUTING' WHERE id = :id AND status = 'POLICY_APPROVED';`
   - Only the single worker thread that successfully updates the row acquires execution rights.

3. **Outbound Gateway Idempotency (`reference_id`):**
   - When issuing a Razorpay Payment Link, Phoenix generates a deterministic merchant reference key:  
     `reference_id = f"phx_rec_{case_id}_{action_sequence}"`
   - This provides secondary defense: if a network partition occurs between Phoenix and Razorpay, re-dispatching with the same `reference_id` prevents duplicate billing.

---

## 6. Deployment Architecture

```
[Browser / Operator] 
         │ (HTTPS)
         ▼
[Vercel: Next.js Frontend]
         │ (Internal REST APIs / Bearer Auth)
         ▼
[Render / Railway: FastAPI Backend]
    ├── Async Webhook Gateway (Ingestion)
    ├── Orchestration Engine (State Machine & Logic)
    └── Background Workers
         │
    ┌────┴───────────────────────────┬───────────────────────────┐
    ▼                                ▼                           ▼
[PostgreSQL Managed DB]      [AI Provider API]       [Razorpay API (Test)]
```
