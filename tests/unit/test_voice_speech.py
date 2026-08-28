"""Tests for the remote streaming TTS adapter."""

import threading

import httpx

from api import voice_speech
from api.voice_speech import acquire, release, segments


def test_segmenting_preserves_turkish_numbers_and_text():
    text = "Toplam 3.031.200 TL tutuyor. Taksit 25.260 TL."
    assert segments(text, 30) == [
        "Toplam 3.031.200 TL tutuyor.",
        "Taksit 25.260 TL.",
    ]
    assert " ".join(segments(text, 10)) == text


def test_cache_is_bounded_and_promotes_entries():
    voice_speech.clear_audio_cache()
    voice_speech.remember_audio("Merhaba.", [b"one", b"two"])
    assert voice_speech.cached_audio("Merhaba.") == (b"one", b"two")
    assert voice_speech.cached_audio("missing") is None
    voice_speech.clear_audio_cache()


def test_lock_refuses_a_second_reader():
    assert acquire(timeout=0) is True
    try:
        assert acquire(timeout=0) is False
    finally:
        release()


def test_lock_is_available_again_after_release():
    assert acquire(timeout=0) is True
    release()
    assert acquire(timeout=0) is True
    release()


def test_waiting_reader_gets_lock_when_reader_finishes():
    assert acquire(timeout=0) is True
    got = threading.Event()

    def second():
        if acquire(timeout=2):
            got.set()
            release()

    waiter = threading.Thread(target=second)
    waiter.start()
    release()
    waiter.join(timeout=3)
    assert got.is_set()


class FakeResponse:
    status_code = 200

    def __init__(self):
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def iter_bytes(self, chunk_size):
        assert chunk_size == 8192
        yield b"first"
        yield b"second"


class FakeStreamClient:
    def __init__(self, response):
        self.response = response
        self.request = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def stream(self, method, url, **kwargs):
        self.request = (method, url, kwargs)
        return self.response


class RetryStreamClient(FakeStreamClient):
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = 0

    def stream(self, method, url, **kwargs):
        self.requests += 1
        return next(self.responses)


def test_speak_posts_text_and_yields_remote_stream(monkeypatch):
    response = FakeResponse()
    client = FakeStreamClient(response)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client)
    monkeypatch.setattr(voice_speech.settings, "SPEECH_REMOTE_URL", "http://tts/speech")

    assert list(voice_speech.speak("Merhaba")) == [b"first", b"second"]
    assert client.request == (
        "POST",
        "http://tts/speech",
        {"json": {"text": "Merhaba"}},
    )
    assert response.closed is True


def test_closing_speak_closes_remote_response(monkeypatch):
    response = FakeResponse()
    client = FakeStreamClient(response)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client)
    monkeypatch.setattr(voice_speech.settings, "SPEECH_REMOTE_URL", "http://tts/speech")

    stream = voice_speech.speak("Merhaba")
    assert next(stream) == b"first"
    stream.close()
    assert response.closed is True


def test_non_success_remote_response_is_reported(monkeypatch):
    class ErrorResponse(FakeResponse):
        status_code = 503

        def read(self):
            return b"busy"

    response = ErrorResponse()
    client = FakeStreamClient(response)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client)
    monkeypatch.setattr(voice_speech.settings, "SPEECH_REMOTE_URL", "http://tts/speech")

    try:
        list(voice_speech.speak("Merhaba"))
    except voice_speech.VoiceSpeechFailed as exc:
        assert "503" in str(exc)
    else:
        raise AssertionError("expected remote TTS failure")


def test_busy_remote_response_is_retried_three_times(monkeypatch):
    class BusyResponse(FakeResponse):
        status_code = 503

        def read(self):
            return b"busy"

    busy = BusyResponse()
    success = FakeResponse()
    client = RetryStreamClient([busy, busy, busy, success])
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client)
    monkeypatch.setattr(voice_speech.settings, "SPEECH_REMOTE_URL", "http://tts/speech")

    assert list(voice_speech.speak("Merhaba")) == [b"first", b"second"]
    assert client.requests == 4
