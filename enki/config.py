from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Config:
    # --- LLM ---
    claude_model: str = os.getenv("ENKI_CLAUDE_MODEL", "claude-sonnet-5-5")
    claude_timeout_s: float = 20.0
    ollama_url: str = os.getenv("OLLAMA_URL", "http://localhost:11434")
    ollama_model: str = os.getenv("ENKI_OLLAMA_MODEL", "qwen2.5:3b")
    ollama_timeout_s: float = 60.0
    max_tokens: int = 1024
    breaker_cooldown_s: float = 60.0

    # --- Memory ---
    db_path: str = os.getenv("ENKI_DB", "enki.db")
    embed_dim: int = 512
    history_turns: int = 12
    recall_k: int = 4
    recall_min_score: float = 0.18
    fact_dedup_threshold: float = 0.90
    max_facts_in_prompt: int = 40

    # --- Initiative (inhomogeneous Poisson process) ---
    tick_s: float = 5.0
    base_rate_per_min: float = 0.02      # λ0: silence=0 iken ~50 dk'da bir
    silence_tau_min: float = 10.0        # λ(t) = λ0 * (1 + t/τ)
    max_rate_per_min: float = 0.25
    initiative_cooldown_s: float = 180.0
    max_unanswered: int = 3              # boş odaya konuşmayı kes
    quiet_hours: tuple[int, int] = (1, 8)
