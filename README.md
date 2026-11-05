# Enki: tek beyin, çok gövde

Nehir'in robotu. Tek bir zihin (hafıza + kişilik + karar) farklı gövdelerden konuşur:
Mac'in terminali, kamera ve mikrofonu, masaüstü yüzü, telefon. İleride oda node'ları (ESP32) da aynı yoldan bağlanacak.

```
pip install -r requirements.txt
python -m enki --kayit        # yüzünü öğret (bir kez)
python -m enki                # her şey açık; --no-cam --no-mic --no-hub --sessiz
pytest -q
```

```
           ┌──────── gövdeler ─────────┐
 terminal ─┤ UserSaid("…", "konsol")   │
 mikrofon ─┤ ear.py: Whisper + "Enki"/bakış/sohbet-devam → UserSaid | Overheard
   kamera ─┤ vision.py: YuNet+SFace → FaceSeen / FaceLeft
  telefon ─┤ hub.py  ws://mac:8765  {"said": …}
           └────────────┬──────────────┘
                        ▼
              brain.py  (tek asyncio kuyruğu, tek tüketici)
                        ▼
              govde.py BodySpeaker ─ Nehir hangi gövdeden konuştuysa cevap oradan çıkar
                        ├─ Mac hoparlörü (EnkiVoice)
                        ├─ telefon: WAV + altyazı
                        └─ gözler (her gövde): {"state": "smug", "text": …}
```

| Modül | Sorumluluk | Ders bağlantısı |
|---|---|---|
| `fsm.py` | DFA: IDLE/LISTENING/THINKING/SPEAKING/SLEEPING, gözler geçişleri dinler | Otomata |
| `memory.py` | SQLite + hashing-trick embedding, cosine recall = `E @ q` | Lineer Cebir |
| `initiative.py` | Non-homogeneous Poisson ile kendiliğinden konuşma | Olasılık |
| `senses/perception/` | YuNet + SFace, sahip galerisi `max(G·e)`, presence DFA (histerezis) | Lineer Cebir, Otomata |
| `senses/ear.py` | VAD segmenter + Whisper, "bana mı konuşuyor" kararı | — |
| `hub.py` | Gövde protokolü, anahtar, HTTP gövde sayfaları | — |
| `govde.py` | Speaker portu: gövdeye yönlendirme, etiket → göz duygusu | — |
| `tts.py` | Ahmet + TON + JARVIS + gerçek gülme kayıtları | Sinyal işleme |

## Gövde protokolü (ws://<mac-ip>:8765)

Gövde → beyin: `{"hello": {"body": "telefon", "caps": ["face","text","speaker"], "key": "…"}}`, sonra
`{"said": "…"}`, `{"overheard": "…"}`, `{"seen": "nehir"}`, `{"left": "nehir"}`.
Beyin → gövde: `{"state": …, "text": …}` (göz), `{"say": …, "audio": <base64 wav>}` (sadece konuşulan gövdeye).
Localhost'tan gelen bağlantı anahtarsız göz ekranıdır (enki-yuz.html, enki_masaustu.py). Ağdan gelen her gövde
`data/govde_anahtari` ile `hello` göndermek zorunda: yurt Wi-Fi'ı herkese açık.
