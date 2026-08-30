"""
Structure-aware chunking for the hotel knowledge base.

Two shapes of source text show up in practice and they want different
treatment:

  * Q&A / FAQ entries — short and self-contained. Splitting them mid-answer
    produces chunks that retrieve well but read as half an answer, so each
    question-and-answer pair is kept whole regardless of length.
  * Free prose — restaurant descriptions, nearby attractions, policy text.
    Split on paragraph boundaries, falling back to sentence boundaries when
    a single paragraph is too long, with a little overlap so a fact sitting
    on a boundary still appears intact in one chunk.

Sentence splitting recognises the Devanagari danda (U+0964) as well as
Latin terminators, so Nepali text does not collapse into one giant chunk
(NFR-6).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ~800 characters is roughly 200 tokens of English, which keeps chunks
# small enough to be specific but large enough to carry context.
MAX_CHARS = 800
OVERLAP_CHARS = 120
MIN_CHARS = 40

_QA_START = re.compile(r"^\s*(?:Q|Question|प्रश्न)\s*[:.)-]", re.IGNORECASE)
_A_START = re.compile(r"^\s*(?:A|Ans|Answer|उत्तर)\s*[:.)-]", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?।])\s+")


@dataclass(frozen=True)
class Chunk:
    text: str
    token_count: int


def estimate_tokens(text: str) -> int:
    """Rough token estimate.

    Deliberately provider-agnostic: the real tokenizer differs per embedding
    model, and this value is only used for cost/eval reporting, never for
    truncation. Roughly 4 characters per token for Latin script; Devanagari
    tends to run denser, so the character heuristic is floored by word count.
    """
    if not text.strip():
        return 0
    return max(len(text) // 4, len(text.split()))


def _looks_like_qa(text: str) -> bool:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return False
    hits = sum(1 for ln in lines if _QA_START.match(ln) or _A_START.match(ln))
    return hits >= 2


def _split_qa_pairs(text: str) -> list[str]:
    """Group lines into question/answer blocks, one block per chunk."""
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if _QA_START.match(line) and current:
            blocks.append(current)
            current = [line]
        elif line.strip() or current:
            current.append(line)
    if current:
        blocks.append(current)
    return [b for b in ("\n".join(x).strip() for x in blocks) if b]


def _split_long_paragraph(paragraph: str) -> list[str]:
    """Sentence-boundary split with overlap, for paragraphs over the limit."""
    sentences = [s.strip() for s in _SENTENCE_END.split(paragraph) if s.strip()]
    if not sentences:
        return []

    out: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip()
        if len(candidate) <= MAX_CHARS or not current:
            current = candidate
            continue
        out.append(current)
        # carry the tail of the previous chunk forward as overlap
        tail = current[-OVERLAP_CHARS:]
        pivot = tail.find(" ")
        current = f"{tail[pivot + 1:] if pivot != -1 else tail} {sentence}".strip()
    if current:
        out.append(current)

    # A single sentence longer than MAX_CHARS still has to be cut somewhere.
    final: list[str] = []
    for piece in out:
        while len(piece) > MAX_CHARS * 2:
            final.append(piece[:MAX_CHARS])
            piece = piece[MAX_CHARS - OVERLAP_CHARS :]
        final.append(piece)
    return final


def chunk_text(text: str) -> list[Chunk]:
    """Split raw source text into embeddable chunks."""
    text = (text or "").strip()
    if not text:
        return []

    if _looks_like_qa(text):
        pieces = _split_qa_pairs(text)
    else:
        # A paragraph is the natural semantic unit, so each one becomes its
        # own chunk. Packing paragraphs together up to a size limit would be
        # denser, but it buries distinct topics in one blob: a question about
        # paragliding should retrieve the paragliding paragraph, not a chunk
        # that also covers three unrelated attractions.
        pieces = []
        pending = ""
        for raw in re.split(r"\n\s*\n", text):
            paragraph = raw.strip()
            if not paragraph:
                continue
            if pending:
                paragraph = f"{pending}\n\n{paragraph}"
                pending = ""
            if len(paragraph) > MAX_CHARS:
                pieces.extend(_split_long_paragraph(paragraph))
            elif len(paragraph) < MIN_CHARS:
                # Too short to stand alone (a heading, a stray line);
                # carry it forward onto the next paragraph.
                pending = paragraph
            else:
                pieces.append(paragraph)
        if pending:
            if pieces:
                pieces[-1] = f"{pieces[-1]}\n\n{pending}"
            else:
                pieces.append(pending)

    # Drop fragments too small to carry meaning, unless that would leave
    # nothing at all (a legitimately tiny document should still be indexed).
    cleaned = [p.strip() for p in pieces if p.strip()]
    meaningful = [p for p in cleaned if len(p) >= MIN_CHARS]
    chosen = meaningful or cleaned
    return [Chunk(text=p, token_count=estimate_tokens(p)) for p in chosen]
