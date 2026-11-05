"""LLM port + adapters. Claude is primary, Ollama is the offline fallback.

FailoverLLM wraps both behind a circuit breaker: after a Claude failure we stop hammering
the network for `breaker_cooldown_s` and go straight to the local model.
"""
from __future__ import annotations

import logging
import time
from typing import Protocol

import httpx

from .config import Config

log = logging.getLogger("enki.llm")
Message = dict  # {"role": "user"|"assistant", "content": str | list[block]}  (block: Anthropic text/image)


def as_text(content) -> str:
    """Görüntülü mesajı metne indir (görüntü okuyamayan yerel model için)."""
    if isinstance(content, str):
        return content
    return " ".join(b.get("text", "") if b.get("type") == "text" else "[kamera görüntüsü: yerel beyin göremez]"
                    for b in content)


class LLM(Protocol):
    name: str

    async def complete(self, core: str, context: str, messages: list[Message]) -> str: ...


class ClaudeLLM:
    name = "claude"

    def __init__(self, cfg: Config):
        from anthropic import AsyncAnthropic  # lazy: offline boxes don't need the SDK loaded

        self.cfg = cfg
        self.client = AsyncAnthropic(timeout=cfg.claude_timeout_s, max_retries=1)

    async def complete(self, core: str, context: str, messages: list[Message]) -> str:
        resp = await self.client.messages.create(
            model=self.cfg.claude_model,
            max_tokens=self.cfg.max_tokens,
            system=[
                # static persona -> cached; dynamic context -> not cached
                {"type": "text", "text": core, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": context},
            ],
            messages=messages,
        )
        return "".join(b.text for b in resp.content if b.type == "text")


class OllamaLLM:
    name = "ollama"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.http = httpx.AsyncClient(base_url=cfg.ollama_url, timeout=cfg.ollama_timeout_s)

    async def complete(self, core: str, context: str, messages: list[Message]) -> str:
        r = await self.http.post(
            "/api/chat",
            json={
                "model": self.cfg.ollama_model,
                "stream": False,
                "options": {"num_predict": self.cfg.max_tokens},
                "messages": [{"role": "system", "content": f"{core}\n\n{context}"},
                             *({"role": m["role"], "content": as_text(m["content"])} for m in messages)],
            },
        )
        r.raise_for_status()
        return r.json()["message"]["content"]


_PERMANENT_STATUS = frozenset({400, 401, 403, 404})  # retry won't fix these


def _why(e: Exception) -> str:
    code = getattr(e, "status_code", None)
    return f"{code} {type(e).__name__}" if code else type(e).__name__


class FailoverLLM:
    def __init__(self, primary: LLM | None, fallback: LLM | None, cooldown_s: float):
        if not (primary or fallback):
            raise RuntimeError("No LLM backend available")
        self.primary, self.fallback, self.cooldown_s = primary, fallback, cooldown_s
        self._open_until = 0.0
        self.last_used = "-"
        self.primary_error: str | None = None

    @property
    def name(self) -> str:
        return self.last_used

    async def complete(self, core: str, context: str, messages: list[Message]) -> str:
        if self.primary and time.monotonic() >= self._open_until:
            try:
                out = await self.primary.complete(core, context, messages)
                self.last_used = self.primary.name
                return out
            except Exception as e:
                if getattr(e, "status_code", None) not in _PERMANENT_STATUS:
                    log.warning("primary failed (%s), breaker open %ss", _why(e), self.cooldown_s)
                    self._open_until = time.monotonic() + self.cooldown_s
                    return await self._fallback(core, context, messages)
                self.primary_error = _why(e)
                log.error("primary DISABLED (%s): config duzelt, yeniden baslat", self.primary_error)
                self.primary = None
        return await self._fallback(core, context, messages)

    async def _fallback(self, core: str, context: str, messages: list[Message]) -> str:
        if not self.fallback:
            raise RuntimeError("Primary down and no fallback configured")
        out = await self.fallback.complete(core, context, messages)
        self.last_used = self.fallback.name
        return out


def build_llm(cfg: Config) -> FailoverLLM:
    import os

    primary = ClaudeLLM(cfg) if os.getenv("ANTHROPIC_API_KEY") else None
    fallback = OllamaLLM(cfg) if os.getenv("ENKI_NO_OLLAMA") is None else None
    return FailoverLLM(primary, fallback, cfg.breaker_cooldown_s)
