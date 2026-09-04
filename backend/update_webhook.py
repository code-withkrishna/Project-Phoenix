"""Update Razorpay Webhook URL to full path."""

import asyncio
import httpx
from app.core.config import get_settings


async def update_webhook():
    settings = get_settings()
    auth = (settings.razorpay_key_id, settings.razorpay_key_secret)
    webhook_id = "TSgmNNS8ZC5buA"
    target_url = "https://figment-happier-rust.ngrok-free.dev/api/v1/webhooks/razorpay"
    
    async with httpx.AsyncClient(base_url="https://api.razorpay.com", auth=auth, timeout=30.0) as client:
        # Check current webhook
        resp = await client.get(f"/v1/webhooks/{webhook_id}")
        print("GET WEBHOOK:", resp.status_code)

        
        # Update URL
        payload = {
            "url": target_url,
            "secret": settings.razorpay_webhook_secret,
            "events": {
                "payment.failed": True,
                "payment.authorized": True,
                "payment.captured": True,
                "payment_link.paid": True,
                "payment_link.cancelled": True,
                "payment_link.expired": True,
            }
        }
        resp_update = await client.put(f"/v1/webhooks/{webhook_id}", json=payload)
        print("PUT WEBHOOK:", resp_update.status_code)
        if resp_update.status_code == 200:
            print("UPDATED WEBHOOK URL:", resp_update.json().get("url"))
        else:
            print("UPDATE ERROR:", resp_update.text)


if __name__ == "__main__":
    asyncio.run(update_webhook())
