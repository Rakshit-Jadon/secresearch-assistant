"""
model_router/providers/cloudflare.py
Cloudflare Workers AI — REST API, result in {"result": {"response": "..."}}
Requires CLOUDFLARE_ACCOUNT_ID in addition to CLOUDFLARE_API_KEY.
"""
import os
import requests


def call(api_key: str, model_name: str, system_text: str, messages: list) -> str:
    account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
    if not account_id:
        raise ValueError(
            "CLOUDFLARE_ACCOUNT_ID is not set in .env. "
            "Add it alongside CLOUDFLARE_API_KEY."
        )
    url = (
        f"https://api.cloudflare.com/client/v4/accounts"
        f"/{account_id}/ai/run/{model_name}"
    )
    full_messages = [{"role": "system", "content": system_text}] + messages
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
        },
        json={"messages": full_messages, "max_tokens": 2048},
        timeout=90,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise ValueError(f"Cloudflare API error: {data.get('errors', [])}")
    return data["result"]["response"]
