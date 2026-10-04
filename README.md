<div align="center">

<img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white"/>
<img src="https://img.shields.io/badge/Flask-3.x-000000?style=for-the-badge&logo=flask&logoColor=white"/>
<img src="https://img.shields.io/badge/Google_Gemini-Flash-4285F4?style=for-the-badge&logo=google&logoColor=white"/>
<img src="https://img.shields.io/badge/Groq-Qwen3_27B-FF6C37?style=for-the-badge&logo=groq&logoColor=white"/>
<img src="https://img.shields.io/badge/Cloudflare-Workers_AI-F38020?style=for-the-badge&logo=cloudflare&logoColor=white"/>
<img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge"/>
<img src="https://img.shields.io/github/stars/Rakshit-Jadon/secresearch-assistant?style=for-the-badge&color=gold"/>

# 🛡️ SecResearch AI Assistant

### **Self-hosted, multi-model, auto-routing security research chat tool**
*Gemini Flash → Groq Qwen3 → Groq OSS-120B → Cloudflare — keys never leave your machine*

[Live Demo](#) · [Quick Start](#quick-start) · [Architecture](#architecture) · [Contributing](#contributing)

</div>

---

> **TL;DR** — Drop your API keys in `.env`, run `python app.py`, open `http://127.0.0.1:5000`. The smart router automatically picks the best available model with rate-limit fallback. Zero cloud deployment, zero key exposure.

---

## ✨ Why This Exists

Every AI security research tool I found either:
- Required keys in the browser (🚨 exposure risk)
- Locked you to one model / vendor
- Crashed silently on rate-limits

**SecResearch Assistant solves all three:**

| Problem | Solution |
|---------|----------|
| Keys exposed client-side | Flask proxy — browser calls `/api/chat` only |
| Single model = single point of failure | 4-provider auto-router with fallback |
| Rate-limit crashes | Exponential cooldown + priority queue |
| Vendor lock-in | Swap models via `.env`, zero code changes |

---

## 🚀 Quick Start

```bash
git clone https://github.com/Rakshit-Jadon/secresearch-assistant.git
cd secresearch-assistant

pip install -r requirements.txt

cp .env.example .env
# ✏️  Edit .env — add your API keys (see table below)

python app.py
# → http://127.0.0.1:5000
```

That's it. No Docker. No build step. No cloud account needed.

---

## 🔑 API Keys (all free tiers available)

| Provider | Env Var | Free Tier | Get Key |
|----------|---------|-----------|---------|
| Google Gemini Flash | `GOOGLE_AI_STUDIO_API_KEY` | 1,500 req/day | [aistudio.google.com](https://aistudio.google.com) |
| Groq (Qwen3-27B + OSS-120B) | `GROQ_API_KEY` | 1,000 req/day | [console.groq.com](https://console.groq.com) |
| Cloudflare Workers AI | `CLOUDFLARE_API_KEY` + `CLOUDFLARE_ACCOUNT_ID` | 10,000 req/day | [dash.cloudflare.com](https://dash.cloudflare.com) |
| NVIDIA NIM (manual) | `NVIDIA_API_KEY` | Credits | [integrate.api.nvidia.com](https://integrate.api.nvidia.com) |

> You need **at minimum one key** to start. The router skips providers with no key configured.

---

## 🏗️ Architecture

```
                        YOU (browser)
                             │
                    http://127.0.0.1:5000
                             │
                    ┌────────▼────────┐
                    │   Flask Backend  │  ← app.py
                    │  (API proxy)     │    Keys stay here
                    └────────┬────────┘
                             │  provider="auto"
                    ┌────────▼────────┐
                    │   ModelRouter   │  ← model_router/router.py
                    │  (Smart Switch) │    RPM + daily quota tracking
                    └────────┬────────┘    Exponential cooldown on fail
                             │
          ┌──────────────────┼──────────────────────┐
          │ Priority 1       │ Priority 2+3          │ Priority 4
          ▼                  ▼                       ▼
  ┌──────────────┐  ┌──────────────────┐  ┌──────────────────┐
  │ Gemini Flash │  │ Groq Qwen3-27B   │  │ Cloudflare Qwen  │
  │ (Primary)    │  │ Groq OSS-120B    │  │ (Fallback)       │
  │ 1,500/day    │  │ (Fast + Strong)  │  │ 10,000/day       │
  └──────────────┘  └──────────────────┘  └──────────────────┘
```

### Auto-Router Logic

```python
# Simplified — see model_router/router.py for full implementation
priority_order = [Gemini, Groq_Qwen, Groq_OSS, Cloudflare]

for provider in priority_order:
    if provider.has_key() and provider.under_quota() and provider.not_in_cooldown():
        try:
            reply = provider.call(messages)
            provider.mark_used()
            return reply          # ✅ success — stops here
        except RateLimitError:
            provider.cooldown(exponential_backoff)
            continue              # 🔄 try next

raise AllProvidersExhausted()     # 503 with helpful message
```

---

## 📁 File Structure

```
secresearch-assistant/
│
├── app.py                          # Flask backend — the only server file you need
├── requirements.txt                # flask, python-dotenv, requests
├── .env.example                    # Key template — copy to .env
├── SYSTEM_PROMPT.md                # Orchestrator rules (edit freely)
│
├── model_router/                   # Smart fallback engine
│   ├── router.py                   # ModelRouter — priority, quotas, cooldowns
│   └── providers/
│       ├── gemini.py               # Google Gemini Flash
│       ├── groq.py                 # Groq (Qwen3-27B & OSS-120B)
│       └── cloudflare.py          # Cloudflare Workers AI
│
└── static/
    └── index.html                  # Full ChatGPT-style UI — zero build step
```

---

## 🎛️ UI Features

- **🤖 Auto (Smart Router)** — default mode, router picks best available model
- **Manual override** — pin to Gemini / Groq Qwen / Groq OSS / Cloudflare
- **Live quota bars** — see daily usage vs limit for each provider, auto-refresh every 15s
- **Model badge** — every AI reply shows which model + provider answered
- **Quick prompts** — one-click: Compliance Audit, Explain CVE, Scope Check
- **Keyboard-friendly** — Enter to send, Shift+Enter for newline, full tab navigation
- **Local audit log** — every request/reply logged to `audit_log.jsonl`

---

## 🔒 Security Design

| Concern | How it's handled |
|---------|-----------------|
| API key exposure | Keys only in `.env` (server-side). Browser only calls `/api/chat` |
| Accidental git push | `.env` in `.gitignore`. Audit log also excluded |
| Public exposure | Server binds to `127.0.0.1` only — not `0.0.0.0` |
| Scope enforcement | `SYSTEM_PROMPT.md` encodes hard rules — loaded fresh on every request |
| Audit trail | `audit_log.jsonl` — append-only, local, JSON Lines format |

---

## 🧩 Customisation

### Change model priorities

Edit `model_router/router.py` → `_DEFAULTS` dict:

```python
Provider.GEMINI: {
    "priority":    1,        # Lower = higher priority
    "rpm_limit":   15,       # Requests per minute
    "daily_limit": 1500,     # Daily quota
    "cooldown_s":  60,       # Cooldown after first failure (doubles each retry)
},
```

### Change the system prompt

Edit `SYSTEM_PROMPT.md` — reloaded on every request, no server restart needed.

### Add a new provider

1. Create `model_router/providers/myprovider.py` with a `call(api_key, model_name, system_text, messages) -> str` function
2. Add an entry to `_DEFAULTS` in `router.py`
3. Add the env-var name to `MANUAL_PROVIDERS` in `app.py`

---

## 📊 Model Comparison

| Model | Provider | Speed | Quality | Daily Free |
|-------|----------|-------|---------|------------|
| **gemini-2.0-flash** | Google | Fast | ⭐⭐⭐⭐⭐ | 1,500 |
| **qwen/qwen3-27b** | Groq | Very Fast | ⭐⭐⭐⭐ | 1,000 |
| **openai/gpt-oss-120b** | Groq | Fast | ⭐⭐⭐⭐⭐ | 500 |
| **@cf/qwen/qwen3-8b** | Cloudflare | Medium | ⭐⭐⭐ | 10,000 |

---

## 🛠️ Troubleshooting

**Server won't start**
```bash
# Check Python version (3.10+ required)
python --version

# Re-install dependencies
pip install -r requirements.txt
```

**`.env` encoding error on startup**
```bash
# Write .env as ASCII (avoid copy-pasting from Word/Notion)
python -c "open('.env','wb').write(open('.env.example').read().encode('ascii'))"
```

**Model returns 401 / Invalid API Key**
- Check the key in `.env` is the real key, not the placeholder
- Restart the server after editing `.env`

**All providers exhausted (503)**
- Wait 60s for RPM cooldown to clear, or switch to a provider with remaining daily quota
- Check `/api/router-status` endpoint for live quota data

---

## 🤝 Contributing

PRs welcome! Ideas:

- [ ] Streaming responses (`text/event-stream`)
- [ ] Conversation history persistence (SQLite)
- [ ] Multi-agent orchestration mode
- [ ] Docker / one-click deploy script
- [ ] OpenRouter support toggle (opt-in)
- [ ] Token cost estimator sidebar

```bash
git checkout -b feature/my-feature
# make changes
git commit -m "feat: add streaming support"
git push origin feature/my-feature
# open a PR 🙏
```

---

## 📄 License

MIT — free to use, modify, and distribute. See [LICENSE](LICENSE).

---

<div align="center">

**If this saved you time → drop a ⭐ — it helps others find it!**

Made with 🔥 by [Rakshit Jadon](https://github.com/Rakshit-Jadon)

</div>
