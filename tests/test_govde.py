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


def test_typing_mode_ignores_everything_but_name():
    looking = lambda a, b: 1.0
    assert addressed("bunu nasıl yazıyordum", 0, 1, 100, 95, looking, last_typed=50) == (None, "yazıyor")
    assert addressed("Enki bak", 0, 1, 100, 0, looking, last_typed=99)[0] is True
    assert addressed("bunu nasıl yazıyordum", 0, 1, 1000, 0, looking, last_typed=50)[0] is True  # yazma bitti


def test_visual_question_attaches_snapshot(tmp_path):
    class ImgLLM:
        name = "fake"
        def __init__(self): self.msgs = None
        async def complete(self, core, context, messages):
            self.msgs = messages; return "Mavi tişört."
    cfg = Config(db_path=str(tmp_path / "t.db"))
    llm = ImgLLM()
    b = Brain(cfg, llm, Memory(cfg), Rec(), eyes=lambda: b"\xff\xd8jpeg")
    async def go():
        await b._handle(UserSaid("beni görüyor musun", "konsol"))
        first = llm.msgs[-1]["content"]
        b.fsm.reset()
        await b._handle(UserSaid("saat kaç", "konsol"))
        return first, llm.msgs[-1]["content"]
    first, second = asyncio.run(go())
    assert isinstance(first, list) and first[0]["type"] == "image"
    assert isinstance(second, str)
    assert "görüyor" in b.mem.recent(4)[0].text  # hafızaya sadece metin girer


def test_ollama_flattens_images():
    from enki.llm import as_text
    assert "yerel beyin" in as_text([{"type": "image"}, {"type": "text", "text": "selam"}])


def test_bare_name_is_not_a_question():
    from enki.senses.ear import only_name
    assert only_name("Enki") and only_name(" Enki? ") and only_name("ENKİ.")
    assert not only_name("Enki saat kaç")
    no_gaze = lambda a, b: 0.0
    assert addressed("Duyursun", 0, 1, 100, 95, no_gaze) == (None, "tek kelime")


def test_visual_regex_no_false_hits():
    from enki.brain import VISUAL_RE
    assert VISUAL_RE.search("beni görüyor musun")
    assert VISUAL_RE.search("elimde ne var")
    assert not VISUAL_RE.search("Şu an üzerinde çalışıyorum. O yüzden yapalım.")
    assert not VISUAL_RE.search("kalın değil")


def test_only_enki_dur_interrupts():
    from enki.senses.ear import barge_in, is_echo, stop_command
    speaking = "Vize cuma, lineer cebir çalışmadın dur bakalım."
    assert stop_command("Enki dur") and stop_command("dur Enki!") and stop_command("ENKİ DUR")
    assert not stop_command("dur") and not stop_command("Enki")
    assert not stop_command("Enki durum ne")            # 'durum' ≠ 'dur'
    assert barge_in("Enki dur", speaking)
    assert not barge_in("sus", speaking)
    assert is_echo("lineer cebir çalışmadın", speaking)


def test_speaker_interrupt_stops_remote_wait():
    hub, local, printer = FakeHub({"telefon"}), Rec(), Rec()
    class LongVoice:
        async def render(self, t): return np.zeros(24000 * 30)  # 30 sn
    sp = BodySpeaker(hub, local, printer, lambda: "telefon", LongVoice())
    async def go():
        t = asyncio.create_task(sp.say("uzun bir konuşma"))
        await asyncio.sleep(0.05)
        assert sp.current == "uzun bir konuşma"
        sp.interrupt()
        await asyncio.wait_for(t, 1)
    asyncio.run(go())
    assert sp.interrupted and ("telefon", {"stop": True}) in hub.sent and hub.faces[-1] == "listening"


def test_company_needs_3_seconds():
    from enki.senses.vision import VisionSense
    v = VisionSense(lambda e: None, None)
    for t in (0, 1, 2):
        v._track_company(1, float(t))
    assert v.others == 0            # 2 sn titreme: misafir değil
    v._track_company(1, 3.1)
    assert v.others == 1
    v._track_company(0, 4); v._track_company(0, 7.5)
    assert v.others == 0


def test_context_mentions_guest():
    from datetime import datetime
    from enki import persona
    c = persona.dynamic_context([], [], datetime.now(), present=["mac"], others=1)
    assert "1 KİŞİ DAHA" in c


# ---------------- ses kimliği (gerçek model yerine sahte encoder: ses "kimliği" = sabit yön)
class FakeEnc:
    def __call__(self, audio):
        v = np.zeros(256, np.float32); v[0 if audio.mean() > 0 else 1] = 1.0
        v[2] = 0.3
        return v


def test_voiceid_owner_vs_guest(tmp_path):
    from enki.senses.voiceid import VoiceID, SR
    vid = VoiceID(tmp_path / "s.npz", encoder=FakeEnc())
    nehir, misafir = np.full(SR * 2, 0.1, np.float32), np.full(SR * 2, -0.1, np.float32)
    assert vid.is_owner(nehir) == (None, None)            # kayıt yok: karar yok
    for _ in range(3):
        vid.add(vid.embed(nehir))
    vid.save()
    vid2 = VoiceID(tmp_path / "s.npz", encoder=FakeEnc())
    assert vid2.is_owner(nehir)[0] is True
    assert vid2.is_owner(misafir)[0] is False
    assert vid2.is_owner(nehir[: SR // 2])[0] is None     # < 1 sn: güvenilmez
