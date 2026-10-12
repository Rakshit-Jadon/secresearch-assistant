"""
RakshaLink AI Security Assistant — Flask Backend (LOCAL ONLY)
Integrated with RakshaLink Core 2046 Dashboard.
API keys live in .env — never hardcode them here.
Do NOT expose port 5001 publicly.

Providers (priority order):
  1. Google Gemini Flash   — primary orchestrator
  2. Groq Qwen3-27B       — fast coding / loops
  3. Groq OSS-120B        — strong reasoning
  4. Cloudflare Llama     — high-volume fallback
  5. NVIDIA NIM           — ML/AI specialist

Use provider="auto" to let the ModelRouter pick automatically.
"""
import os, json, re, datetime, logging
from pathlib import Path
from datetime import timezone

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv
import requests as req

from model_router.router import ModelRouter, Provider
from model_router.providers import gemini as p_gemini
from model_router.providers import groq    as p_groq
from model_router.providers import cloudflare as p_cf

load_dotenv(override=True)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("rakshalink-ai")

# ═══ Singleton router ══════════════════════════════════════════════════════
router = ModelRouter()

# ═══ Placeholder-key guard ═════════════════════════════════════════════════
_PLACEHOLDER_RE = re.compile(
    r'^(your[_\-]?|xxx+|placeholder|changeme|<|sk-xxx|gsk_xxx)',
    re.IGNORECASE,
)

# ═══ Manual provider config (for explicit overrides) ═══════════════════════
# VALUES are env-var *names*, never keys.
MANUAL_PROVIDERS = {
    "gemini":     "GOOGLE_AI_STUDIO_API_KEY",
    "groq":       "GROQ_API_KEY",
    "groq_qwen":  "GROQ_API_KEY",
    "groq_oss":   "GROQ_API_KEY",
    "nvidia":     "NVIDIA_API_KEY",
    "cloudflare": "CLOUDFLARE_API_KEY",
}

# Manual model defaults (when not routed)
MANUAL_MODELS = {
    "gemini":     "gemini-2.0-flash",
    "groq":       "llama-3.3-70b-versatile",
    "groq_qwen":  "llama-3.3-70b-versatile",
    "groq_oss":   "llama-3.3-70b-versatile",
    "nvidia":     "meta/llama-3.1-70b-instruct",
    "cloudflare": "@cf/meta/llama-3.1-8b-instruct",
}

# ═══ Flask app setup ════════════════════════════════════════════════════════
BASE_DIR = Path(__file__).parent
app = Flask(__name__, static_folder=str(BASE_DIR / "static"))

# Allow CORS from the RakshaLink dashboard (file:// and localhost)
CORS(app, resources={r"/api/*": {
    "origins": ["*"],  # For local dashboard usage
    "methods": ["GET", "POST", "OPTIONS"],
    "allow_headers": ["Content-Type"],
}})

# ═══ Audit log ══════════════════════════════════════════════════════════════
AUDIT_LOG = BASE_DIR / "audit_log.jsonl"

def audit(data: dict) -> None:
    entry = {
        "ts": datetime.datetime.now(timezone.utc).isoformat(),
        "app": "rakshalink_ai_2046",
        **data,
    }
    with AUDIT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ═══ System prompt loader ════════════════════════════════════════════════════
def load_system_prompt() -> str:
    sp = BASE_DIR / "SYSTEM_PROMPT.md"
    if sp.exists():
        return sp.read_text(encoding="utf-8").strip()
    return (
        "You are RakshaLink AI, India's sovereign cybersecurity intelligence assistant. "
        "Help users understand and defend against UPI fraud, phishing, smishing, "
        "voice deepfakes, and digital arrest scams. Be concise, technical, and actionable."
    )


# ═══ Key helper ═════════════════════════════════════════════════════════════
def get_key(env_var: str) -> str | None:
    v = os.getenv(env_var, "").strip()
    return v if v and not _PLACEHOLDER_RE.match(v) else None


# ═══ Routes ══════════════════════════════════════════════════════════════════

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_static(path):
    if path and (BASE_DIR / "static" / path).exists():
        return send_from_directory(str(BASE_DIR / "static"), path)
    return send_from_directory(str(BASE_DIR / "static"), "index.html")


@app.route("/api/health", methods=["GET"])
def health():
    """Health-check endpoint for the RakshaLink dashboard."""
    configured = {k: bool(get_key(v)) for k, v in MANUAL_PROVIDERS.items()}
    return jsonify({
        "status": "operational",
        "service": "RakshaLink AI Security Intelligence",
        "version": "20.4-LTS",
        "timestamp": datetime.datetime.now(timezone.utc).isoformat(),
        "providers": configured,
        "router": router.status(),
    })


@app.route("/api/router-status", methods=["GET"])
def router_status_endpoint():
    return jsonify(router.status())


@app.route("/api/chat", methods=["POST", "OPTIONS"])
def chat():
    """Main AI chat endpoint — receives messages, returns AI reply."""
    if request.method == "OPTIONS":
        return "", 204

    data = request.get_json(force=True, silent=True) or {}
    messages  = data.get("messages", [])
    provider  = data.get("provider", "auto").strip().lower()

    if not isinstance(messages, list) or not messages:
        return jsonify({"error": "messages array required."}), 400

    # Sanitise: keep only role+content fields
    messages = [
        {"role": m["role"], "content": str(m["content"])[:8000]}
        for m in messages
        if isinstance(m, dict) and m.get("role") in ("user", "assistant")
    ]

    last_user = next(
        (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
    )
    system_text = load_system_prompt()

    # 🔀 AUTO mode: let the router pick ──────────────────────────────────────
    if provider == "auto":
        return _handle_auto(messages, system_text, last_user)

    # 🔧 MANUAL override ─────────────────────────────────────────────────────
    return _handle_manual(provider, messages, system_text, last_user)


# ═══ RakshaLink-specific specialized endpoints ══════════════════════════════

@app.route("/api/analyze-url", methods=["POST", "OPTIONS"])
def analyze_url():
    """Specialized URL phishing analysis endpoint."""
    if request.method == "OPTIONS":
        return "", 204

    data = request.get_json(force=True, silent=True) or {}
    url = str(data.get("url", "")).strip()[:2048]
    if not url:
        return jsonify({"error": "url field required."}), 400

    prompt = (
        f"Analyze this URL for phishing indicators: {url}\n\n"
        "Provide:\n"
        "1. Threat severity: CRITICAL/HIGH/MEDIUM/SAFE\n"
        "2. Key risk indicators detected\n"
        "3. Brand being impersonated (if any)\n"
        "4. Technical indicators (homoglyphs, suspicious TLDs, redirect chains)\n"
        "5. Recommended action for Indian UPI users\n\n"
        "Be concise and technical. Format with bullet points."
    )
    messages = [{"role": "user", "content": prompt}]
    return _run_ai_query(messages, "URL Phishing Analysis", {"url": url[:200]})


@app.route("/api/analyze-sms", methods=["POST", "OPTIONS"])
def analyze_sms():
    """Specialized SMS phishing (smishing) analysis endpoint."""
    if request.method == "OPTIONS":
        return "", 204

    data = request.get_json(force=True, silent=True) or {}
    sms = str(data.get("sms", "")).strip()[:4000]
    if not sms:
        return jsonify({"error": "sms field required."}), 400

    prompt = (
        f"Analyze this SMS message for phishing/smishing indicators:\n\n{sms}\n\n"
        "Provide:\n"
        "1. Threat severity: CRITICAL/HIGH/MEDIUM/SAFE\n"
        "2. Scam type (UPI fraud, OTP theft, fake prize, digital arrest, etc.)\n"
        "3. Social engineering techniques used\n"
        "4. Suspicious keywords and patterns\n"
        "5. What the attacker wants and recommended action\n\n"
        "Context: This is for Indian citizens on the UPI payment network. "
        "Be concise and actionable."
    )
    messages = [{"role": "user", "content": prompt}]
    return _run_ai_query(messages, "SMS Smishing Analysis", {"sms_preview": sms[:100]})


@app.route("/api/analyze-voice-transcript", methods=["POST", "OPTIONS"])
def analyze_voice_transcript():
    """Specialized voice call transcript coercion analysis."""
    if request.method == "OPTIONS":
        return "", 204

    data = request.get_json(force=True, silent=True) or {}
    transcript = str(data.get("transcript", "")).strip()[:6000]
    if not transcript:
        return jsonify({"error": "transcript field required."}), 400

    prompt = (
        f"Analyze this call transcript for social engineering and coercion patterns:\n\n{transcript}\n\n"
        "Provide:\n"
        "1. Coercion threat level: CRITICAL/HIGH/MEDIUM/SAFE\n"
        "2. Scam type (Digital Arrest, CBI impersonation, family distress, customs scam, etc.)\n"
        "3. Psychological manipulation tactics detected\n"
        "4. Urgency and fear triggers identified\n"
        "5. Authority impersonation claims\n"
        "6. Financial demand method (UPI, bank transfer, crypto, etc.)\n"
        "7. Recommended immediate action for the victim\n\n"
        "Context: MHA/I4C reported 45,000+ Digital Arrest scams in India in 2024. "
        "Be direct and protective in your response."
    )
    messages = [{"role": "user", "content": prompt}]
    return _run_ai_query(messages, "Voice Coercion Analysis", {"transcript_preview": transcript[:100]})


@app.route("/api/analyze-qr", methods=["POST", "OPTIONS"])
def analyze_qr():
    """Specialized QR code payload security analysis."""
    if request.method == "OPTIONS":
        return "", 204

    data = request.get_json(force=True, silent=True) or {}
    qr_data = str(data.get("qr_data", "")).strip()[:2048]
    if not qr_data:
        return jsonify({"error": "qr_data field required."}), 400

    prompt = (
        f"Analyze this QR code payload for security threats:\n\n{qr_data}\n\n"
        "Provide:\n"
        "1. Threat severity: CRITICAL/HIGH/MEDIUM/SAFE\n"
        "2. QR type (UPI payment, URL redirect, vCard, WiFi, etc.)\n"
        "3. Malicious indicators (tampered UPI ID, suspicious domains, redirect chains)\n"
        "4. If UPI QR: verify pa= (payee VPA), am= (amount), pn= (payee name) fields\n"
        "5. Recommended action before scanning\n\n"
        "Context: QR payment fraud is a major vector in India targeting UPI users. "
        "Be concise and technical."
    )
    messages = [{"role": "user", "content": prompt}]
    return _run_ai_query(messages, "QR Code Analysis", {"qr_preview": qr_data[:100]})


# ═══ Auto-router logic ═══════════════════════════════════════════════════════
def _run_ai_query(messages, task_name, audit_extra=None):
    """Common wrapper to run an AI query through the auto-router."""
    system_text = load_system_prompt()
    last_user = next(
        (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
    )
    tried = []
    while True:
        entry = router.pick()
        if entry is None:
            msg = f"All AI providers exhausted. Tried: {tried}"
            audit({"task": task_name, "error": msg, **(audit_extra or {})})
            return jsonify({"error": msg}), 503

        provider_enum = entry["provider"]
        tried.append(entry["display"])

        try:
            reply = _dispatch(
                api_id=entry["api_id"],
                model_name=entry["model_name"],
                system_text=system_text,
                messages=messages,
            )
            router.mark_used(provider_enum)
            audit({
                "task": task_name,
                "provider": entry["display"],
                "model": entry["model_name"],
                "user_msg": last_user[:200],
                "reply": reply[:500],
                **(audit_extra or {}),
            })
            return jsonify({
                "reply": reply,
                "model": entry["model_name"],
                "provider": entry["display"],
                "mode": "auto",
            })

        except req.HTTPError as exc:
            status = exc.response.status_code
            router.mark_failed(provider_enum, reason=f"HTTP {status}")
            log.warning("Auto-router: %s failed HTTP %d, trying next.", entry["display"], status)

        except Exception as exc:
            router.mark_failed(provider_enum, reason=repr(exc)[:80])
            log.warning("Auto-router: %s failed (%s), trying next.", entry["display"], exc)


def _handle_auto(messages, system_text, last_user):
    """Try providers in priority order; fallback on failure."""
    tried = []
    while True:
        entry = router.pick()
        if entry is None:
            msg = f"All AI providers exhausted or rate-limited. Tried: {tried}"
            audit({"mode": "auto", "user_msg": last_user[:200], "error": msg})
            return jsonify({"error": msg}), 503

        provider_enum = entry["provider"]
        tried.append(entry["display"])

        try:
            reply = _dispatch(
                api_id=entry["api_id"],
                model_name=entry["model_name"],
                system_text=system_text,
                messages=messages,
            )
            router.mark_used(provider_enum)
            audit({
                "mode": "auto",
                "provider": entry["display"],
                "model": entry["model_name"],
                "user_msg": last_user[:200],
                "reply": reply[:500],
            })
            return jsonify({
                "reply": reply,
                "model": entry["model_name"],
                "provider": entry["display"],
                "mode": "auto",
            })

        except req.HTTPError as exc:
            status = exc.response.status_code
            reason = "rate_limit" if status in (429, 503) else "http_error"
            router.mark_failed(provider_enum, reason=f"HTTP {status}")
            log.warning("Auto-router: %s failed HTTP %d, trying next.", entry["display"], status)

        except Exception as exc:
            router.mark_failed(provider_enum, reason=repr(exc)[:80])
            log.warning("Auto-router: %s failed (%s), trying next.", entry["display"], exc)


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
    api_id = "groq" if provider in ("groq", "groq_qwen", "groq_oss") else provider

    try:
        reply = _dispatch(
            api_id=api_id,
            model_name=model_name,
            system_text=system_text,
            messages=messages,
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
        "reply": reply,
        "model": model_name,
        "provider": provider,
        "mode": "manual",
    })


# ═══ Provider dispatcher ════════════════════════════════════════════════════
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


# ═══ Entry point ════════════════════════════════════════════════════════════
if __name__ == "__main__":
    configured_manual = [p for p, v in MANUAL_PROVIDERS.items() if get_key(v)]
    router_status_data = router.status()
    print("=" * 64)
    print("  RakshaLink AI Security Intelligence  |  LOCAL ONLY")
    print("  http://127.0.0.1:5001")
    print("  CORS enabled for RakshaLink dashboard")
    print("  Do NOT expose this port publicly.")
    print("=" * 64)
    print(f"  Manual providers : {configured_manual or ['none']}")
    print()
    print("  Auto-Router (priority order):")
    for p, info in router_status_data.items():
        key_ok = bool(
            get_key("GOOGLE_AI_STUDIO_API_KEY") if "gemini" in p else
            get_key("GROQ_API_KEY")              if "groq"   in p else
            get_key("CLOUDFLARE_API_KEY")
        )
        status_sym = "[OK]" if key_ok else "[--] (no key)"
        print(f"    [{info['priority']}] {info['display']:22s} {info['model']:40s} {status_sym}")
    print()
    app.run(host="127.0.0.1", port=5001, debug=False)
