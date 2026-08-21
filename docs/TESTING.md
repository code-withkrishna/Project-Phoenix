# Testing & Verification Strategy — Project Phoenix

This document outlines the testing architecture, test suites, edge case scenarios, and validation criteria for Project Phoenix.

---

## 1. Testing Pyramid & Philosophy

```
                    ┌─────────────────────────┐
                    │   End-to-End Live Test  │  (Razorpay Test Mode)
                    │    Real Webhook to Link │
                    ├─────────────────────────┤
                    │    Integration Tests    │  (FastAPI TestClient + DB)
                    │  Idempotency & Gateway  │
                    ├─────────────────────────┤
                    │       Unit Tests        │  (Isolated logic)
                    │  HMAC, Policy, Schemas  │
                    └─────────────────────────┘
```

### Core Invariants Tested:
1. **Security:** No unverified webhooks accepted.
2. **Idempotency:** 100% deduplication of duplicate webhook deliveries.
3. **Safety:** Deterministic policy engine fails closed on all violations.
4. **State Machine Integrity:** Zero invalid transitions or deadlocks under out-of-order events.

---

## 2. Test Suites & Coverage Requirements

### 2.1 Unit Test Suite (`tests/unit/`)

| Test File | Focus Area | Key Scenarios |
| :--- | :--- | :--- |
| `test_signature.py` | HMAC-SHA256 Verification | Valid signature, invalid signature, tampered body, missing header, empty secret. |
| `test_state_machine.py` | State Transitions | Valid legal transitions, illegal transition rejection, terminal state immutability. |
| `test_policy_engine.py` | Guardrail Rules | Max retry breach, cooldown window violation, expiry bounds check, amount limits, fail-closed exception test. |
| `test_ai_schemas.py` | Pydantic Schema Validation | Valid structured output, extra fields rejection, missing required fields, enum violation, boundary clamping. |

### 2.2 Integration Test Suite (`tests/integration/`)

| Test File | Focus Area | Key Scenarios |
| :--- | :--- | :--- |
| `test_webhook_gateway.py` | HTTP Ingestion | HTTP 200 on valid webhook, HTTP 401 on bad signature, raw payload persistence in DB. |
| `test_idempotency.py` | Concurrent Replay | Dispatching 10 identical webhook events in parallel; assert exactly 1 case created. |
| `test_out_of_order.py` | Async Ordering | `payment_link.paid` arriving before link execution completes; `payment.captured` canceling open link. |
| `test_orchestrator_pipeline.py` | Mocked End-to-End | Mocking LLM and Razorpay API, verifying full pipeline from `payment.failed` to `AWAITING_PAYMENT`. |

### 2.3 Live Sandbox Test Suite & Buildathon Demo Flow (`tests/e2e/`)

Runs exclusively against real Razorpay Test Mode using configured `rzp_test_*` credentials.

> [!IMPORTANT]
> **Live Demo Integrity Rule:**  
> The final Buildathon demo runs directly through real Razorpay Test Mode checkout flows. It does not use simulated webhook generation buttons.

1. **Step 1:** Customer initiates a checkout on Razorpay Test Mode and triggers a realistic failure (e.g. UPI user cancellation or test failure card).
2. **Step 2:** Razorpay servers deliver genuine signed `payment.failed` webhook to Phoenix (`POST /api/v1/webhooks/razorpay`).
3. **Step 3:** Phoenix verifies HMAC-SHA256 signature, deduplicates via `x-razorpay-event-id`, and reconciles status (`GET /v1/payments/{id}`).
4. **Step 4:** Context Engine extracts telemetry; AI diagnostic generates structured JSON plan.
5. **Step 5:** Policy Engine validates invariants (enforcing $\ge 15\text{m}$ expiry, cooldown, retry count).
6. **Step 6:** Razorpay client calls `POST /v1/payment_links` with `reference_id` (`phx_rec_<case_id>_<action_id>`).
7. **Step 7:** Customer opens the real `short_url` (Razorpay hosted page) and completes test payment.
8. **Step 8:** Razorpay servers deliver genuine `payment_link.paid` webhook.
9. **Step 9:** Phoenix transitions case to `RECOVERED` and writes the immutable audit ledger.

---

## 3. Key Edge Case Scenarios

### Scenario 1: Duplicate Webhook Flood (Idempotency)
```python
@pytest.mark.asyncio
async def test_duplicate_webhook_flood(client, valid_webhook_payload, valid_headers):
    # Send identical payload 5 times concurrently
    tasks = [
        client.post("/api/v1/webhooks/razorpay", content=valid_webhook_payload, headers=valid_headers)
        for _ in range(5)
    ]
    responses = await asyncio.gather(*tasks)
    
    # All must return 200 OK
    for r in responses:
        assert r.status_code == 200
        
    # Verify DB contains only 1 raw_webhook_event and 1 recovery_case
    async with get_test_db() as session:
        events = await session.execute(select(RawWebhookEvent))
        cases = await session.execute(select(RecoveryCase))
        assert len(events.scalars().all()) == 1
        assert len(cases.scalars().all()) == 1
```

### Scenario 2: Policy Engine Fail-Closed on LLM Malformation
```python
def test_policy_fails_closed_on_bad_action(policy_engine, sample_case, sample_policy):
    malformed_plan = RecoveryPlan(
        root_cause_category="USER_FRICTION",
        confidence_score=0.9,
        diagnostic_summary="Valid summary",
        recommended_action="UNAUTHORIZED_MONEY_TRANSFER", # Invalid action
        urgency="HIGH",
        link_expiry_minutes=30,
        customer_facing_message="Pay now"
    )
    result = policy_engine.evaluate(sample_case, malformed_plan, sample_policy, prior_actions=[])
    assert result.decision == "REJECTED"
    assert any("not whitelisted" in v for v in result.violations)
```

---

## 4. Test Fixtures & Mocking Guidelines

1. **No External Network in Unit/Integration Tests:**  
   All tests in `tests/unit/` and `tests/integration/` must use mock LLM clients and mock Razorpay HTTP adapters (`respx` or `unittest.mock`).
2. **Deterministic Timestamps:**  
   Use `freezegun` or explicit UTC timestamp fixtures to verify expiry bounds and cooldown calculations reliably.
3. **Database Isolation:**  
   Integration tests run against a dedicated test database (or transactional rollback session) to ensure test independence.
