# Project Phoenix — Engineering Constitution & Architectural Invariants

> **Status:** ACTIVE & MANDATORY  
> **Scope:** Entire Project Phoenix Codebase  
> **Audience:** All Engineers, Subagents, and LLM Reasoning Units  

---

## 1. Core Thesis & Invariant Axioms

Project Phoenix is an **Autonomous AI Revenue Recovery Orchestrator** built for the Razorpay ecosystem. It is **NOT** a simple retry bot, nor is it an autonomous agent with direct financial execution privileges. 

The system operates strictly under the **Separation of Reasoning and Execution**:

$$\text{Detect} \longrightarrow \text{Diagnose} \longrightarrow \text{Decide (AI)} \longrightarrow \text{Policy Gate (Deterministic)} \longrightarrow \text{Execute (Deterministic)} \longrightarrow \text{Verify} \longrightarrow \text{Audit}$$

---

## 2. The 18 Non-Negotiable Invariants

1. **AI Reasons; Deterministic Code Executes:**  
   The LLM (Large Language Model) is strictly an advisory planner. It diagnoses failures and proposes recovery strategies in structured JSON. It possesses **zero** execution credentials and **cannot** invoke external APIs directly.

2. **No Direct Razorpay API Access for LLMs:**  
   The LLM never formats, signs, or executes raw Razorpay HTTP requests. All Razorpay API interactions are executed by hardcoded, deterministic client code.

3. **Server-Side Credential Isolation:**  
   `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET` must remain exclusively in backend environment variables. They must never be exposed to the client, frontend bundle, LLM prompts, or client logs.

4. **Mandatory HMAC-SHA256 Webhook Verification:**  
   Every inbound webhook payload must be validated using `X-Razorpay-Signature` against `RAZORPAY_WEBHOOK_SECRET` before any parsing, persistence, or processing takes place. Unverified webhooks must be rejected with HTTP 400/401 immediately.

5. **Strict Two-Tier Idempotency (Phoenix DB + Razorpay reference_id):**  
   - `reference_id` alone does not equal idempotency. Safe execution requires:
     $$\text{Phoenix Database-Level State Lock} + \text{Razorpay Unique reference\_id} = \text{Safe Idempotent Execution}$$
   - Every webhook event is deduplicated via the official `x-razorpay-event-id` header in `raw_webhook_events`.
   - Every financial recovery action must first acquire an atomic database lock before invoking Razorpay API with a deterministic `reference_id`.

6. **Out-of-Order Webhook Resilience:**  
   Webhooks can arrive out of chronological order (e.g., `payment.captured` before `payment.failed`, or late `payment_link.paid`). The state machine must ignore or handle stale transitions gracefully without corrupting case state.

7. **`payment.failed` is Not Automatically Permanent Loss:**  
   A failed payment is a signal for diagnostic evaluation. The orchestrator must distinguish between transient technical failures (gateway timeout, bank downtime), customer friction (expired OTP, insufficient balance), and terminal failures (fraud block, card expired, invalid account).

8. **Authoritative State Reconciliation:**  
   Before executing any external recovery action (such as creating a new payment link), Phoenix must verify the authoritative status of the original payment against Razorpay's API (`GET /v1/payments/{payment_id}`) or order status to confirm the customer hasn't already paid via an alternative attempt.

9. **Deterministic Policy Gate Fails Closed & Enforces 15m Min Expiry:**  
   If the AI planner suggests an action that violates merchant policy (e.g., discount ceiling exceeded, max retry count breached, blacklisted customer, cooldown window violated, link expiry < 15 minutes, or unparseable JSON), the Policy Engine must **fail closed** (reject the action, mark the case as `ESCALATED` or `CANCELLED`, and halt execution).

10. **Immutable Full-Spectrum Auditability:**  
    Every state change, LLM input/output, policy decision, API payload, and webhook receipt must be persisted in an append-only audit trail (`audit_logs` table) with high-resolution timestamps.

11. **Strict Simulation vs. Live Test Mode Demo Boundaries:**  
    - `/api/v1/simulation/*` endpoints are strictly reserved for local development, automated CI/CD integration tests, edge-case failure injection, and unit tests.
    - The final Buildathon demo and live validation must execute exclusively against real Razorpay Test Mode behavior (genuine checkout failures, genuine signed webhooks from Razorpay servers, and genuine Razorpay Payment Links paid in test mode). No fake simulation buttons masquerading as Razorpay in demo presentations.

12. **Development Exclusively in Razorpay Test Mode:**  
    Live mode API keys (`rzp_live_*`) are forbidden during development and testing. Only `rzp_test_*` keys may be configured.

13. **Strict MVP Scope Discipline:**  
    Do not build multi-merchant SaaS tenancy, multi-agent swarms, complex reinforcement learning, subscription mandate recurring recovery, or portfolio risk models before the core single-case MVP Golden Path is battle-tested.

14. **Lean Dependency Footprint:**  
    Do not introduce message brokers (Kafka/RabbitMQ), distributed task runners (Celery/Temporal), or complex graph frameworks (LangGraph/CrewAI) until performance metrics and requirements demand them. For the MVP, FastAPI background tasks and ACID PostgreSQL transactions are standard.

15. **Single-Agent Structured Advisory Design:**  
    Use a single structured prompt pipeline with deterministic JSON schema output. Swarms and autonomous agent loops are banned.

16. **Provider-Agnostic LLM Layer:**  
    All LLM interactions must go through an abstract interface (`LLMClient`) supporting standard structured JSON output. Switching between OpenAI, Anthropic, Gemini, or local models must require only an environment variable change.

17. **No Direct Schema Fabrication:**  
    All payload definitions, Razorpay entity structures, and error codes must be cross-referenced against authoritative Razorpay documentation. Assumptions must be explicitly documented in `/docs/RAZORPAY_INTEGRATION.md`.

18. **Explicit Human-in-the-Loop (HITL) Override:**  
    The system must allow an operator to pause, override, or manually resolve any recovery case from the frontend dashboard at any state.

---

## 3. The Golden Path Flow

```
[Customer Checkout] 
       │ (Failure occurs)
       ▼
[Razorpay Test Mode] ──(Webhook: payment.failed)──► [Phoenix Webhook Gateway]
                                                              │
                                                   (Verify Signature)
                                                              ▼
                                                   [raw_webhook_events]
                                                              │
                                                   (Reconcile Status)
                                                              ▼
                                                   [Recovery Case Created]
                                                              │
                                                   (Extract Context)
                                                              ▼
                                                   [AI Recovery Planner]
                                                              │ (Structured JSON Plan)
                                                              ▼
                                                   [Deterministic Policy Gate]
                                                              │
                                                   (Passed Checks)
                                                              ▼
                                                   [Payment Link Executor]
                                                              │ (POST /v1/payment_links)
                                                              ▼
[Customer Pays Link] ◄──(SMS/Email Link)───────── [Razorpay Payment Link]
       │
       ▼
[Razorpay Test Mode] ──(Webhook: payment_link.paid)─► [Phoenix Webhook Gateway]
                                                              │
                                                   (Verify & Reconcile)
                                                              ▼
                                                   [Case Status: RECOVERED]
                                                              │
                                                   [Immutable Audit Log]
```

---

## 4. Enforcement & Code Review Criteria

Any PR or code change that:
- Calls Razorpay API directly from an LLM prompt response without policy gating,
- Omits webhook signature verification,
- Mutates database state without generating an audit record,
- Skips idempotency checks,
- Logs credentials or unmasked PII,

**MUST BE REJECTED IMMEDIATELY.**
