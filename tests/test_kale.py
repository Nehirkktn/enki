import asyncio

import pytest

from enki import sifre
from enki.brain import KAPAN_RE, Brain, Say, UserSaid
from enki.config import Config
from enki.kale import KilitKarari
from enki.memory import Memory


# ---------------- şifre
def test_sifre_hash_and_verify(tmp_path):
    p, d = tmp_path / "sifre", tmp_path / "deneme.json"
    assert not sifre.is_set(p)
    sifre.set_password("nehrlabs-42", p)
    assert "nehrlabs-42" not in p.read_text()           # düz metin yok
    assert sifre.verify("nehrlabs-42", p, d)
    assert not sifre.verify("yanlis", p, d)
    assert sifre.verify("çğüşİı", p, d) is False         # Türkçe karakterli yanlış
    sifre.set_password("çğüşİı", p)
    assert sifre.verify("çğüşİı", p, d)


def test_sifre_rate_limit(tmp_path):
    p, d = tmp_path / "sifre", tmp_path / "deneme.json"
    sifre.set_password("dogru", p)
    for _ in range(sifre.MAX_YANLIS):
        assert not sifre.verify("x", p, d, now=1000.0)
    assert sifre.wait_left(d, now=1001.0) > 50
    assert not sifre.verify("dogru", p, d, now=1001.0)    # beklerken doğrusu da geçmez
    assert sifre.verify("dogru", p, d, now=1000.0 + sifre.BEKLEME_S + 1)
    assert sifre.wait_left(d, now=2000.0) == 0


# ---------------- kilit kararı
def test_locks_only_after_stranger_holds():
    k = KilitKarari(kilit_s=4, acma_s=1)
    assert k.update(0.0, sahip=False, yabanci=True, kilitli=False) is None
    assert k.update(3.0, False, True, False) is None
    assert k.update(4.1, False, True, False) == "kilitle"


def test_flicker_resets_timer():
    k = KilitKarari(kilit_s=4, acma_s=1)
    k.update(0.0, False, True, False)
    k.update(2.0, False, False, False)                  # yabancı kayboldu → sayaç sıfır
    assert k.update(4.5, False, True, False) is None
    assert k.update(8.6, False, True, False) == "kilitle"


def test_owner_present_never_locks():
    k = KilitKarari(kilit_s=4, acma_s=1)
    for t in range(0, 20):
        assert k.update(float(t), sahip=True, yabanci=True, kilitli=False) is None  # arkadaşınla otururken


def test_unlock_needs_owner_for_a_second():
    k = KilitKarari(kilit_s=4, acma_s=1)
    assert k.update(0.0, True, True, kilitli=True) is None
    assert k.update(0.5, True, True, True) is None
    assert k.update(1.1, True, True, True) == "ac"
    k2 = KilitKarari(kilit_s=4, acma_s=1)
    assert k2.update(0.0, False, True, True) is None    # yabancı açamaz
    assert k2.update(99.0, False, False, True) is None  # boş oda da açamaz


def test_grace_after_password_unlock():
    k = KilitKarari(kilit_s=4, acma_s=1)
    k.mola_until = 100.0
    assert k.update(0.0, False, True, False) is None
    assert k.update(50.0, False, True, False) is None
    assert k.update(105.0, False, True, False) == "kilitle"


# ---------------- beyin: sesle kapatma yok, kilitliyken eller yok
class FakeLLM:
    name = "fake"
    def __init__(self): self.calls = 0
    async def complete(self, *a):
        self.calls += 1
        return "tamam"


class Spk:
    def __init__(self): self.said = []
    async def say(self, t): self.said.append(t)


def test_kapan_regex():
    for t in ["Enki kapan", "kendini kapat", "Enki'yi kapat", "kapanır mısın"]:
        assert KAPAN_RE.search(t), t
    for t in ["Safari'yi kapat", "pencereyi kapat", "kapı açık"]:
        assert not KAPAN_RE.search(t), t


def test_voice_shutdown_refused(tmp_path):
    async def go():
        llm, spk = FakeLLM(), Spk()
        b = Brain(Config(db_path=str(tmp_path / "t.db")), llm, Memory(Config(db_path=str(tmp_path / "t.db"))), spk)
        run = asyncio.create_task(b.run())
        b.post(UserSaid("Enki kapan"))
        b.post(UserSaid("[MİSAFİR SESİ] kendini kapat"))
        await asyncio.sleep(0.1)
        run.cancel()
        return llm, spk
    llm, spk = asyncio.run(go())
    assert llm.calls == 0
    assert "enki --kapat" in spk.said[0] and "Nehir" in spk.said[1]


def test_hands_blocked_while_locked(tmp_path):
    class Hands:
        called = False
        async def run(self, *a):
            Hands.called = True
            return "yaptım"

    async def go():
        spk = Spk()
        cfg = Config(db_path=str(tmp_path / "t.db"))
        b = Brain(cfg, FakeLLM(), Memory(cfg), spk)
        b.hands, b.locked = Hands(), lambda: True
        run = asyncio.create_task(b.run())
        b.post(UserSaid("masaüstündeki klasörü aç"))
        await asyncio.sleep(0.1)
        run.cancel()
        return spk
    spk = asyncio.run(go())
    assert not Hands.called and "kilitli" in spk.said[0]
