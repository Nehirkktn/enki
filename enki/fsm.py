"""Enki's behavioural state as a DFA:  M = (Q, Σ, δ, q0, ∅).

Every module (eyes, mic, motors later) reacts to state *changes*, never pokes state itself.
Undefined δ(q, σ) is a bug, not a silent no-op — it raises.
"""
from __future__ import annotations

from enum import Enum, auto
from typing import Callable


class Q(Enum):
    IDLE = auto()
    LISTENING = auto()
    THINKING = auto()
    SPEAKING = auto()
    SLEEPING = auto()


class Sigma(Enum):
    WAKE = auto()        # wake word / user input arrived
    HEARD = auto()       # STT finished
    INITIATE = auto()    # Enki decided to talk on its own
    REPLY_READY = auto()
    SILENT = auto()      # LLM chose [SUS]
    DONE_SPEAKING = auto()
    SLEEP = auto()
    WAKE_UP = auto()


DELTA: dict[tuple[Q, Sigma], Q] = {
    (Q.IDLE, Sigma.WAKE): Q.LISTENING,
    (Q.IDLE, Sigma.INITIATE): Q.THINKING,
    (Q.IDLE, Sigma.SLEEP): Q.SLEEPING,
    (Q.LISTENING, Sigma.HEARD): Q.THINKING,
    (Q.THINKING, Sigma.REPLY_READY): Q.SPEAKING,
    (Q.THINKING, Sigma.SILENT): Q.IDLE,
    (Q.SPEAKING, Sigma.DONE_SPEAKING): Q.IDLE,
    (Q.SPEAKING, Sigma.WAKE): Q.LISTENING,  # barge-in: she interrupts Enki
    (Q.SLEEPING, Sigma.WAKE): Q.LISTENING,
    (Q.SLEEPING, Sigma.WAKE_UP): Q.IDLE,
}

Listener = Callable[[Q, Sigma, Q], None]


class InvalidTransition(RuntimeError):
    pass


class StateMachine:
    def __init__(self, q0: Q = Q.IDLE):
        self.q = q0
        self._listeners: list[Listener] = []

    def subscribe(self, fn: Listener) -> None:
        self._listeners.append(fn)

    def reset(self, q: Q = Q.IDLE) -> None:
        """Error-recovery escape hatch only. Normal flow goes through fire()."""
        self.q = q

    def fire(self, s: Sigma) -> Q:
        nxt = DELTA.get((self.q, s))
        if nxt is None:
            raise InvalidTransition(f"δ({self.q.name}, {s.name}) undefined")
        prev, self.q = self.q, nxt
        for fn in self._listeners:
            fn(prev, s, nxt)
        return nxt
