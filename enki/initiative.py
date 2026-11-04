"""When does Enki speak unprompted?  Non-homogeneous Poisson process.

    λ(t) = min(λ0 · (1 + t/τ), λmax)        t = minutes since last exchange
    P(speak in Δt) = 1 − exp(−λ(t) · Δt)

Silence raises the rate; a reply resets it. Hard gates (cooldown, quiet hours,
unanswered streak) sit on top so it can't turn into a nagging metronome.
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from datetime import datetime

from .config import Config


@dataclass
class Initiative:
    cfg: Config
    rng: random.Random = field(default_factory=random.Random)
    last_exchange: float = field(default_factory=time.time)
    last_initiative: float = 0.0
    unanswered: int = 0
    greeted_on: str = ""

    def on_user_spoke(self) -> None:
        self.last_exchange = time.time()
        self.unanswered = 0

    def on_enki_initiated(self) -> None:
        self.last_initiative = time.time()
        self.unanswered += 1

    def silence_min(self, now: float | None = None) -> float:
        return ((now or time.time()) - self.last_exchange) / 60.0

    def rate(self, silence_min: float) -> float:
        c = self.cfg
        return min(c.base_rate_per_min * (1 + silence_min / c.silence_tau_min), c.max_rate_per_min)

    def _quiet_hour(self, hour: int) -> bool:
        a, b = self.cfg.quiet_hours
        if a == b:  # empty interval = feature off
            return False
        return a <= hour < b if a < b else (hour >= a or hour < b)

    def should_speak(self, now: float | None = None) -> str | None:
        """Called every tick. Returns a reason string if Enki should talk, else None."""
        now = now or time.time()
        if self.unanswered >= self.cfg.max_unanswered:
            return None
        if now - self.last_initiative < self.cfg.initiative_cooldown_s:
            return None
        if self._quiet_hour(datetime.fromtimestamp(now).hour):
            return None
        s = self.silence_min(now)
        p = 1.0 - math.exp(-self.rate(s) * self.cfg.tick_s / 60.0)
        return "uzun sessizlik, canın sıkıldı" if self.rng.random() < p else None

    def should_greet(self, now: float | None = None) -> str | None:
        """Event-driven rule: first sighting of the day (camera) -> greet."""
        d = datetime.fromtimestamp(now or time.time())
        today = d.strftime("%Y-%m-%d")
        if self.greeted_on == today or self._quiet_hour(d.hour):
            return None
        self.greeted_on = today
        return "Nehir'i bugün ilk kez görüyorsun, selam ver"
