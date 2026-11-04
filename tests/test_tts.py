import asyncio, base64, json
import httpx, pytest
from enki.tts import Audio, AzureTTS, FailoverTTS, GoogleTTS, OpenAITTS


def client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_openai_request_shape():
    seen = {}
    def h(req):
        seen["url"], seen["auth"], seen["body"] = str(req.url), req.headers["authorization"], json.loads(req.content)
        return httpx.Response(200, content=b"RIFFwav")
    a = asyncio.run(OpenAITTS("k", http=client(h)).synth("selam"))
    assert a == Audio(b"RIFFwav", "wav")
    assert seen["url"].endswith("/v1/audio/speech") and seen["auth"] == "Bearer k"
    assert seen["body"]["model"] == "gpt-4o-mini-tts" and seen["body"]["instructions"]


def test_google_decodes_base64():
    def h(req):
        b = json.loads(req.content)
        assert b["voice"]["languageCode"] == "tr-TR" and req.url.params["key"] == "k"
        return httpx.Response(200, json={"audioContent": base64.b64encode(b"PCM").decode()})
    assert asyncio.run(GoogleTTS("k", http=client(h)).synth("x")).data == b"PCM"


def test_azure_escapes_ssml():
    def h(req):
        assert b"&lt;b&gt; &amp;" in req.content and "westeurope" in str(req.url)
        return httpx.Response(200, content=b"W")
    asyncio.run(AzureTTS("k", "westeurope", http=client(h)).synth("<b> &"))


def test_failover_chain():
    class Bad:
        name = "bad"
        async def synth(self, t): raise ConnectionError("offline")
    class Good:
        name = "good"
        async def synth(self, t): return Audio(b"ok", "wav")
    f = FailoverTTS([Bad(), Good()])
    assert asyncio.run(f.synth("x")).data == b"ok" and f.name == "good"
    with pytest.raises(RuntimeError):
        asyncio.run(FailoverTTS([Bad()]).synth("x"))
