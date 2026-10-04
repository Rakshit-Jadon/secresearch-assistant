"""
model_router/providers/gemini.py
Gemini generateContent — system prompt via systemInstruction,
assistant role mapped to "model".
"""
import os
import requests


def call(api_key: str, model_name: str, system_text: str, messages: list) -> str:
    url = (
        f"https://generativelanguage.googleapis.com"
        f"/v1beta/models/{model_name}:generateContent?key={api_key}"
    )
    contents = []
    for m in messages:
        role = "model" if m.get("role") == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})

    body = {
        "systemInstruction": {"parts": [{"text": system_text}]},
        "contents": contents,
        "generationConfig": {"maxOutputTokens": 2048, "temperature": 0.3},
    }
    resp = requests.post(url, json=body, timeout=90)
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]
