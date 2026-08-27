"""Segmenting an answer for Trendyol-TTS, and the lock that serialises it.

The segmenting is where silent data loss would live. `max_len` bounds one
`generate_streaming` call and a text over it comes back *short* with no error --
the end of the answer simply never gets spoken. Cutting on sentence boundaries
first is what makes that impossible, so the rule is pinned here rather than
trusted.

The Turkish cases are the point of most of it: this app's answers are full of
`3.031.200 TL` and `27.08.2026`, and a splitter that treats every full stop as a
sentence end cuts a number in half and puts a pause down the middle of it.
"""

import threading

import pytest

from api import voice_speech
from api.voice_speech import acquire, release, segments


# --- segmenting --------------------------------------------------------------


def test_a_short_answer_is_one_segment():
    assert segments("Kuveyt Türk en uygun seçenek.", 100) == [
        "Kuveyt Türk en uygun seçenek."
    ]


def test_sentences_are_packed_up_to_the_budget():
    assert segments("Bir cümle. İki cümle. Üç cümle.", 200) == [
        "Bir cümle. İki cümle. Üç cümle."
    ]


def test_the_cut_falls_between_sentences():
    assert segments("Bir cümle. İki cümle. Üç cümle.", 16) == [
        "Bir cümle.",
        "İki cümle.",
        "Üç cümle.",
    ]


def test_a_thousands_separator_is_not_a_sentence_end():
    """3.031.200 split into "3.031." and "200 TL" is a pause inside the number."""
    assert segments("Toplam 3.031.200 TL tutuyor. Taksit 25.260 TL.", 30) == [
        "Toplam 3.031.200 TL tutuyor.",
        "Taksit 25.260 TL.",
    ]


def test_a_date_is_not_three_sentences():
    assert segments("Oranlar 27.08.2026 tarihlidir.", 10) == [
        "Oranlar 27.08.2026 tarihlidir."
    ]


def test_a_sentence_over_the_budget_is_kept_whole():
    """A wrong pause is worse than a long segment, and cutting mid-clause is
    audible in a way a long generation is not."""
    long = "a" * 400 + "."
    assert segments(long, 50) == [long]


def test_nothing_is_dropped_however_it_is_cut():
    text = (
        "Konut finansmanı karşılaştırması. Toplam 3.031.200 TL tutuyor. "
        "En uygun seçenek Kuveyt Türk. Oranlar 27.08.2026 tarihlidir."
    )
    for budget in (10, 30, 60, 120, 500):
        assert " ".join(segments(text, budget)) == text, budget


def test_an_empty_answer_has_nothing_to_say():
    assert segments("", 100) == []
    assert segments("   ", 100) == []


def test_a_text_with_no_terminator_is_still_spoken():
    assert segments("Sonu noktasız bir cümle", 100) == ["Sonu noktasız bir cümle"]


def test_question_and_exclamation_end_a_sentence_too():
    assert segments("Öyle mi? Evet! Tamam.", 8) == ["Öyle mi?", "Evet!", "Tamam."]


# --- the queue ---------------------------------------------------------------


def test_a_second_reader_is_refused_rather_than_left_hanging():
    """The model is not thread-safe, so readings queue -- but a caller has to be
    told, and told while a status code can still be sent."""
    assert acquire(timeout=0) is True
    try:
        assert acquire(timeout=0) is False
    finally:
        release()


def test_the_lock_is_free_again_afterwards():
    assert acquire(timeout=0) is True
    release()
    assert acquire(timeout=0) is True
    release()


def test_releasing_twice_is_survivable(caplog):
    """A double release is a caller bug; it must not fail a request that has
    otherwise finished."""
    assert acquire(timeout=0) is True
    release()
    release()  # does not raise
    assert acquire(timeout=0) is True
    release()


def test_a_waiting_reader_gets_the_model_when_it_frees_up():
    assert acquire(timeout=0) is True
    got = threading.Event()

    def second():
        if acquire(timeout=2.0):
            got.set()
            release()

    waiter = threading.Thread(target=second)
    waiter.start()
    release()
    waiter.join(timeout=3.0)
    assert got.is_set()


# --- the optional dependency -------------------------------------------------


def test_a_missing_dependency_is_reported_not_raised_as_import_error(monkeypatch):
    """The speech extra is optional: the API has to start without it, and the
    endpoint has to be able to answer 503 rather than 500."""
    monkeypatch.setattr(voice_speech, "_MODEL", None)

    def no_package():
        raise voice_speech.VoiceSpeechUnavailable("not installed")

    monkeypatch.setattr(voice_speech, "_runtime", no_package)
    with pytest.raises(voice_speech.VoiceSpeechUnavailable):
        voice_speech.prepare()


def test_a_missing_checkpoint_names_the_script_that_fetches_it(monkeypatch, tmp_path):
    """The failure a new machine actually hits. Saying only "unavailable" leaves
    the reader guessing between a bad install and a missing download."""
    monkeypatch.setattr(voice_speech, "_MODEL", None)
    monkeypatch.setattr(voice_speech, "_runtime", lambda: object())
    monkeypatch.setattr(
        voice_speech.settings, "SPEECH_MODEL_PATH", str(tmp_path / "absent")
    )
    with pytest.raises(voice_speech.VoiceSpeechUnavailable) as caught:
        voice_speech.prepare()
    assert "download_speech_model.py" in str(caught.value)


def test_a_half_downloaded_checkpoint_is_not_treated_as_ready(monkeypatch, tmp_path):
    """A request must never start a multi-gigabyte download, and it must not load
    a directory that is still receiving one either."""
    monkeypatch.setattr(voice_speech, "_MODEL", None)
    monkeypatch.setattr(voice_speech, "_runtime", lambda: object())
    checkpoint = tmp_path / "Trendyol-TTS"
    checkpoint.mkdir()
    (checkpoint / "config.json").write_text("{}")
    (checkpoint / "model.safetensors.incomplete").write_text("")
    monkeypatch.setattr(voice_speech.settings, "SPEECH_MODEL_PATH", str(checkpoint))
    with pytest.raises(voice_speech.VoiceSpeechUnavailable) as caught:
        voice_speech.prepare()
    assert "incomplete" in str(caught.value)


def test_the_model_is_loaded_from_the_local_path_and_never_the_network(
    monkeypatch, tmp_path
):
    """Both halves matter. The path keeps the checkpoint under TF26_data beside
    the Whisper one, and `local_files_only` is what stops a cache miss turning a
    user's request into a five-gigabyte download."""
    monkeypatch.setattr(voice_speech, "_MODEL", None)
    checkpoint = tmp_path / "Trendyol-TTS"
    checkpoint.mkdir()
    (checkpoint / "config.json").write_text("{}")
    monkeypatch.setattr(voice_speech.settings, "SPEECH_MODEL_PATH", str(checkpoint))

    seen: dict = {}

    class _FakeVoxCPM:
        @staticmethod
        def from_pretrained(**kwargs):
            seen.update(kwargs)
            return _FakeModel()

    monkeypatch.setattr(voice_speech, "_runtime", lambda: _FakeVoxCPM)
    voice_speech.prepare()
    monkeypatch.setattr(voice_speech, "_MODEL", None)

    assert seen["hf_model_id"] == str(checkpoint.resolve())
    assert seen["local_files_only"] is True
    assert seen["load_denoiser"] is False
    # MPS, not MLX -- see TTS_ENTEGRASYON.md section 9.
    assert seen["device"] == "mps"


def test_the_declared_sample_rate_is_used_before_the_model_loads(monkeypatch):
    """The browser builds its AudioContext from this. A wrong rate does not
    fail, it plays the answer at the wrong pitch."""
    monkeypatch.setattr(voice_speech, "_MODEL", None)
    assert voice_speech.sample_rate() == 48_000


# --- streaming ---------------------------------------------------------------


class _FakeModel:
    """The model's surface as `speak` uses it: a rate, and a chunk generator.

    Standing in for a 5 GB checkpoint so the parts we actually wrote -- the
    segment loop, the float-to-PCM conversion, the clipping -- are verified
    without one. What it cannot verify is voxcpm's own call signature; that
    needs the real package.
    """

    def __init__(self, chunks_per_call=None):
        import numpy as np

        self.tts_model = type("T", (), {"sample_rate": 48_000})()
        self.calls: list[str] = []
        self._chunks = chunks_per_call or [np.array([0.0, 0.5, -0.5], dtype="float32")]

    def generate_streaming(self, *, text, **kwargs):
        self.calls.append(text)
        yield from self._chunks


@pytest.fixture
def fake_model(monkeypatch):
    def install(model):
        monkeypatch.setattr(voice_speech, "_MODEL", model)
        return model

    yield install
    monkeypatch.setattr(voice_speech, "_MODEL", None)


def test_each_segment_is_generated_into_one_stream(fake_model):
    """A long answer is several generate_streaming calls and one continuous
    reading -- the listener must not hear where the segments meet."""
    model = fake_model(_FakeModel())
    list(voice_speech.speak("Bir cümle. İki cümle. Üç cümle."))
    assert len(model.calls) >= 1
    assert " ".join(model.calls) == "Bir cümle. İki cümle. Üç cümle."


def test_audio_leaves_as_little_endian_16_bit_pcm(fake_model):
    import numpy as np

    fake_model(_FakeModel([np.array([0.0, 1.0, -1.0], dtype="float32")]))
    payload = b"".join(voice_speech.speak("Merhaba."))
    assert np.frombuffer(payload, dtype="<i2").tolist() == [0, 32767, -32767]


def test_a_sample_over_full_scale_is_clipped_not_wrapped(fake_model):
    """Without the clip, 1.5 overflows int16 and a loud passage becomes a burst
    of noise rather than a loud passage."""
    import numpy as np

    fake_model(_FakeModel([np.array([1.5, -1.5], dtype="float32")]))
    payload = b"".join(voice_speech.speak("Merhaba."))
    assert np.frombuffer(payload, dtype="<i2").tolist() == [32767, -32767]


def test_every_chunk_holds_a_whole_number_of_samples(fake_model):
    """The browser reassembles a byte stream into int16s; an odd-length chunk
    would shift every sample after it and turn the reading into noise."""
    import numpy as np

    fake_model(_FakeModel([np.zeros(7, dtype="float32"), np.zeros(3, dtype="float32")]))
    for chunk in voice_speech.speak("Merhaba."):
        assert len(chunk) % 2 == 0


def test_a_model_that_fails_mid_reading_is_reported_as_a_speech_failure(fake_model):
    class _Broken(_FakeModel):
        def generate_streaming(self, *, text, **kwargs):
            raise RuntimeError("mps out of memory")
            yield  # pragma: no cover - generator marker

    fake_model(_Broken())
    with pytest.raises(voice_speech.VoiceSpeechFailed):
        list(voice_speech.speak("Merhaba."))


def test_the_measured_parameters_are_the_ones_actually_sent(fake_model):
    """These are measured values, not preferences -- timesteps=2 makes the model
    skip text outright and cfg 2.5 clips. A silent drift here is a quality
    regression nobody would trace back to this call."""
    seen: dict = {}

    class _Recording(_FakeModel):
        def generate_streaming(self, *, text, **kwargs):
            seen.update(kwargs)
            yield from self._chunks

    fake_model(_Recording())
    list(voice_speech.speak("Merhaba."))
    # 4, not the guide's 10: on this hardware 10 generates slower than it
    # plays. See SPEECH_TIMESTEPS for the measurements.
    assert seen["inference_timesteps"] == 4
    assert seen["cfg_value"] == 2.0
    assert seen["max_len"] == 4096
    # Off: voxcpm's normaliser is wetext's English model and it raises on
    # "%2,89" and says "teraliters" for TL. See SPEECH_NORMALIZE.
    assert seen["normalize"] is False
    assert seen["denoise"] is False
