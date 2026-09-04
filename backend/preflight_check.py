"""Pre-flight check script for Razorpay Test Mode and PostgreSQL connectivity."""

import asyncio
import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.services.razorpay.client import RazorpayClient


async def check_database():
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    async with engine.connect() as conn:
        res = await conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name"))
        tables = [r[0] for r in res.fetchall()]
        print("DATABASE TABLES:", tables)
        
        # Check current row counts
        for table in ["raw_webhook_events", "recovery_cases", "recovery_actions", "ai_diagnoses", "audit_logs"]:
            if table in tables:
                cnt_res = await conn.execute(text(f"SELECT count(*) FROM {table}"))
                cnt = cnt_res.scalar_one()
                print(f"  {table}: {cnt} rows")
    await engine.dispose()


async def check_razorpay_connection():
    settings = get_settings()
    client = RazorpayClient(settings)
    try:
        # Check Razorpay credentials by calling a read-only endpoint (e.g., listing payments or payment links with limit 1)
        async with httpx.AsyncClient(
            base_url=settings.razorpay_api_base_url,
            auth=(settings.razorpay_key_id, settings.razorpay_key_secret),
        ) as http_client:
            resp = await http_client.get("/v1/payments", params={"count": 1})
            print("RAZORPAY TEST API STATUS:", resp.status_code)
            if resp.status_code == 200:
                data = resp.json()
                print("RAZORPAY AUTH SUCCESSFUL! Payments count returned:", data.get("count", 0))
            else:
                print("RAZORPAY ERROR:", resp.text)
    finally:
        await client.close()


async def main():
    print("--- 1. DATABASE CHECK ---")
    await check_database()
    print("\n--- 2. RAZORPAY API CHECK ---")
    await check_razorpay_connection()


if __name__ == "__main__":
    asyncio.run(main())
