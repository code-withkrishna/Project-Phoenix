"""Inspect Razorpay Test Mode setup and webhooks."""

import asyncio
import httpx
from app.core.config import get_settings


async def inspect_razorpay_account():
    settings = get_settings()
    auth = (settings.razorpay_key_id, settings.razorpay_key_secret)
    async with httpx.AsyncClient(base_url="https://api.razorpay.com", auth=auth) as client:
        # Check orders
        resp = await client.get("/v1/orders", params={"count": 5})
        print("ORDERS STATUS:", resp.status_code)
        if resp.status_code == 200:
            print("ORDERS COUNT:", resp.json().get("count"))

        # Check payment links
        resp = await client.get("/v1/payment_links", params={"count": 5})
        print("PAYMENT LINKS STATUS:", resp.status_code)
        if resp.status_code == 200:
            print("PAYMENT LINKS COUNT:", resp.json().get("count"))


if __name__ == "__main__":
    asyncio.run(inspect_razorpay_account())
