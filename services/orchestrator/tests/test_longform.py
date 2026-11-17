"""Tests for the long-form chunker (Phase D, Task 1)."""
import pytest

from voxora.longform import MAX_CHUNKS, chunk_script


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
