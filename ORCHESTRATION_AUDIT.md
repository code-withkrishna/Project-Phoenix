# Project Phoenix — Orchestration Bridge Audit

## 1. Executive Summary & Audit Context

Project Phoenix has successfully built and verified all core Wave 1, Wave 2, and Wave 3 components with a 107-test baseline passing. However, the system currently stops after creating a `RecoveryCase` in `DETECTED` during webhook processing. The subsequent autonomous pipeline—Context Engine $\to$ AI Recovery Planner $\to$ Deterministic Policy Engine $\to$ Execution Guard $\to$ Recovery Executor $\to$ Razorpay Payment Link creation—has not yet been connected to the live background webhook lifecycle.

This audit document identifies the exact orchestration gap, reviews all existing services and lifecycle transitions, and defines the deterministic, fail-closed, transaction-safe orchestration bridge.

---

## 2. Answers to Audit Inquiries (A through K)

### A. Where a RecoveryCase is created
- **File:** `backend/app/services/recovery/case_service.py` (`RecoveryCaseService.handle_payment_failed`, lines 74–85).
- **Mechanism:** Calls `RecoveryCaseRepository.create_if_absent` with `payment_id`, `order_id`, `amount`, `currency`, customer telemetry, and initial `status="DETECTED"`.
- **Idempotency:** Protected by PostgreSQL `ON CONFLICT (payment_id) DO NOTHING`.

### B. Where the case transitions into DETECTED
- **File:** `backend/app/repositories/recovery_cases.py` (line 85 defaults `status="DETECTED"` upon row insertion).
- **Audit Log:** Recorded in `RecoveryCaseService.handle_payment_failed` with `from_state=None`, `to_state="DETECTED"`, `trigger="WEBHOOK_PAYMENT_FAILED"`, and `actor="SYSTEM_WEBHOOK_GATEWAY"`.

### C. Whether anything currently reacts to DETECTED
- **Current State:** **NOTHING** currently reacts to `DETECTED`. After logging `Recovery case created: case_id=...`, `handle_payment_failed` simply returns. The case remains indefinitely in `DETECTED`.

### D. Where Context Engine is currently invoked
- **File:** `backend/app/services/recovery/context_engine.py`.
- **Invocation:** Instantiated in `AIRecoveryPlanner.__init__` (`backend/app/services/recovery/planner.py:72`) and invoked in `AIRecoveryPlanner.plan_recovery` (`context = await self._context_engine.build_context(case)`).
- **Function:** Gathers customer history, strips PII, and produces `DiagnosticContext`.

### E. Where AI Recovery Planner is currently invoked
- **File:** `backend/app/services/recovery/planner.py` (`AIRecoveryPlanner.plan_recovery`).
- **Invocation:** Only invoked in isolated unit/integration tests (`test_ai_planning_integration.py`, `test_mocked_recovery_pipeline.py`). Not connected to the webhook processing flow.

### F. Where Policy Engine is currently invoked
- **File:** `backend/app/services/policy/engine.py` (`PolicyEngine.evaluate`).
- **Invocation:** Instantiated in `RecoveryExecutor.__init__` (`backend/app/services/recovery/executor.py:50`) and invoked in `RecoveryExecutor.execute_recovery` (lines 77–83).

### G. Where Execution Guard is currently invoked
- **File:** `backend/app/services/recovery/execution_guard.py` (`ExecutionGuard.verify`).
- **Invocation:** Instantiated in `RecoveryExecutor.__init__` (`backend/app/services/recovery/executor.py:51`) and invoked in `RecoveryExecutor.execute_recovery` (lines 138–146) to verify 14 mandatory invariants immediately prior to Razorpay API dispatch.

### H. Where Payment Link execution is currently invoked
- **File:** `backend/app/services/recovery/executor.py` (`RecoveryExecutor.execute_recovery`, lines 222–233).
- **Invocation:** Calls `RazorpayClient.create_payment_link` with deterministic `reference_id`, authoritative case amount, and calculated expiry. Includes 408 timeout reconciliation.

### I. Whether these components already have service methods that can be composed together
- **YES.** All necessary service methods are complete, robust, and tested:
  1. `AIRecoveryPlanner.plan_recovery(case_or_id)` $\to$ `PlannerResult` (transitions `DETECTED` $\to$ `DIAGNOSING` $\to$ `PLAN_GENERATED` or fail-closed `ESCALATED`).
  2. `RecoveryExecutor.execute_recovery(case_or_id, plan, policy)` $\to$ `ExecutionResult` (evaluates Policy $\to$ transitions to `POLICY_APPROVED` or `POLICY_REJECTED` $\to$ verifies ExecutionGuard $\to$ transitions to `EXECUTING` $\to$ creates Razorpay link $\to$ updates `RecoveryAction` to `ISSUED` and case to `AWAITING_PAYMENT`).

### J. Whether an orchestration service already partially exists
- The individual stage engines exist (`AIRecoveryPlanner` and `RecoveryExecutor`), but the bridge connecting `handle_payment_failed` / `DETECTED` case to `AIRecoveryPlanner` and then to `RecoveryExecutor` does not exist.
- `WebhookDispatcher` handles webhook routing, and `test_mocked_recovery_pipeline.py` manually chained these components in a test harness.

### K. Whether invoking planner/executor from webhook processing could create transaction, async, retry, or background-task problems
- **Analysis:**
  - **FastAPI BackgroundTasks:** In `webhooks.py`, the webhook endpoint returns HTTP 200 immediately after signature verification and DB event insertion, then queues `process_webhook_event_background(event_id, database_url)`.
  - **Session Isolation:** `process_webhook_event_background` creates a fresh, isolated `AsyncSession` from the session factory (`async with session_factory() as session:`).
  - **Transaction Management:** Both `AIRecoveryPlanner` and `RecoveryExecutor` use explicit row locking (`get_by_id_for_update`) and commit state transitions via `update_status` / `append_audit`.
  - **Idempotency:** If a duplicate webhook event arrives, `raw_webhook_events` deduplicates it via `event_id` unique index and `process_webhook_event_background` is not enqueued. If `process_event` is called on an already-processed event, `event.is_processed` exits early. If an orchestration attempt is made on a case already in `AWAITING_PAYMENT`, `RECOVERED`, `EXECUTING`, etc., the state machine and `ExecutionGuard` check #11 / #13 protect against duplicate actions.
  - **Resource Cleanup:** `RazorpayClient` must be properly closed when `WebhookDispatcher` finishes (which is already implemented in `WebhookDispatcher.close()`).

---

## 3. Current Flow vs. Missing Connection

### Current Flow:
```
Razorpay (payment.failed)
       │
       ▼
POST /api/v1/webhooks/razorpay (FastAPI endpoint)
       │ (HMAC signature verified, event stored in raw_webhook_events)
       ├─────────────────────────────────────────► HTTP 200 OK to Razorpay
       ▼ (FastAPI BackgroundTask)
process_webhook_event_background(event_id)
       │
WebhookDispatcher.process_event(event_id)
       │
RecoveryCaseService.handle_payment_failed(normalized_event)
       │
ReconciliationService.reconcile_payment(payment_id)
       │ (confirmed failed)
RecoveryCase created in DETECTED + audit entry logged
       │
       ▼
    [STOPPED — GAP]
```

### Connected Golden Path Flow:
```
RecoveryCase created in DETECTED
       │
       ▼
RecoveryOrchestrator.orchestrate_case(case)
       │
AIRecoveryPlanner.plan_recovery(case)
       ├─► ContextEngine.build_context(case)
       ├─► Status: DIAGNOSING
       ├─► AIProvider.generate_plan(context) + Bounded Correction
       ├─► Persist AIDiagnosis
       └─► Status: PLAN_GENERATED  (or fail-closed ESCALATED -> HALT)
       │
       ▼
RecoveryExecutor.execute_recovery(case, plan)
       ├─► PolicyEngine.evaluate(case, plan, policy)
       │     └─► If REJECT: Status: POLICY_REJECTED -> HALT
       │     └─► If ALLOW: Status: POLICY_APPROVED
       │           └─► If DO_NOT_RECOVER: HALT (non-financial)
       ├─► ExecutionGuard.verify(case, plan, decision, ...)
       │     └─► If FAIL: Status: ESCALATED -> HALT
       │     └─► If PASS: Status: EXECUTING
       ├─► RecoveryAction created (PENDING)
       ├─► RazorpayClient.create_payment_link(...) + 408 reconciliation
       └─► RecoveryAction -> ISSUED, RecoveryCase -> AWAITING_PAYMENT
```

---

## 4. Recommended Orchestration Bridge Architecture

### 1. New Component: `RecoveryOrchestrator`
- **Location:** `backend/app/services/recovery/orchestrator.py`
- **Responsibilities:**
  1. Accepts `case_or_id` and optional `merchant_policy`.
  2. Acquires row lock and verifies the case is in an orchestratable state (`DETECTED` or `PLAN_GENERATED`).
  3. Invokes `AIRecoveryPlanner.plan_recovery(case)`.
  4. If planner fails or transitions to `ESCALATED`, returns `OrchestrationResult(success=False, stage="PLANNING")`.
  5. If planner succeeds, invokes `RecoveryExecutor.execute_recovery(case, plan, policy)`.
  6. Returns unified `OrchestrationResult(success=..., case=..., plan=..., action=..., decision=...)`.

### 2. Integration Point: `RecoveryCaseService.handle_payment_failed`
- When a `RecoveryCase` is newly created (or in `DETECTED`), invoke `orchestrator.orchestrate_case(case)`.
- Pass dependencies cleanly (reuse existing session and `RazorpayClient`).

### 3. `payment_link.paid` Resolution:
- Already implemented in `RecoveryCaseService.handle_payment_link_paid`.
- Verifies `payment_link_status == 'paid'`, `payment_status == 'captured'`, matches authoritative `amount` and `currency`.
- Transitions `RecoveryAction` to `PAID` and `RecoveryCase` to `RECOVERED`.
- Protected terminal state invariant ensures late webhooks do not mutate `RECOVERED`.

---

## 5. Idempotency & Concurrency Strategy
1. **DB Row-Level Locking:** `RecoveryCaseRepository.get_by_id_for_update` prevents race conditions.
2. **State Guard:** Orchestration only proceeds if the case is not in a terminal or active execution state.
3. **Execution Guard Rule #11:** Checks if an active `RecoveryAction` (`PENDING` or `ISSUED`) already exists for the case and blocks duplicate link generation.
4. **Deterministic Reference ID:** `PHX_{case_id_hex[:8]}_{sequence}` ensures Razorpay-level deduplication.

---

## 6. Failure & Fail-Closed Invariants
- **AI Failure / Timeout / Schema Error:** Fails closed $\to$ case transitions to `ESCALATED`, audit logged, no external financial calls made.
- **Policy Rejection:** Fails closed $\to$ case transitions to `POLICY_REJECTED`, audit logged, no payment link created.
- **Execution Guard Violation:** Fails closed $\to$ case transitions to `ESCALATED`, audit logged, no payment link created.
- **Razorpay API Error:** If timeout (408) $\to$ timeout reconciliation via reference ID; if failed $\to$ `RecoveryAction` set to `FAILED`, `RecoveryCase` set to `FAILED`.
- **Zero Direct LLM Execution:** The AI provider produces structured data only; `RecoveryExecutor` deterministically calls Razorpay.

---

## 7. Test Strategy & Plan (Covering Tests 1–16)
1. **Unit & Integration Tests for `RecoveryOrchestrator`:**
   - Test 1: `DETECTED` recovery case triggers orchestration end-to-end.
   - Test 2: Context Engine is invoked with sanitized telemetry.
   - Test 3: AI Planner generates valid structured plan.
   - Test 4: Policy Engine evaluates and validates plan.
   - Test 5: Execution Guard authorizes valid execution.
   - Test 6: Payment Link execution is invoked through executor.
   - Test 7: Case moves to `AWAITING_PAYMENT` with `ISSUED` `RecoveryAction`.
   - Test 8: AI failure fails closed to `ESCALATED` with zero Razorpay calls.
   - Test 9: Policy rejection transitions case to `POLICY_REJECTED` with zero Razorpay calls.
   - Test 10: Execution Guard failure transitions case to `ESCALATED` with zero Razorpay calls.
   - Test 11: Duplicate webhook event does not create duplicate recovery action.
   - Test 12: Duplicate orchestration call is idempotent and safe.
   - Test 13: Razorpay execution timeout triggers reconciliation.
   - Test 14: `payment_link.paid` resolves case to `RECOVERED` and action to `PAID`.
   - Test 15: Duplicate `payment_link.paid` is idempotent on terminal `RECOVERED`.
   - Test 16: All 107 existing Wave 1–3 regression tests remain green.

---

## 8. Database / Schema Impact
- **No Schema Changes Required.** All models (`RecoveryCase`, `RecoveryAction`, `AIDiagnosis`, `AuditLog`, `WebhookEvent`) and repositories already contain all necessary columns, enums, indexes, and constraints.
- **Alembic migration head remains `0003_recovery_actions`.**
