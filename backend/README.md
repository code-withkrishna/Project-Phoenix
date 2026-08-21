# Project Phoenix — Backend

FastAPI backend for Project Phoenix revenue recovery orchestration.

## Stack

- Python 3.11+
- FastAPI + Uvicorn
- SQLAlchemy 2.x (async) + asyncpg
- Pydantic v2 + pydantic-settings
- PostgreSQL 15+
- Alembic
- pytest + pytest-asyncio + httpx + respx

## Setup

```powershell
cd backend
C:\Users\LENOVO\AppData\Local\Programs\Python\Python311\python.exe -m pip install -r requirements.txt
copy .env.example .env
```

Edit `.env`:

| Variable | Description |
| :--- | :--- |
| `DATABASE_URL` | PostgreSQL async URL (`postgresql+asyncpg://...`) |
| `RAZORPAY_KEY_ID` | Razorpay Test Mode key (`rzp_test_*`) |
| `RAZORPAY_KEY_SECRET` | Razorpay Test Mode secret |
| `RAZORPAY_WEBHOOK_SECRET` | Webhook signing secret from Razorpay Dashboard |

## Database Migrations

```powershell
python -m alembic upgrade head
python -m alembic history
```

## Run

```powershell
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Health Check

```powershell
curl http://127.0.0.1:8000/health
```

Expected:

```json
{"status":"ok","service":"phoenix-backend"}
```

## Razorpay Test Mode Webhook Setup

1. Log in to [Razorpay Dashboard](https://dashboard.razorpay.com) → **Test Mode**.
2. Go to **Settings → Webhooks** and create a webhook:
   - **URL:** `https://<your-tunnel-host>/api/v1/webhooks/razorpay`
   - **Events:** `payment.failed` (and later `payment_link.paid`)
3. Copy the webhook secret into `RAZORPAY_WEBHOOK_SECRET` in `.env`.
4. Use **ngrok** or similar to expose local port 8000:

```powershell
ngrok http 8000
```

Set the ngrok HTTPS URL in the Razorpay webhook configuration.

## Wave 1 API Endpoints

| Method | Path | Description |
| :--- | :--- | :--- |
| `GET` | `/health` | Service liveness |
| `POST` | `/api/v1/webhooks/razorpay` | Inbound Razorpay webhook gateway |
| `GET` | `/api/v1/recovery-cases` | List recovery cases (filters: `status`, `payment_id`) |
| `GET` | `/api/v1/recovery-cases/{case_id}` | Recovery case detail with audit trail |

## Tests

Integration tests use in-memory SQLite by default. Override with PostgreSQL:

```powershell
$env:TEST_DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/phoenix_test"
python -m pytest -v
```

## Project Layout

```
backend/
  app/
    api/v1/          # health, webhooks, recovery_cases
    core/            # config, database, logging, security
    models/          # raw_webhook_events, recovery_cases, audit_logs
    repositories/    # data access layer
    schemas/         # Pydantic request/response models
    services/
      razorpay/      # client, verifier, reconciliation
      webhooks/      # ingestion, normalization, dispatcher
      recovery/      # case_service
  alembic/           # migrations
  tests/
    fixtures/        # TEST FIXTURE Razorpay payloads
    unit/
    integration/
```

## Wave 1 Scope

Implemented:

- HMAC-SHA256 webhook verification on raw body
- Idempotent webhook ingestion (`raw_webhook_events`)
- Background processing via FastAPI `BackgroundTasks`
- Authoritative payment reconciliation (`GET /v1/payments/{id}`)
- Idempotent recovery case creation (`DETECTED` state)
- Recovery case list/detail REST APIs
- Immutable audit log entries
- Alembic migration for Wave 1 schema

Not implemented (later waves):

- AI Recovery Planner, Policy Engine, Payment Link execution
- Dashboard frontend, simulation endpoints, HITL overrides
