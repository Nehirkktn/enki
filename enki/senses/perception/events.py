"""Event bus + presence FSM. Perception publishes, the robot 'brain' subscribes."""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable

log = logging.getLogger("events")


class EventType(Enum):
    MOTION_START = auto()
    MOTION_END = auto()
    LIGHTING_CHANGE = auto()
    OWNER_ARRIVED = auto()
    OWNER_LEFT = auto()
    STRANGER_APPEARED = auto()
    SCENE_EMPTY = auto()


@dataclass(frozen=True, slots=True)
class Event:
    type: EventType
    data: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


Handler = Callable[[Event], None]


class EventBus:
    """In-process Observer. Same interface later backs onto MQTT/ZeroMQ (Pi <-> PC)."""

    def __init__(self) -> None:
        self._subs: dict[EventType | None, list[Handler]] = defaultdict(list)

    def subscribe(self, handler: Handler, *types: EventType) -> None:
        for t in types or (None,):  # no types -> wildcard
            self._subs[t].append(handler)

    def publish(self, event: Event) -> None:
        for h in (*self._subs[event.type], *self._subs[None]):
            try:
                h(event)
            except Exception:  # one bad subscriber must never kill the perception loop
                log.exception("handler failed for %s", event.type.name)


class Presence(Enum):
    EMPTY = auto()
    STRANGER = auto()
    OWNER = auto()


class PresenceFSM:
    """DFA over Σ = {EMPTY, STRANGER, OWNER} with hysteresis.

    A transition fires only after a candidate observation has held CONTINUOUSLY for
    `enter_s` (or `exit_s` for EMPTY) seconds. Time-based, not frame-based: the pipeline's
    detection rate varies (motion gating, CPU load), a frame count would mean different
    latencies on a laptop vs a Pi. Exit is deliberately slower: a 2-frame detector miss
    must not make the robot think you left the room.
    `clock` is injected -> deterministic tests with a fake clock.
    """

    def __init__(self, bus: EventBus, enter_s: float = 0.3, exit_s: float = 1.2,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.bus, self.enter_s, self.exit_s, self.clock = bus, enter_s, exit_s, clock
        self.state = Presence.EMPTY
        self._cand: Presence | None = None
        self._cand_t = 0.0
        self._since = clock()

    def step(self, obs: Presence, **data) -> None:
        now = self.clock()
        if obs is self.state:
            self._cand = None
            return
        if obs is not self._cand:
            self._cand, self._cand_t = obs, now
        if now - self._cand_t >= (self.exit_s if obs is Presence.EMPTY else self.enter_s):
            self._transition(obs, data, now)

    def _transition(self, new: Presence, data: dict, now: float) -> None:
        old = self.state
        self.state, self._cand = new, None
        data = {**data, "from": old.name, "dwell_s": round(now - self._since, 1)}
        self._since = now
        if old is Presence.OWNER:
            self.bus.publish(Event(EventType.OWNER_LEFT, data))
        match new:
            case Presence.OWNER:
                self.bus.publish(Event(EventType.OWNER_ARRIVED, data))
            case Presence.STRANGER:
                self.bus.publish(Event(EventType.STRANGER_APPEARED, data))
            case Presence.EMPTY:
                self.bus.publish(Event(EventType.SCENE_EMPTY, data))
