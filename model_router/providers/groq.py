"""
model_router/providers/groq.py
Groq Cloud — OpenAI-compatible chat/completions endpoint.
Supports both Qwen3-27B and OSS-120B via the same API shape.
"""
import requests

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def call(api_key: str, model_name: str, system_text: str, messages: list) -> str:
    full_messages = [{"role": "system", "content": system_text}] + messages
    resp = requests.post(
        GROQ_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
        },
        json={
            "model":       model_name,
            "messages":    full_messages,
            "max_tokens":  2048,
            "temperature": 0.3,
        },
        timeout=90,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]
