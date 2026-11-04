import asyncio, random
import numpy as np

from enki import tts
from enki.tts import EnkiVoice, LaughPicker, Segment, contour, segments, strip_tags


def test_segments_tags_and_laughs():
    s = segments("[smug] Tabii. Üçüncü denemede. [laugh] Şaka şaka. [angry] [stern] Vize cuma.")
    assert s == [
        Segment("speech", "smug", "Tabii. Üçüncü denemede."),
        Segment("laugh", laugh="kisa"),
        Segment("speech", "laugh", "Şaka şaka."),
        Segment("speech", "stern", "Vize cuma."),
    ]
    assert segments("[kahkaha] Tamam bu iyiydi.")[0] == Segment("laugh", laugh="uzun")
    assert segments("[laughbig] x")[0].laugh == "uzun"  # brain'in _MD_RE'si alt çizgiyi silerse


def test_strip_tags():
    assert strip_tags("[smug] Tabii.  [laugh] Şaka.") == "Tabii. Şaka."


def test_contour_question_rises_statement_falls():
    assert contour("Gerçekten mi?", 0, 1)[1] > 0
    first, last = contour("A.", 0, 3)[1], contour("C.", 2, 3)[1]
    assert first > last


def test_laugh_picker_no_back_to_back_repeat(tmp_path):
    (tmp_path / "kisa").mkdir()
    for n in ("a", "b"):
        (tmp_path / "kisa" / f"{n}.wav").write_bytes(b"")
    p = LaughPicker(tmp_path, random.Random(0))
    picks = [p.pick("kisa").name for _ in range(10)]
    assert all(a != b for a, b in zip(picks, picks[1:]))
    assert p.pick("uzun") is None


def test_render_inserts_laugh_at_speech_level(tmp_path):
    (tmp_path / "kisa").mkdir()
    laugh = (np.sin(np.arange(tts.SR // 2) / 5) * 20000).astype(np.int16)
    tts.write_wav(tmp_path / "kisa" / "k.wav", laugh)
    calls = []

    async def fake_synth(text, rate, pitch, volume):
        calls.append((text, rate, pitch, volume))
        return (np.sin(np.arange(tts.SR // 4) / 7) * 3000).astype(np.int16)

    v = EnkiVoice(fake_synth, LaughPicker(tmp_path), effect=None)
    pcm = asyncio.run(v.render("[laugh] Şaka şaka. [stern] Vize cuma."))
    assert pcm is not None and pcm.dtype == np.int16
    assert len(pcm) > tts.SR  # 2 cümle + gülme
    assert {c[2] for c in calls} <= {tts.BASE_PITCH + d for d in range(-5, 11)}  # kimlik sabit: sadece küçük kontur
    assert calls[-1][3] == tts.TON["stern"][1]
    assert abs(pcm).max() < 6000  # gülme 20000'den konuşma seviyesine indirildi


def test_render_empty_and_missing_laugh(tmp_path):
    async def fake_synth(*a):
        return np.zeros(10, np.int16)
    v = EnkiVoice(fake_synth, LaughPicker(tmp_path), effect=None)
    assert asyncio.run(v.render("")) is None
    assert asyncio.run(v.render("[laugh]")) is None  # kayıt yoksa sessizce atlar
