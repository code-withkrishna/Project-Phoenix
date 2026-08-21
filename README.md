# Project Phoenix — Autonomous AI Revenue Recovery Orchestrator

[![Track](https://img.shields.io/badge/Razorpay_AI_Buildathon-Track_03-blue.svg)](https://razorpay.com)
[![Architecture](https://img.shields.io/badge/Architecture-Policy--Governed_AI_Orchestrator-brightgreen.svg)](docs/ARCHITECTURE.md)
[![Status](https://img.shields.io/badge/Status-Architectural_Foundation-orange.svg)]()

Project Phoenix is a policy-governed revenue recovery orchestration platform designed for Razorpay merchants. It autonomously bridges the gap between payment failures and successful recovery using intelligent diagnosis, deterministic policy validation, and automated financial link execution.

---

## 🌟 The Core Thesis

Payment failures are not homogenous. When a transaction fails, traditional systems rely on static time-based retries or blunt email blasts, leading to customer annoyance, checkout abandonment, and lost revenue.

Phoenix introduces a high-integrity, automated 7-step orchestration pipeline:

$$\mathbf{Detect} \longrightarrow \mathbf{Diagnose} \longrightarrow \mathbf{Decide} \longrightarrow \mathbf{Policy\ Gate} \longrightarrow \mathbf{Execute} \longrightarrow \mathbf{Verify} \longrightarrow \mathbf{Audit}$$

### The Golden Rule of Phoenix
> **AI reasons; Deterministic code executes.**  
> The LLM serves purely as a diagnostic and planning advisor. It has zero credentials, zero network access to financial gateways, and zero direct execution privileges. Every action passes through a deterministic, fail-closed policy engine before touching Razorpay APIs.

---

## 🚀 The MVP Golden Path

```
Razorpay Test Mode (payment.failed)
             ↓
Webhook Gateway (HMAC-SHA256 Signature Verification)
             ↓
Event Persistence & Idempotency Check
             ↓
Authoritative Payment State Reconciliation (GET /v1/payments/{id})
             ↓
Recovery Case Creation (Status: DETECTED → DIAGNOSING)
             ↓
Context Engine (Failure taxonomy, Customer profile, Order metadata)
             ↓
AI Recovery Planner (Structured JSON diagnosis & recovery strategy)
             ↓
Deterministic Policy Engine (Hard business rules, limits, cooldowns)
             ↓
Payment Link Executor (POST /v1/payment_links with idempotency key)
             ↓
Customer Payment on Link (payment_link.paid webhook)
             ↓
Outcome Verification & State Transition (Status: RECOVERED)
             ↓
Immutable Audit Record Persisted
```

---

## 🛠️ Technology Stack

| Layer | Technology | Purpose |
| :--- | :--- | :--- |
| **Frontend** | Next.js (App Router), TypeScript, Tailwind CSS, shadcn/ui | Merchant recovery dashboard, case management, HITL overrides, analytics |
| **Backend** | Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2.0 | Async webhook gateway, state machine, policy engine, Razorpay client |
| **Database** | PostgreSQL 15+ | Raw event storage, case state tracking, audit logs, merchant policies |
| **AI Layer** | Provider-agnostic LLM interface (OpenAI / Anthropic / Gemini) | Failure categorization, recovery strategy proposal via JSON Schema |
| **Gateway** | Razorpay REST API & Webhooks (Test Mode) | Ingestion of `payment.failed`, `payment_link.paid`, generation of payment links |

---

## 📚 Technical Documentation Index

Detailed architectural blueprints are available in `/docs`:

1. [**System Architecture** (`docs/ARCHITECTURE.md`)](docs/ARCHITECTURE.md) — System topology, security isolation, idempotency model, and data flow.
2. [**Product Specification** (`docs/PRODUCT_SPEC.md`)](docs/PRODUCT_SPEC.md) — Problem statement, user journeys, MVP scope boundaries, and business rules.
3. [**API Contracts** (`docs/API_CONTRACTS.md`)](docs/API_CONTRACTS.md) — Inbound webhook specs and internal dashboard REST APIs.
4. [**Database Schema** (`docs/DATABASE.md`)](docs/DATABASE.md) — Relational schema, indices, audit tables, and transaction safety.
5. [**State Machine** (`docs/STATE_MACHINE.md`)](docs/STATE_MACHINE.md) — State lifecycle, legal transitions, and out-of-order webhook protection.
6. [**Razorpay Integration** (`docs/RAZORPAY_INTEGRATION.md`)](docs/RAZORPAY_INTEGRATION.md) — Test Mode setup, API endpoints, payload models, and verification checklists.
7. [**AI Design & Prompts** (`docs/AI_DESIGN.md`)](docs/AI_DESIGN.md) — Failure diagnostic taxonomy, prompt schemas, and structured JSON outputs.
8. [**Policy Engine** (`docs/POLICY_ENGINE.md`)](docs/POLICY_ENGINE.md) — Deterministic validation rules, fail-closed mechanics, and merchant guardrails.
9. [**Testing Strategy** (`docs/TESTING.md`)](docs/TESTING.md) — Unit testing, replay simulation, idempotency verification, and test mode end-to-end runs.
10. [**Engineering Constitution** (`PHOENIX_RULES.md`)](PHOENIX_RULES.md) — Invariant safety rules and code review standards.

---

## 🔒 Security & Safety Principles

- **Zero Gateway Exposure:** Razorpay keys (`RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`) are never shared with the AI or client.
- **Fail-Closed Architecture:** If the AI produces invalid JSON, proposes an invalid action, or violates policy boundaries, the system defaults to a safe halt and flags the case for manual review.
- **Test Mode Exclusivity:** All development and validation occur strictly within Razorpay Test Mode.
