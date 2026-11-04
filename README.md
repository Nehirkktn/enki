# Enki — Brain (Phase 1.1)

```
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...          # yoksa direkt Ollama'ya düşer
ollama pull qwen2.5:3b                # offline yedek beyin
python -m enki                        # ENKI_DEBUG_FSM=1 → state geçişlerini gör
pytest -q
```

Komutlar: `/kendi <metin>` (kendi kendine konuşma), `/yuz` (kamera seni gördü), `/hafiza`, `/cik`.

| Modül | Sorumluluk | Ders bağlantısı |
|---|---|---|
| `fsm.py` | DFA: IDLE/LISTENING/THINKING/SPEAKING/SLEEPING | Otomata |
| `memory.py` | SQLite + hashing-trick embedding, cosine recall = `E @ q` | Lineer Cebir |
| `initiative.py` | Non-homogeneous Poisson ile kendiliğinden konuşma | Olasılık |
| `llm.py` | Claude → Ollama failover, circuit breaker, prompt cache | — |
| `brain.py` | Tek consumer'lı event loop (Ports & Adapters) | — |

## Ses (Phase 1.1b)

```
python tools/voice_compare.py          # bulunan tüm sesleri aynı cümlelerle çal + ms ölç
ENKI_TTS=openai,piper python -m enki     # Enki sesli konuşur; openai düşerse say'e geçer
```

| Backend | Gereken env | Not |
|---|---|---|
| say | — (macOS) | Yelda sesi: Sistem Ayarları → Erişilebilirlik → Seslendirilmiş İçerik'ten indir |
| openai | `OPENAI_API_KEY` | platform.openai.com, ses `ENKI_OPENAI_VOICE` (onyx, ash, sage…) |
| google | `GOOGLE_TTS_API_KEY` | Cloud Console → Text-to-Speech API'yi aç → API key |
| azure | `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION` | Azure Portal → Speech resource (F0 = ücretsiz) |
| piper | `ENKI_PIPER_MODEL=/yol/tr_TR-....onnx` | `pip install piper-tts`, model: huggingface.co/rhasspy/piper-voices |
