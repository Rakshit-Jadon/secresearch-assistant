"""
Security Research Assistant — Flask backend (LOCAL ONLY).
API keys live in .env — never hardcode them here.
Do NOT expose port 5000 publicly.

Providers (priority order):
  1. Gemini Flash      — primary orchestrator
  2. Groq Qwen3-27B   — fast coding / loops
  3. Groq OSS-120B    — strong reasoning
  4. Cloudflare Qwen  — high-volume fallback

Use provider="auto" to let the ModelRouter pick automatically.
"""
import os, json, re, datetime, logging
from pathlib import Path
from datetime import timezone

from flask import Flask, request, jsonify, send_from_directory
from dotenv import load_dotenv
import requests as req

from model_router.router import ModelRouter, Provider
from model_router.providers import gemini as p_gemini
from model_router.providers import groq    as p_groq
from model_router.providers import cloudflare as p_cf

load_dotenv(override=True)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

# ── Singleton router ─────────────────────────────────────────────────────────
router = ModelRouter()

# ── Placeholder-key guard ────────────────────────────────────────────────────
_PLACEHOLDER_RE = re.compile(
    r'^(your[_\-]?|xxx+|placeholder|changeme|<|sk-xxx|gsk_xxx)',
    re.IGNORECASE,
)

# ── Manual provider config (for explicit overrides) ──────────────────────────
# VALUES are env-var *names*, never keys.
MANUAL_PROVIDERS = {
    "gemini":     "GOOGLE_AI_STUDIO_API_KEY",
    "groq":       "GROQ_API_KEY",
    "nvidia":     "NVIDIA_API_KEY",
    "cloudflare": "CLOUDFLARE_API_KEY",
}

# Manual model defaults (when not routed)
MANUAL_MODELS = {
    "gemini":     "gemini-2.0-flash",
    "groq":       "llama3-70b-8192",
    "nvidia":     "meta/llama-3.1-70b-instruct",
    "cloudflare": "@cf/qwen/qwen3-8b",
}

app = Flask(__name__, static_folder="static", static_url_path="/static")


def get_key(env_var: str) -> str:
    val = os.getenv(env_var, "").strip()
    return "" if (not val or _PLACEHOLDER_RE.match(val)) else val


# ── System prompt ─────────────────────────────────────────────────────────────
def load_system_prompt() -> str:
    p = Path("SYSTEM_PROMPT.md")
    return p.read_text(encoding="utf-8").strip() if p.exists() else (
        "You are a helpful security research assistant. "
        "Always follow scope and authorization rules."
    )


# ── Audit log ─────────────────────────────────────────────────────────────────
AUDIT_FILE = Path("audit_log.jsonl")


def audit(entry: dict) -> None:
    try:
        entry["ts"] = datetime.datetime.now(timezone.utc).isoformat()
        with AUDIT_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/providers")
def api_providers():
    """Manual provider key status — boolean only, no key values."""
    return jsonify({p: bool(get_key(v)) for p, v in MANUAL_PROVIDERS.items()})


@app.route("/api/router-status")
def api_router_status():
    """Live ModelRouter quota / cooldown snapshot."""
    return jsonify(router.status())


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data     = request.get_json(force=True, silent=True) or {}
    provider = data.get("provider", "auto")   # "auto" | "gemini" | "groq" | etc.
    messages = data.get("messages", [])

    last_user = next(
        (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
    )
    system_text = load_system_prompt()

    # ── AUTO mode: let the router pick ───────────────────────────────────────
    if provider == "auto":
        return _handle_auto(messages, system_text, last_user)

    # ── MANUAL override ───────────────────────────────────────────────────────
    return _handle_manual(provider, messages, system_text, last_user)


# ── Auto-router logic ─────────────────────────────────────────────────────────
def _handle_auto(messages, system_text, last_user):
    """Try providers in priority order; fallback on failure."""
    tried = []
    while True:
        entry = router.pick()
        if entry is None:
            msg = f"All providers exhausted or rate-limited. Tried: {tried}"
            audit({"mode": "auto", "user_msg": last_user[:200], "error": msg})
            return jsonify({"error": msg}), 503

        provider_enum = entry["provider"]
        tried.append(entry["display"])

        try:
            reply = _dispatch(
                api_id      = entry["api_id"],
                model_name  = entry["model_name"],
                system_text = system_text,
                messages    = messages,
            )
            router.mark_used(provider_enum)
            audit({
                "mode":       "auto",
                "provider":   entry["display"],
                "model":      entry["model_name"],
                "user_msg":   last_user[:200],
                "reply":      reply[:500],
            })
            return jsonify({
                "reply":    reply,
                "model":    entry["model_name"],
                "provider": entry["display"],
                "mode":     "auto",
            })

        except req.HTTPError as exc:
            status  = exc.response.status_code
            reason  = "rate_limit" if status in (429, 503) else "http_error"
            router.mark_failed(provider_enum, reason=f"HTTP {status}")
            log.warning("Auto-router: %s failed HTTP %d, trying next.", entry["display"], status)

        except Exception as exc:
            router.mark_failed(provider_enum, reason=repr(exc)[:80])
            log.warning("Auto-router: %s failed (%s), trying next.", entry["display"], exc)


# ── Manual provider logic ─────────────────────────────────────────────────────
def _handle_manual(provider, messages, system_text, last_user):
    if provider not in MANUAL_PROVIDERS:
        return jsonify({"error": f"Unknown provider '{provider}'."}), 400

    api_key = get_key(MANUAL_PROVIDERS[provider])
    if not api_key:
        env_var = MANUAL_PROVIDERS[provider]
        return jsonify({
            "error": (
                f"No API key configured for '{provider}'. "
                f"Add {env_var} to your .env file and restart."
            )
        }), 503

    model_name = MANUAL_MODELS[provider]

    try:
        reply = _dispatch(
            api_id      = provider,
            model_name  = model_name,
            system_text = system_text,
            messages    = messages,
        )
    except req.HTTPError as exc:
        detail = f"Upstream HTTP {exc.response.status_code}: {exc.response.text[:400]}"
        audit({"provider": provider, "user_msg": last_user[:200], "error": detail})
        return jsonify({"error": detail}), 502
    except req.Timeout:
        audit({"provider": provider, "user_msg": last_user[:200], "error": "timeout"})
        return jsonify({"error": "Request timed out (90 s)."}), 504
    except Exception as exc:
        detail = repr(exc)
        audit({"provider": provider, "user_msg": last_user[:200], "error": detail})
        return jsonify({"error": f"Unexpected error: {detail}"}), 500

    audit({"mode": "manual", "provider": provider, "model": model_name,
           "user_msg": last_user[:200], "reply": reply[:500]})
    return jsonify({
        "reply":    reply,
        "model":    model_name,
        "provider": provider,
        "mode":     "manual",
    })


# ── Provider dispatcher ───────────────────────────────────────────────────────
def _dispatch(api_id: str, model_name: str, system_text: str, messages: list) -> str:
    """Route to the correct provider module."""

    if api_id == "gemini":
        key = get_key("GOOGLE_AI_STUDIO_API_KEY")
        if not key:
            raise ValueError("GOOGLE_AI_STUDIO_API_KEY not configured.")
        return p_gemini.call(key, model_name, system_text, messages)

    elif api_id == "groq":
        key = get_key("GROQ_API_KEY")
        if not key:
            raise ValueError("GROQ_API_KEY not configured.")
        return p_groq.call(key, model_name, system_text, messages)

    elif api_id == "nvidia":
        key = get_key("NVIDIA_API_KEY")
        if not key:
            raise ValueError("NVIDIA_API_KEY not configured.")
        # NVIDIA also uses OpenAI-compat shape
        full_messages = [{"role": "system", "content": system_text}] + messages
        resp = req.post(
            "https://integrate.api.nvidia.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model_name, "messages": full_messages,
                  "max_tokens": 2048, "temperature": 0.3},
            timeout=90,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    elif api_id == "cloudflare":
        key = get_key("CLOUDFLARE_API_KEY")
        if not key:
            raise ValueError("CLOUDFLARE_API_KEY not configured.")
        return p_cf.call(key, model_name, system_text, messages)

    else:
        raise ValueError(f"Unknown api_id '{api_id}'.")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    configured_manual = [p for p, v in MANUAL_PROVIDERS.items() if get_key(v)]
    router_status     = router.status()
    print("=" * 64)
    print("  Security Research Assistant  —  LOCAL ONLY")
    print("  http://127.0.0.1:5000")
    print("  Do NOT expose this port publicly.")
    print("=" * 64)
    print(f"  Manual providers : {configured_manual or ['none']}")
    print()
    print("  Auto-Router (priority order):")
    for p, info in router_status.items():
        key_ok = bool(
            get_key("GOOGLE_AI_STUDIO_API_KEY") if "gemini" in p else
            get_key("GROQ_API_KEY")              if "groq"   in p else
            get_key("CLOUDFLARE_API_KEY")
        )
        status_sym = "[OK]" if key_ok else "[--] (no key)"
        print(f"    [{info['priority']}] {info['display']:22s} {info['model']:40s} {status_sym}")
    print()
    app.run(host="127.0.0.1", port=5000, debug=False)
