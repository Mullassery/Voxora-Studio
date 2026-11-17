"""Long-form (Phase D) generation: chunking, run engine, retry.

Design: docs/superpowers/specs/2026-11-13-longform-generation-design.md
"""
import re
from typing import List

MAX_CHUNKS = 64
DEFAULT_MAX_CHUNK_CHARS = 4096

_PARA_SPLIT = re.compile(r"\n\s*\n")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")


def _split_long_text(text: str, limit: int) -> List[str]:
    if len(text) <= limit:
        return [text]
    sentences = _SENTENCE_SPLIT.split(text)
    if len(sentences) > 1:
        parts: List[str] = []
        cur = ""
        for s in sentences:
            if len(s) > limit:
                if cur:
                    parts.append(cur)
                    cur = ""
                parts.extend(_hard_split(s, limit))
            elif not cur:
                cur = s
            elif len(cur) + 1 + len(s) <= limit:
                cur = f"{cur} {s}"
            else:
                parts.append(cur)
                cur = s
        if cur:
            parts.append(cur)
        return parts
    return _hard_split(text, limit)


def _hard_split(text: str, limit: int) -> List[str]:
    parts: List[str] = []
    cur = text
    while len(cur) > limit:
        cut = cur.rfind(" ", 0, limit + 1)
        if cut <= 0:
            cut = limit
        parts.append(cur[:cut])
        cur = cur[cut:].lstrip(" ")
    if cur:
        parts.append(cur)
    return parts


def chunk_script(script: str, max_chunk_chars: int) -> List[str]:
    """Split a script into provider-sized chunks.

    Paragraph boundaries first (blank lines); a paragraph longer than the
    limit is split at sentence boundaries; a single sentence longer than
    the limit is hard-split at a word boundary. Chunk texts concatenate
    back to the original script verbatim (whitespace between pieces is
    preserved at merge boundaries).
    """
    if not script.strip():
        raise ValueError("script is empty")
    paragraphs = [p for p in _PARA_SPLIT.split(script) if p.strip()]
    chunks: List[str] = []
    cur = ""
    for p in paragraphs:
        if len(p) > max_chunk_chars:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.extend(_split_long_text(p, max_chunk_chars))
        elif not cur:
            cur = p
        elif len(cur) + 2 + len(p) <= max_chunk_chars:
            cur = f"{cur}\n\n{p}"
        else:
            chunks.append(cur)
            cur = p
    if cur:
        chunks.append(cur)
    if len(chunks) > MAX_CHUNKS:
        raise ValueError(
            f"script requires {len(chunks)} chunks, above the {MAX_CHUNKS}-chunk cap; shorten the script"
        )
    return chunks
