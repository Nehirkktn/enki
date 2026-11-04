"""Long-term memory: SQLite storage + in-RAM embedding matrix for cosine recall.

Embedding = feature hashing (char 3-grams -> R^d, L2-normalized). No model download,
deterministic, Pi-friendly. Recall is a single mat-vec: scores = E @ q, E ∈ R^{n×d}.
Swap `embed()` for a real sentence-embedding model later; the interface stays the same.
"""
from __future__ import annotations

import sqlite3
import time
import unicodedata
import zlib
from dataclasses import dataclass

import numpy as np

from .config import Config


def _normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).casefold()
    return "".join(c if c.isalnum() else " " for c in t)


def embed(text: str, dim: int) -> np.ndarray:
    v = np.zeros(dim, dtype=np.float32)
    for word in _normalize(text).split():
        w = f" {word} "
        for i in range(len(w) - 2):
            h = zlib.crc32(w[i : i + 3].encode())
            # signed hashing: collisions cancel in expectation instead of piling up
            v[h % dim] += 1.0 if (h >> 31) & 1 else -1.0
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


@dataclass(slots=True)
class Turn:
    role: str  # "user" | "assistant"
    text: str
    ts: float


class _VectorIndex:
    """Append-only row matrix with amortized O(1) growth (capacity doubling)."""

    def __init__(self, dim: int):
        self._m = np.empty((64, dim), dtype=np.float32)
        self.n = 0
        self.ids: list[int] = []
        self._pos: dict[int, int] = {}

    def add(self, row_id: int, vec: np.ndarray) -> None:
        if self.n == self._m.shape[0]:
            self._m = np.resize(self._m, (self.n * 2, self._m.shape[1]))
        self._m[self.n] = vec
        self.ids.append(row_id)
        self._pos[row_id] = self.n
        self.n += 1

    def set(self, row_id: int, vec: np.ndarray) -> None:
        self._m[self._pos[row_id]] = vec

    def topk(self, q: np.ndarray, k: int) -> list[tuple[int, float]]:
        if self.n == 0:
            return []
        scores = self._m[: self.n] @ q
        k = min(k, self.n)
        idx = np.argpartition(-scores, k - 1)[:k]
        idx = idx[np.argsort(-scores[idx])]
        return [(self.ids[i], float(scores[i])) for i in idx]


class Memory:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.db = sqlite3.connect(cfg.db_path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS turns(
                id INTEGER PRIMARY KEY, ts REAL NOT NULL, role TEXT NOT NULL,
                text TEXT NOT NULL, emb BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS facts(
                id INTEGER PRIMARY KEY, ts REAL NOT NULL, text TEXT NOT NULL,
                emb BLOB NOT NULL);
            """
        )
        self._turns = _VectorIndex(cfg.embed_dim)
        self._facts = _VectorIndex(cfg.embed_dim)
        self._load()

    def _load(self) -> None:
        for rid, blob in self.db.execute("SELECT id, emb FROM turns WHERE role='user'"):
            self._turns.add(rid, np.frombuffer(blob, dtype=np.float32))
        for rid, blob in self.db.execute("SELECT id, emb FROM facts"):
            self._facts.add(rid, np.frombuffer(blob, dtype=np.float32))

    # ---- turns ----
    def add_turn(self, role: str, text: str) -> None:
        vec = embed(text, self.cfg.embed_dim)
        cur = self.db.execute(
            "INSERT INTO turns(ts, role, text, emb) VALUES(?,?,?,?)",
            (time.time(), role, text, vec.tobytes()),
        )
        self.db.commit()
        if role == "user":
            self._turns.add(cur.lastrowid, vec)

    def recent(self, n: int) -> list[Turn]:
        rows = self.db.execute(
            "SELECT role, text, ts FROM turns ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
        return [Turn(r, t, ts) for r, t, ts in reversed(rows)]

    def recall(self, query: str, exclude_recent: int) -> list[str]:
        """Semantically similar *old* user turns (recent ones are already in the window)."""
        q = embed(query, self.cfg.embed_dim)
        cutoff = self.db.execute(
            "SELECT COALESCE(MIN(id), 0) FROM (SELECT id FROM turns ORDER BY id DESC LIMIT ?)",
            (exclude_recent,),
        ).fetchone()[0]
        hits = [
            (rid, s)
            for rid, s in self._turns.topk(q, self.cfg.recall_k * 3)
            if rid < cutoff and s >= self.cfg.recall_min_score
        ][: self.cfg.recall_k]
        out = []
        for rid, _ in hits:
            ts, text = self.db.execute("SELECT ts, text FROM turns WHERE id=?", (rid,)).fetchone()
            out.append(f"[{time.strftime('%d.%m %H:%M', time.localtime(ts))}] Nehir: {text}")
        return out

    # ---- facts ----
    def add_fact(self, text: str) -> bool:
        text = text.strip(" -•\t")
        if len(text) < 4:
            return False
        vec = embed(text, self.cfg.embed_dim)
        top = self._facts.topk(vec, 1)
        if top and top[0][1] >= self.cfg.fact_dedup_threshold:
            # near-duplicate: newer phrasing wins (facts get corrected over time)
            self.db.execute(
                "UPDATE facts SET text=?, ts=?, emb=? WHERE id=?",
                (text, time.time(), vec.tobytes(), top[0][0]),
            )
            self.db.commit()
            self._facts.set(top[0][0], vec)
            return False
        cur = self.db.execute(
            "INSERT INTO facts(ts, text, emb) VALUES(?,?,?)", (time.time(), text, vec.tobytes())
        )
        self.db.commit()
        self._facts.add(cur.lastrowid, vec)
        return True

    def facts(self) -> list[str]:
        rows = self.db.execute(
            "SELECT text FROM facts ORDER BY ts DESC LIMIT ?", (self.cfg.max_facts_in_prompt,)
        ).fetchall()
        return [r[0] for r in rows]

    def close(self) -> None:
        self.db.close()
