# Razorpay Integration Specification — Project Phoenix

This document details the exact integration mechanics between Project Phoenix and the **Razorpay API (Test Mode)**.

---

## 1. Credentials & Configuration

Phoenix requires three environment variables for Razorpay operations:

```env
RAZORPAY_KEY_ID="rzp_test_XXXXXXXXXXXXXX"      # Razorpay Test Key ID
RAZORPAY_KEY_SECRET="XXXXXXXXXXXXXXXXXXXXXXXX"   # Razorpay Test Key Secret
RAZORPAY_WEBHOOK_SECRET="XXXXXXXXXXXXXXXXXXXX" # Shared Webhook Secret configured in Razorpay Dashboard
```

> [!CAUTION]
> Live mode keys (`rzp_live_*`) are strictly disallowed in development and Buildathon evaluation.

---

## 2. Webhook Signature Verification & Deduplication

Every incoming webhook request to `/api/v1/webhooks/razorpay` must be verified using HMAC-SHA256 and deduplicated using `x-razorpay-event-id`.

### 2.1 Required Inbound Headers
- `X-Razorpay-Signature`: Hex-encoded HMAC-SHA256 signature calculated over raw request body using `RAZORPAY_WEBHOOK_SECRET`.
- `X-Razorpay-Event-Id`: Unique event identifier emitted by Razorpay, used for database deduplication.

### 2.2 Verification Algorithm (Python)
```python
import hmac
import hashlib

def verify_razorpay_webhook_signature(
    raw_body: bytes,
    signature: str,
    webhook_secret: str
) -> bool:
    """
    Verifies that the incoming webhook originated from Razorpay.
    Comparison is done using hmac.compare_digest to prevent timing attacks.
    """
    if not signature or not webhook_secret:
        return False
        
    expected_signature = hmac.new(
        key=webhook_secret.encode('utf-8'),
        msg=raw_body,
        digestmod=hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(expected_signature, signature)
```

---

## 3. Webhook Payloads & Event Schemas

### 3.1 `payment.failed` Event Structure
When a customer's payment fails, Razorpay fires `payment.failed`.

```json
{
  "entity": "event",
  "account_id": "acc_1234567890",
  "event": "payment.failed",
  "contains": ["payment"],
  "payload": {
    "payment": {
      "entity": {
        "id": "pay_O7f84mK12nABcd",
        "entity": "payment",
        "amount": 499900,
        "currency": "INR",
        "status": "failed",
        "order_id": "order_O7f81xP89mKLno",
        "invoice_id": null,
        "international": false,
        "method": "upi",
        "amount_refunded": 0,
        "refund_status": null,
        "captured": false,
        "description": "Purchase on Phoenix Store",
        "card_id": null,
        "bank": null,
        "wallet": null,
        "vpa": "customer@oksbi",
        "email": "customer@example.com",
        "contact": "+919876543210",
        "customer_id": "cust_C123456",
        "notes": {
          "cart_id": "cart_88321"
        },
        "fee": null,
        "tax": null,
        "error_code": "BAD_REQUEST_ERROR",
        "error_description": "Payment was cancelled by the user on the bank/UPI app",
        "error_source": "customer",
        "error_step": "payment_authentication",
        "error_reason": "payment_cancelled",
        "created_at": 1755765000
      }
    }
  },
  "created_at": 1755765001
}
```

### 3.2 `payment_link.paid` Event Structure
When the customer completes payment through the Phoenix-generated payment link.

```json
{
  "entity": "event",
  "account_id": "acc_1234567890",
  "event": "payment_link.paid",
  "contains": ["payment_link", "payment"],
  "payload": {
    "payment_link": {
      "entity": {
        "id": "plink_O7f92zL34mUVwx",
        "accept_partial": false,
        "amount": 499900,
        "amount_paid": 499900,
        "currency": "INR",
        "description": "Complete your order with instant 1-click payment.",
        "reference_id": "phx_rec_01J8F9X2Q9Z8K3V01N5A7B8C9D_1",
        "short_url": "https://rzp.io/i/Xyz12345",
        "status": "paid",
        "customer": {
          "name": "Customer Name",
          "email": "customer@example.com",
          "contact": "+919876543210"
        },
        "notes": {
          "phoenix_case_id": "01J8F9X2Q9Z8K3V01N5A7B8C9D",
          "original_payment_id": "pay_O7f84mK12nABcd"
        },
        "created_at": 1755765005,
        "updated_at": 1755765730
      }
    },
    "payment": {
      "entity": {
        "id": "pay_O7f99kL99mQWrt",
        "amount": 499900,
        "currency": "INR",
        "status": "captured",
        "order_id": "order_O7f92zL34mUVwx_order",
        "method": "card"
      }
    }
  },
  "created_at": 1755765731
}
```

---

## 4. Razorpay REST Endpoints Used

### 4.1 Authoritative Payment Status Lookup
`GET https://api.razorpay.com/v1/payments/{payment_id}`
- **Authentication:** HTTP Basic Auth (`RAZORPAY_KEY_ID` : `RAZORPAY_KEY_SECRET`)
- **Usage:** Pre-execution reconciliation check to ensure transaction has not transitioned to `captured` or `authorized`.

### 4.2 Create Standard Payment Link
`POST https://api.razorpay.com/v1/payment_links`
- **Authentication:** HTTP Basic Auth
- **Enforced Constraints:**
  - `expire_by`: Must be a Unix timestamp at least **15 minutes** in the future.
  - `reference_id`: Unique merchant reference key (`phx_rec_<case_id>_<action_id>`).
- **Request Body:**
```json
{
  "amount": 499900,
  "currency": "INR",
  "accept_partial": false,
  "reference_id": "phx_rec_01J8F9X2Q9Z8K3V01N5A7B8C9D_1",
  "description": "Complete your order with instant 1-click payment.",
  "customer": {
    "name": "Customer Name",
    "contact": "+919876543210",
    "email": "customer@example.com"
  },
  "notify": {
    "sms": true,
    "email": true
  },
  "reminder_enable": false,
  "expire_by": 1755766800,
  "notes": {
    "phoenix_case_id": "01J8F9X2Q9Z8K3V01N5A7B8C9D",
    "original_payment_id": "pay_O7f84mK12nABcd"
  }
}
```

### 4.3 Cancel Payment Link
`POST https://api.razorpay.com/v1/payment_links/{payment_link_id}/cancel`
- **Authentication:** HTTP Basic Auth
- **Usage:** Cancels an active payment link if the customer pays through another channel or if an operator cancels the case.

---

## 5. Razorpay Error Taxonomy & Reason Mapping

Razorpay standardizes error entities in failed payments into:

| Error Code | Common Reasons | Recommended Phoenix AI Diagnostic |
| :--- | :--- | :--- |
| `BAD_REQUEST_ERROR` | `payment_cancelled`, `incorrect_pin`, `otp_timeout` | **USER_FRICTION** — High probability of recovery via immediate reminder link. |
| `GATEWAY_ERROR` | `bank_technical_error`, `gateway_timeout`, `issuer_down` | **TECHNICAL_GATEWAY_ERROR** — Recoverable; advise alternative payment method (e.g. Card if UPI bank is down). |
| `BAD_REQUEST_ERROR` | `insufficient_funds` | **INSUFFICIENT_FUNDS** — Recoverable with delayed expiry (e.g., 24h) or alternative payment instrument. |
| `BAD_REQUEST_ERROR` | `card_expired`, `invalid_card_details`, `international_not_enabled` | **INSTRUMENT_INVALID** — Recoverable by presenting full alternative payment gateway (Netbanking/UPI). |

---

## 6. Official Razorpay Integration Standards & Demo Protocol

> [!IMPORTANT]
> **Buildathon Demo Protocol:**
> 1. **Live Test Mode Transactions Only:** The official demonstration must execute against genuine Razorpay Test Mode checkout interactions (triggering actual `payment.failed` webhooks via test cards/UPI, receiving and paying genuine Razorpay Payment Links, and processing actual `payment_link.paid` webhooks).
> 2. **Simulation Endpoints:** `/api/v1/simulation/*` endpoints are explicitly isolated for CI/CD unit/integration testing and failure injection stress tests, never as a substitute for real Razorpay Test Mode behavior during evaluation.
> 3. **Confirmed Specifications:**
>    - `expire_by` minimum timestamp is strictly 15 minutes (enforced in Policy Engine).
>    - `x-razorpay-event-id` is the authoritative unique identifier for deduplicating webhook events.
