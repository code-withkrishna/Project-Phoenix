"""Check webhook endpoint via Razorpay API."""

import asyncio
import httpx
from app.core.config import get_settings


async def check_webhooks():
    settings = get_settings()
    auth = (settings.razorpay_key_id, settings.razorpay_key_secret)
    async with httpx.AsyncClient(base_url="https://api.razorpay.com", auth=auth) as client:
        resp = await client.get("/v1/webhooks")
        print("WEBHOOKS API STATUS:", resp.status_code)
        if resp.status_code == 200:
            print("WEBHOOKS DATA:", resp.json())
        else:
            print("WEBHOOKS API RESPONSE:", resp.text)


if __name__ == "__main__":
    asyncio.run(check_webhooks())
