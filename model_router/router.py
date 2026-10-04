"""
model_router/router.py
Smart auto-fallback model router — 4-provider, no OpenRouter.

Priority:
  1. Gemini Flash   (primary — best free model overall)
  2. Groq Qwen3     (fast coding + loops)
  3. Groq OSS-120b  (strong reasoning)
  4. Cloudflare     (high-volume fallback)
"""
import threading
import time
import logging
from enum import Enum
from typing import Optional, Dict, Any

log = logging.getLogger(__name__)


class Provider(Enum):
    GEMINI     = "gemini"
    GROQ_QWEN  = "groq_qwen"
    GROQ_OSS   = "groq_oss"
    CLOUDFLARE = "cloudflare"


# ── Default limits (tune in config/models.yaml or via constructor) ────────────
_DEFAULTS: Dict[Provider, Dict[str, Any]] = {
    Provider.GEMINI: {
        "display":     "Gemini Flash",
        "api_id":      "gemini",          # maps to app.py call_google()
        "model_name":  "gemini-2.0-flash",
        "priority":    1,
        "rpm_limit":   15,
        "daily_limit": 1500,
        "cooldown_s":  60,                # seconds to wait after a failure
    },
    Provider.GROQ_QWEN: {
        "display":     "Groq Qwen3-27B",
        "api_id":      "groq",
        "model_name":  "qwen/qwen3-27b",
        "priority":    2,
        "rpm_limit":   30,
        "daily_limit": 1000,
        "cooldown_s":  30,
    },
    Provider.GROQ_OSS: {
        "display":     "Groq OSS-120B",
        "api_id":      "groq",
        "model_name":  "openai/gpt-oss-120b",
        "priority":    3,
        "rpm_limit":   20,
        "daily_limit": 500,
        "cooldown_s":  30,
    },
    Provider.CLOUDFLARE: {
        "display":     "Cloudflare Qwen",
        "api_id":      "cloudflare",
        "model_name":  "@cf/qwen/qwen3-8b",
        "priority":    4,
        "rpm_limit":   50,
        "daily_limit": 10_000,
        "cooldown_s":  15,
    },
}


class ModelRouter:
    """
    Thread-safe priority router with RPM + daily quota tracking and
    exponential-cooldown on failure.

    Usage::

        router = ModelRouter()
        entry = router.pick()            # {"provider": ..., "model_name": ..., ...}
        # ... make the API call ...
        router.mark_used(entry["provider"])
        # or on failure:
        router.mark_failed(entry["provider"], reason="rate_limit")
    """

    def __init__(self, overrides: Optional[Dict[Provider, Dict]] = None):
        self._lock   = threading.Lock()
        self._state: Dict[Provider, Dict[str, Any]] = {}

        for p, defaults in _DEFAULTS.items():
            entry = dict(defaults)
            if overrides and p in overrides:
                entry.update(overrides[p])
            entry.update({
                "current_rpm":   0,
                "current_daily": 0,
                "last_rpm_reset":   time.monotonic(),
                "last_daily_reset": time.monotonic(),
                "failed_until":  0.0,    # epoch; 0 = available
                "fail_count":    0,
            })
            self._state[p] = entry

    # ── Public API ─────────────────────────────────────────────────────────────

    def pick(self, task: str = "general") -> Optional[Dict[str, Any]]:
        """
        Return the highest-priority available provider entry, or None if all
        are exhausted.  The returned dict contains at minimum:
          provider, api_id, model_name, display
        """
        with self._lock:
            self._refresh_counters()
            ordered = sorted(
                self._state.items(),
                key=lambda kv: kv[1]["priority"],
            )
            for provider, info in ordered:
                if self._is_available(info):
                    return {
                        "provider":   provider,
                        "api_id":     info["api_id"],
                        "model_name": info["model_name"],
                        "display":    info["display"],
                    }
        return None   # all exhausted

    def mark_used(self, provider: Provider) -> None:
        """Increment counters after a successful call."""
        with self._lock:
            info = self._state.get(provider)
            if info:
                info["current_rpm"]   += 1
                info["current_daily"] += 1
                info["fail_count"]     = 0     # reset on success

    def mark_failed(self, provider: Provider, reason: str = "error") -> None:
        """
        Put provider on cooldown. Cooldown doubles with each consecutive failure
        (capped at 10 min).
        """
        with self._lock:
            info = self._state.get(provider)
            if not info:
                return
            info["fail_count"] += 1
            backoff   = info["cooldown_s"] * (2 ** (info["fail_count"] - 1))
            backoff   = min(backoff, 600)  # cap at 10 min
            info["failed_until"] = time.monotonic() + backoff
            log.warning(
                "Router: %s failed (%s). Cooldown %.0fs (attempt #%d).",
                info["display"], reason, backoff, info["fail_count"],
            )

    def status(self) -> Dict[str, Dict]:
        """Return a snapshot for /api/router-status endpoint."""
        with self._lock:
            self._refresh_counters()
            now = time.monotonic()
            out = {}
            for p, info in sorted(
                self._state.items(), key=lambda kv: kv[1]["priority"]
            ):
                available = self._is_available(info)
                out[p.value] = {
                    "display":       info["display"],
                    "model":         info["model_name"],
                    "priority":      info["priority"],
                    "available":     available,
                    "daily_used":    info["current_daily"],
                    "daily_limit":   info["daily_limit"],
                    "rpm_used":      info["current_rpm"],
                    "rpm_limit":     info["rpm_limit"],
                    "cooldown_left": max(0.0, round(info["failed_until"] - now, 1)),
                }
            return out

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _is_available(self, info: Dict) -> bool:
        now = time.monotonic()
        if now < info["failed_until"]:
            return False
        if info["current_daily"] >= info["daily_limit"]:
            return False
        if info["current_rpm"] >= info["rpm_limit"]:
            return False
        return True

    def _refresh_counters(self) -> None:
        """Reset RPM every 60 s and daily every 24 h."""
        now = time.monotonic()
        for info in self._state.values():
            if now - info["last_rpm_reset"] >= 60:
                info["current_rpm"]   = 0
                info["last_rpm_reset"] = now
            if now - info["last_daily_reset"] >= 86_400:
                info["current_daily"]   = 0
                info["last_daily_reset"] = now
