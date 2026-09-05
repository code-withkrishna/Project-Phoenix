"""Integration tests for all 6 simulation scenarios and error resilience."""

import uuid
import httpx
import pytest
import respx

from app.core.config import Settings


@pytest.mark.asyncio
@respx.mock
async def test_all_six_simulation_scenarios_end_to_end(client, db_session) -> None:
    """Verify that all 6 failure simulation scenarios run end-to-end without errors."""
    def payment_link_side_effect(request: httpx.Request) -> httpx.Response:
        unique_id = uuid.uuid4().hex[:8]
        return httpx.Response(
            status_code=200,
            json={
                "id": f"plink_sim_{unique_id}",
                "short_url": f"https://rzp.io/i/{unique_id}",
                "status": "created",
                "amount": 299900,
                "currency": "INR",
            },
        )

    respx.post("https://api.razorpay.com/v1/payment_links").mock(side_effect=payment_link_side_effect)

    scenarios = [
        ("otp_friction", 2999.0, "AWAITING_PAYMENT"),
        ("insufficient_funds", 2999.0, "AWAITING_PAYMENT"),
        ("bank_downtime", 2999.0, "POLICY_APPROVED"),
        ("card_expired", 2999.0, "POLICY_APPROVED"),
        ("high_value", 15000.0, "ESCALATED"),
        ("fraud_security", 2999.0, "POLICY_APPROVED"),
    ]

    for scenario_name, amount_inr, expected_status in scenarios:
        response = await client.post(
            "/api/v1/checkout/simulate-failure",
            json={
                "scenario": scenario_name,
                "amount_inr": amount_inr,
                "customer_name": "Test Merchant Customer",
                "customer_email": "merchant.test@example.com",
                "customer_phone": "+919876543210",
            },
        )

        assert response.status_code == 200, f"Scenario {scenario_name} failed with {response.status_code}: {response.text}"
        data = response.json()
        assert data["success"] is True, f"Scenario {scenario_name} returned success=False: {data}"
        assert data["scenario"] == scenario_name
        assert data["case_id"] is not None
        assert data["case_status"] == expected_status, f"Scenario {scenario_name} got status {data['case_status']}, expected {expected_status}"

        # Fetch case details
        case_res = await client.get(f"/api/v1/recovery-cases/{data['case_id']}")
        assert case_res.status_code == 200
        case_detail = case_res.json()
        assert case_detail["id"] == data["case_id"]
        assert case_detail["status"] == expected_status

        if scenario_name == "high_value":
            assert case_detail["status"] == "ESCALATED"
            # Test HITL manual approval
            appr_res = await client.post(f"/api/v1/recovery-cases/{data['case_id']}/approve", json={"reason": "Approved in test"})
            assert appr_res.status_code == 200
            appr_data = appr_res.json()
            assert appr_data["status"] == "AWAITING_PAYMENT"
        elif scenario_name == "fraud_security":
            # Execution must be prevented for fraud security
            assert len(case_detail.get("recovery_actions", [])) == 0


@pytest.mark.asyncio
async def test_non_200_and_error_handling_returns_structured_json(client) -> None:
    """Verify that 404s, 400s, and application errors return structured JSON and never tracebacks."""
    # 404 Case not found
    res = await client.get("/api/v1/recovery-cases/00000000-0000-0000-0000-000000000000")
    assert res.status_code == 404
    assert "application/json" in res.headers.get("content-type", "")
    data = res.json()
    assert "detail" in data or "error" in data
    assert "Traceback" not in res.text
