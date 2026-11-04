import asyncio, math, random
import numpy as np
import pytest

from enki.brain import Brain, FaceSeen, Overheard, UserSaid, parse_reply, to_messages
from enki.config import Config
from enki.fsm import InvalidTransition, Q, Sigma, StateMachine
from enki.initiative import Initiative
from enki.llm import FailoverLLM
from enki.memory import Memory, embed


class FakeLLM:
    name = "fake"
    def __init__(self, replies): self.replies, self.calls = list(replies), []
    async def complete(self, core, context, messages):
        self.calls.append((context, messages))
        return self.replies.pop(0)


class Boom:
    name = "boom"
    async def complete(self, *a): raise ConnectionError("offline")


class Spk:
    def __init__(self): self.said = []
    async def say(self, t): self.said.append(t)


@pytest.fixture
def cfg(tmp_path):
    return Config(db_path=str(tmp_path / "t.db"))


def test_embed_unit_and_similarity():
    d = 512
    a, b, c = embed("yarın lineer cebir sınavım var", d), embed("lineer cebir sınavı yarın", d), embed("pizza sipariş et", d)
    assert math.isclose(float(np.linalg.norm(a)), 1.0, rel_tol=1e-5)
    assert a @ b > a @ c + 0.2


def test_parse_reply():
    r = parse_reply("**Tamam** patron.\n<hafiza>\nCuma lineer cebir quiz'i var\n</hafiza>")
    assert r.speech == "Tamam patron." and r.facts == ["Cuma lineer cebir quiz'i var"] and not r.silent
    assert parse_reply("[SUS]").silent
    assert parse_reply("ok <hafiza>yarım kalmış").facts == ["yarım kalmış"]


def test_to_messages_alternates():
    m = to_messages([("assistant", "a"), ("user", "u1"), ("user", "u2"), ("assistant", "b")], None)
    assert [x["role"] for x in m] == ["user", "assistant", "user"]
    assert m[0]["content"] == "u1\nu2"


def test_fsm_rejects_undefined():
    f = StateMachine()
    with pytest.raises(InvalidTransition):
        f.fire(Sigma.DONE_SPEAKING)
    f.fire(Sigma.WAKE); f.fire(Sigma.HEARD)
    assert f.q is Q.THINKING


def test_memory_dedup_and_recall(cfg):
    m = Memory(cfg)
    assert m.add_fact("Nehir'in kedisinin adı Pamuk")
    assert not m.add_fact("Nehir'in kedisinin adı Pamuk.")
    assert len(m.facts()) == 1
    m.add_turn("user", "otomata dersinde DFA minimizasyonu anlamadım")
    for i in range(20): m.add_turn("user", f"alakasız {i}")
    assert any("DFA" in r for r in m.recall("DFA minimizasyonu nasıl yapılır", exclude_recent=12))
    m.close()
    assert len(Memory(cfg).facts()) == 1  # persisted


def test_initiative_rate_and_gates(cfg):
    ini = Initiative(cfg, rng=random.Random(0))
    assert ini.rate(0) == pytest.approx(cfg.base_rate_per_min)
    assert ini.rate(1e6) == cfg.max_rate_per_min
    ini.unanswered = cfg.max_unanswered
    assert ini.should_speak() is None


def test_initiative_empirical_rate(cfg):
    # E[ticks until fire] for constant λ ≈ 1/p.  Freeze silence by moving last_exchange with now.
    ini = Initiative(Config(quiet_hours=(0, 0), initiative_cooldown_s=0), rng=random.Random(1))
    lam = ini.rate(30)
    p = 1 - math.exp(-lam * ini.cfg.tick_s / 60)
    hits = 0
    for i in range(200_000):
        now = 1e9 + i
        ini.last_exchange = now - 30 * 60
        hits += ini.should_speak(now) is not None
    assert hits / 200_000 == pytest.approx(p, rel=0.05)


def test_brain_turn_stores_fact_and_speaks(cfg):
    async def go():
        llm = FakeLLM(["Not aldım. <hafiza>Cuma quiz var</hafiza>", "[SUS]"])
        spk = Spk(); b = Brain(cfg, llm, Memory(cfg), spk, rng=random.Random(0))
        await b._handle(UserSaid("cuma quiz var"))
        assert spk.said == ["Not aldım."] and b.fsm.q is Q.IDLE
        assert "Cuma quiz var" in b.mem.facts()
        b.rng = random.Random(); b.rng.random = lambda: 0.0  # force interject attempt
        await b._handle(Overheard("ya bu integral niye tutmuyor"))
        assert spk.said == ["Not aldım."] and b.fsm.q is Q.IDLE  # chose silence
        assert "Cuma quiz var" in llm.calls[1][0]  # fact injected into context
    asyncio.run(go())


def test_greet_once_per_day(cfg):
    async def go():
        c = Config(db_path=cfg.db_path, quiet_hours=(0, 0))
        llm = FakeLLM(["Günaydın, uyuyan güzel."]); spk = Spk()
        b = Brain(c, llm, Memory(c), spk)
        await b._handle(FaceSeen()); await b._handle(FaceSeen())
        assert spk.said == ["Günaydın, uyuyan güzel."]
    asyncio.run(go())


def test_failover_breaker():
    async def go():
        fb = FakeLLM(["a", "b"]); f = FailoverLLM(Boom(), fb, cooldown_s=60)
        assert await f.complete("", "", []) == "a" and f.name == "fake"
        assert await f.complete("", "", []) == "b"   # breaker open: primary skipped
    asyncio.run(go())


class Status(Exception):
    def __init__(self, code): self.status_code = code


def test_permanent_error_disables_primary():
    class Bad:
        name = "claude"; calls = 0
        async def complete(self, *a):
            Bad.calls += 1; raise Status(401)
    async def go():
        f = FailoverLLM(Bad(), FakeLLM(["a", "b"]), cooldown_s=0)
        assert await f.complete("", "", []) == "a"
        assert await f.complete("", "", []) == "b"
        assert Bad.calls == 1 and f.primary is None and f.primary_error == "401 Status"
    asyncio.run(go())


def test_transient_error_retries_after_cooldown():
    class Flaky:
        name = "claude"; calls = 0
        async def complete(self, *a):
            Flaky.calls += 1; raise Status(529)
    async def go():
        f = FailoverLLM(Flaky(), FakeLLM(["a", "b"]), cooldown_s=0)
        await f.complete("", "", []); await f.complete("", "", [])
        assert Flaky.calls == 2 and f.primary is not None  # still trying
    asyncio.run(go())
