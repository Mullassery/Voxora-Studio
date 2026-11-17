"""Tests for the long-form chunker and run engine (Phase D, Tasks 1-2)."""

import io
import wave as wave_mod

import pytest

from voxora.config import Settings
from voxora.longform import (
    MAX_CHUNKS,
    chunk_script,
    generate_long_form,
    retry_long_form,
    run_locks,
)
from voxora.tts_generation import MockTTSProvider, TTSProviderError, TTSRequest


def _para(n_chars: int, seed: int = 0) -> str:
    words = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet"]
    out: list[str] = []
    size = 0
    i = 0
    while size < n_chars:
        w = words[(i + seed) % len(words)]
        out.append(w)
        size += len(w) + 1
        i += 1
    return " ".join(out)

def test_short_script_single_chunk_verbatim():
    script = "Hello world. This is a test."
    chunks = chunk_script(script, 4096)
    assert chunks == [script]

def test_paragraphs_merged_never_split_when_each_fits():
    p1, p2 = _para(1500, 0), _para(1500, 3)
    script = p1 + "\n\n" + p2
    chunks = chunk_script(script, 4096)
    assert len(chunks) == 1
    assert chunks[0] == script  # merged, no paragraph split

def test_merge_respects_limit_boundary_between_paragraphs():
    p1, p2, p3 = _para(1500, 0), _para(1500, 3), _para(1500, 5)
    script = "\n\n".join([p1, p2, p3])
    chunks = chunk_script(script, 4096)
    assert len(chunks) == 2
    assert chunks[0] == "\n\n".join([p1, p2])
    assert chunks[1] == p3
    assert all(len(c) <= 4096 for c in chunks)

def test_oversized_paragraph_split_at_sentence_boundaries():
    sentences = [f"Sentence number {i} ends here." for i in range(200)]
    para = " ".join(sentences)  # > 4096 chars
    assert len(para) > 4096
    chunks = chunk_script(para, 4096)
    assert len(chunks) >= 2
    for c in chunks:
        assert len(c) <= 4096
    assert " ".join(chunks) == para  # verbatim, nothing lost
    for s in sentences:
        assert any(s in c for c in chunks)  # sentences intact

def test_single_sentence_hard_split_word_boundary():
    sentence = _para(6000, 2)  # one "sentence", no terminal punctuation
    chunks = chunk_script(sentence, 4096)
    assert len(chunks) == 2
    assert all(len(c) <= 4096 for c in chunks)
    assert " ".join(chunks) == sentence  # word-boundary split, verbatim

def test_chunk_cap_exceeded_raises():
    script = "\n\n".join(_para(4500, i) for i in range(MAX_CHUNKS + 5))
    with pytest.raises(ValueError) as exc:
        chunk_script(script, 4096)
    assert "64" in str(exc.value)

def test_empty_script_raises():
    with pytest.raises(ValueError):
        chunk_script("", 4096)
    with pytest.raises(ValueError):
        chunk_script("   \n\n  ", 4096)

def test_custom_max_chunk_chars_respected():
    script = "\n\n".join(_para(400, i) for i in range(10))
    chunks = chunk_script(script, 500)
    assert len(chunks) >= 5
    assert all(len(c) <= 500 for c in chunks)
    assert "\n\n".join(chunks) == script

# --- Task 2: run engine -------------------------------------------------

TENANT = "t-longform"

class FailOnceProvider(MockTTSProvider):
    """Mock that fails the Nth generate call once (default: the 2nd)."""

    id = "mock"

    def __init__(self, fail_on_call: int = 2) -> None:
        self.calls: list[str] = []
        self._fail_on_call = fail_on_call
        self._armed = True

    def generate(self, request):  # type: ignore[override]
        self.calls.append(request.script)
        if self._armed and len(self.calls) == self._fail_on_call:
            self._armed = False
            raise TTSProviderError(502, "upstream provider unavailable")
        return super().generate(request)

class RawPcmProvider(MockTTSProvider):
    id = "mock"

    def generate(self, request):  # type: ignore[override]
        result = super().generate(request)
        return type(result)(
            audio=result.audio[44:],  # strip the canonical 44-byte WAV header
            content_type="audio/pcm",
            model=result.model,
            voice=result.voice,
            format="pcm",
        )

def _settings(tmp_path) -> Settings:
    return Settings(env="test", audio_storage_path=str(tmp_path / "store"))

def _payload(**overrides):
    payload = {
        "script": "\n\n".join(_para(1200, i) for i in range(4)),
        "voice": "alloy",
        "model": "tts-1",
        "speed": 1.0,
        "response_format": "wav",
        "title": "Long story",
        "max_chunk_chars": 4096,
    }
    payload.update(overrides)
    return payload

def _chunk_entries(asset):
    return [e for e in asset["processing_history"] if e.get("kind") == "chunk"]

def _mock_frames(text: str, voice: str = "alloy") -> int:
    blob = MockTTSProvider().generate(TTSRequest(script=text, voice=voice)).audio
    with wave_mod.open(io.BytesIO(blob)) as reader:
        return reader.getnframes()

@pytest.fixture
def actor_db(database):
    database.ensure_tenant(TENANT, "Tenant")
    return database

def test_happy_path_assembles_one_ready_asset(actor_db, tmp_path):
    db = actor_db
    script = _payload()["script"]
    asset = generate_long_form(db, _settings(tmp_path), TENANT, "admin@a.b", _payload(), provider=MockTTSProvider())
    assert asset["status"] == "ready"
    with wave_mod.open(str(tmp_path / "store" / TENANT / f"{asset['id']}.wav")) as reader:
        assembled_frames = reader.getnframes()
    expected = sum(_mock_frames(text) for text in chunk_script(script, 4096))
    assert assembled_frames == expected
    history = asset["processing_history"]
    header = history[0]
    assert header["kind"] == "longform"
    assert header["settings"]["voice"] == "alloy"
    assert header["settings"]["instructions"] is None
    assert header["max_chunk_chars"] == 4096
    entries = _chunk_entries(asset)
    assert all(e["state"] == "ready" for e in entries)
    assert any(e.get("kind") == "assembled" for e in history)
    leftovers = list((tmp_path / "store" / TENANT).glob(f"{asset['id']}_chunk_*"))
    assert leftovers == []

def test_partial_failure_keeps_processing_and_failed_chunk(actor_db, tmp_path):
    db = actor_db
    asset = generate_long_form(db, _settings(tmp_path), TENANT, "admin@a.b", _payload(), provider=FailOnceProvider())
    assert asset["status"] == "processing"
    entries = _chunk_entries(asset)
    failed = [e for e in entries if e["state"] == "failed"]
    assert len(failed) == 1
    assert "upstream provider unavailable" in failed[0]["error"]
    assert len([e for e in entries if e["state"] == "ready"]) == len(entries) - 1

def test_retry_regenerates_only_failed_chunks(actor_db, tmp_path):
    db = actor_db
    settings = _settings(tmp_path)
    provider = FailOnceProvider()
    asset = generate_long_form(db, settings, TENANT, "admin@a.b", _payload(), provider=provider)
    assert asset["status"] == "processing"
    manifest_texts = [e["text"] for e in _chunk_entries(asset)]
    calls_after_failure = len(provider.calls)
    retried = retry_long_form(db, settings, TENANT, "admin@a.b", asset, provider=provider)
    assert retried["status"] == "ready"
    assert len(provider.calls) == calls_after_failure + 1  # only the failed chunk
    assert provider.calls[-1] in manifest_texts  # STORED text, verbatim
    assert [e["text"] for e in _chunk_entries(retried)] == manifest_texts  # boundaries unchanged

def test_retry_rejects_non_longform_asset(actor_db, tmp_path):
    db = actor_db
    settings = _settings(tmp_path)
    from voxora.audio_assets import generate_asset

    asset = generate_asset(db, settings, TENANT, "admin@a.b", {"script": "short", "voice": "alloy"})
    with pytest.raises(ValueError):
        retry_long_form(db, settings, TENANT, "admin@a.b", asset, provider=MockTTSProvider())

def test_retry_rejected_while_run_in_flight(actor_db, tmp_path):
    db = actor_db
    settings = _settings(tmp_path)
    asset = generate_long_form(db, settings, TENANT, "admin@a.b", _payload(), provider=MockTTSProvider())
    run_locks.add(asset["id"])
    try:
        with pytest.raises(ValueError) as exc:
            retry_long_form(db, settings, TENANT, "admin@a.b", asset, provider=MockTTSProvider())
        assert "in flight" in str(exc.value)
    finally:
        run_locks.discard(asset["id"])

def test_assembly_over_size_cap_is_terminal_failed(actor_db, tmp_path, monkeypatch):
    import voxora.longform as longform_mod

    db = actor_db
    monkeypatch.setattr(longform_mod, "MAX_ASSET_BYTES", 4096)
    payload = _payload(script="\n\n".join(_para(1200, i) for i in range(2)))
    asset = generate_long_form(db, _settings(tmp_path), TENANT, "admin@a.b", payload, provider=MockTTSProvider())
    assert asset["status"] == "failed"
    failed_entries = [e for e in asset["processing_history"] if e.get("kind") == "assembly_failed"]
    assert failed_entries and "shorter" in failed_entries[-1]["error"]

def test_compressed_format_refused(actor_db, tmp_path):
    db = actor_db
    with pytest.raises(ValueError) as exc:
        generate_long_form(db, _settings(tmp_path), TENANT, "admin@a.b", _payload(response_format="mp3"), provider=MockTTSProvider())
    assert "decode" in str(exc.value)

def test_pcm_longform_raw_concat_honest_duration(actor_db, tmp_path):
    db = actor_db
    asset = generate_long_form(db, _settings(tmp_path), TENANT, "admin@a.b", _payload(response_format="pcm"), provider=RawPcmProvider())
    assert asset["status"] == "ready"
    assert asset["duration_seconds"] is None
    assert (tmp_path / "store" / TENANT / f"{asset['id']}.pcm").exists()

def test_no_row_left_when_validation_fails_before_creation(actor_db, tmp_path):
    db = actor_db
    with pytest.raises(ValueError):
        generate_long_form(db, _settings(tmp_path), TENANT, "admin@a.b", _payload(voice=""), provider=MockTTSProvider())
    items, _ = db.list_audio_assets(TENANT, limit=10, offset=0)
    assert items == []
