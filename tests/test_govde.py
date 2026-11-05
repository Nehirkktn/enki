import asyncio, json

import numpy as np

from enki.brain import Brain, FaceLeft, FaceSeen, Overheard, UserSaid
from enki.config import Config
from enki.fsm import Q, Sigma
from enki.govde import BodySpeaker, face_emotion, fsm_face
from enki.hub import Body, handle, wav_b64
from enki.senses.ear import addressed
from enki.senses.perception.events import Event as PEvent, EventType
from enki.senses.vision import to_brain_event
from enki.memory import Memory


# ---------------- hub protokolü
def test_untrusted_body_needs_key():
    b = Body("misafir@1.2.3.4")
    assert handle(b, {"said": "selam"}, "k") is None             # anahtarsız: hiçbir şey
    assert handle(b, {"hello": {"body": "telefon", "key": "yanlis"}}, "k") is None
    assert not b.trusted
    handle(b, {"hello": {"body": "telefon", "caps": ["face", "speaker"], "key": "k"}}, "k")
    assert b.trusted and b.name == "telefon" and "speaker" in b.caps
    assert handle(b, {"said": " selam "}, "k") == UserSaid("selam", "telefon")
    assert handle(b, {"overheard": "of"}, "k") == Overheard("of", "telefon")
    assert handle(b, {"seen": "nehir"}, "k") == FaceSeen("nehir", "telefon")
    assert handle(b, {"left": "nehir"}, "k") == FaceLeft("nehir", "telefon")


def test_local_legacy_face_is_trusted_without_hello():
    b = Body("yuz", trusted=True)
    assert handle(b, {"said": "x"}, "k") == UserSaid("x", "yuz")


def test_wav_b64_roundtrip_header():
    import base64
    raw = base64.b64decode(wav_b64(np.zeros(2400), 24000))
    assert raw[:4] == b"RIFF" and raw[8:12] == b"WAVE"


# ---------------- gözler
def test_face_emotion_from_tags():
    assert face_emotion("[smug] Tabii. [laugh]") == "smug"
    assert face_emotion("[stern] Uyu artık.") == "angry"
    assert face_emotion("[kahkaha]") == "laugh"
    assert face_emotion("[surprised] Ne?") == "surprised"
    assert face_emotion("düz cevap") == "speaking"


def test_fsm_face_leaves_speaking_to_speaker():
    assert fsm_face(Q.IDLE, Sigma.WAKE, Q.LISTENING) == "listening"
    assert fsm_face(Q.LISTENING, Sigma.HEARD, Q.THINKING) == "thinking"
    assert fsm_face(Q.THINKING, Sigma.REPLY_READY, Q.SPEAKING) is None
    assert fsm_face(Q.SPEAKING, Sigma.DONE_SPEAKING, Q.IDLE) is None
    assert fsm_face(Q.THINKING, Sigma.SILENT, Q.IDLE) == "idle"


# ---------------- konuşmacı yönlendirme
class FakeHub:
    def __init__(self, bodies): self.bodies, self.faces, self.sent = bodies, [], []
    def has(self, name, cap): return name in self.bodies
    async def face(self, s, t=None): self.faces.append(s)
    async def send_to(self, name, msg): self.sent.append((name, msg))


class Rec:
    def __init__(self): self.said = []
    async def say(self, t): self.said.append(t)


class FakeVoice:
    async def render(self, text): return np.zeros(240)


def test_reply_goes_to_body_she_spoke_from():
    hub, local, printer = FakeHub({"telefon"}), Rec(), Rec()
    active = ["konsol"]
    sp = BodySpeaker(hub, local, printer, lambda: active[0], FakeVoice())
    asyncio.run(sp.say("[blush] Teşekkürler."))
    assert local.said == ["[blush] Teşekkürler."] and hub.sent == []
    assert hub.faces == ["blush", "idle"]
    active[0] = "telefon"
    asyncio.run(sp.say("[smug] Tabii."))
    name, msg = hub.sent[0]
    assert name == "telefon" and msg["say"] == "Tabii." and "audio" in msg
    assert len(local.said) == 1 and printer.said  # Mac hoparlörü susar, terminal yine yazar
    assert not sp.muted.is_set() and sp.last_spoke > 0


# ---------------- duyular
def test_vision_events_map_to_brain():
    assert to_brain_event(PEvent(EventType.OWNER_ARRIVED), "mac") == FaceSeen("nehir", "mac")
    assert to_brain_event(PEvent(EventType.STRANGER_APPEARED), "mac") == FaceSeen("yabanci", "mac")
    assert to_brain_event(PEvent(EventType.OWNER_LEFT), "mac") == FaceLeft("nehir", "mac")
    assert to_brain_event(PEvent(EventType.MOTION_START), "mac") is None


def test_ear_addressing():
    no_gaze = lambda a, b: 0.0
    assert addressed("Enki saat kaç", 0, 1, 100, 0, no_gaze)[0]
    assert addressed("ENKİ bak", 0, 1, 100, 0, no_gaze)[0]          # Türkçe büyük İ
    assert addressed("tamam olur", 0, 1, 100, 95, no_gaze) == (True, "sohbet devam")
    assert addressed("of ya bu ödev", 0, 1, 100, 0, lambda a, b: 0.8)[0]
    assert not addressed("of ya bu ödev", 0, 1, 100, 0, no_gaze)[0]
    assert not addressed("enkaz", 0, 1, 100, 0, no_gaze)[0]          # 'enka' öneki tetiklememeli


# ---------------- beyin
class FakeLLM:
    name = "fake"
    def __init__(self): self.contexts = []
    async def complete(self, core, context, messages):
        self.contexts.append(context); return "Tamam."


def test_brain_tracks_body_and_presence(tmp_path):
    cfg = Config(db_path=str(tmp_path / "t.db"))
    llm, spk = FakeLLM(), Rec()
    b = Brain(cfg, llm, Memory(cfg), spk)
    async def go():
        t = asyncio.create_task(b.run())
        b.post_threadsafe(FaceSeen("yabanci", "mac"))
        b.post(UserSaid("selam", "telefon"))
        await asyncio.sleep(0.05)
        from enki.brain import Shutdown
        b.post(Shutdown()); await t
    asyncio.run(go())
    assert b.active_body == "telefon" and b.present == set()
    assert "telefon" in llm.contexts[0]
