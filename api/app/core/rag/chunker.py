"""Section-aware parent/child chunking with a hard token limit."""

import re
from dataclasses import dataclass, field

import tiktoken

_encoder = tiktoken.get_encoding("cl100k_base")
_SENTENCE_END = re.compile(r"(?<=[。！？.!?；;])\s*|\n+")
_HEADING = re.compile(r"^#{1,6}\s+|^第\s*\d+\s*页$")


@dataclass(frozen=True)
class ChunkConfig:
    parent_tokens: int = 1024
    child_tokens: int = 256
    overlap_tokens: int = 24

    def __post_init__(self) -> None:
        if self.parent_tokens < 64 or self.child_tokens < 32:
            raise ValueError("chunk token limits are too small")
        if self.child_tokens >= self.parent_tokens:
            raise ValueError("child_tokens must be smaller than parent_tokens")
        if not 0 <= self.overlap_tokens < self.child_tokens // 2:
            raise ValueError("overlap_tokens must be less than half a child chunk")


@dataclass
class ParentChunk:
    content: str
    children: list[str] = field(default_factory=list)


def count_tokens(text: str) -> int:
    return len(_encoder.encode(text))


def _hard_split(text: str, limit: int) -> list[str]:
    """Split long sentences without producing a chunk above the token budget."""
    out = []
    remaining = text
    while remaining:
        if count_tokens(remaining) <= limit:
            out.append(remaining)
            break
        low, high = 1, len(remaining)
        while low < high:
            middle = (low + high + 1) // 2
            if count_tokens(remaining[:middle]) <= limit:
                low = middle
            else:
                high = middle - 1
        cut = max(1, low)
        out.append(remaining[:cut])
        remaining = remaining[cut:]
    return out


def _sentences(text: str, limit: int) -> list[str]:
    pieces = [piece.strip() for piece in _SENTENCE_END.split(text) if piece.strip()]
    return [part for piece in pieces for part in _hard_split(piece, limit)]


def _sections(text: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    heading = ""
    lines: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if "\f" in line or _HEADING.match(stripped):
            if lines:
                sections.append((heading, "\n".join(lines).strip()))
                lines = []
            heading = stripped.replace("\f", "").strip()
        elif stripped:
            lines.append(stripped)
    if lines:
        sections.append((heading, "\n".join(lines).strip()))
    return sections or [("", text.strip())]


def _pack(parts: list[str], limit: int, overlap: int = 0) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    for part in parts:
        if current and count_tokens("\n".join([*current, part])) > limit:
            chunks.append("\n".join(current))
            tail = current[-1] if overlap else ""
            if tail and count_tokens(tail) <= overlap:
                current = [tail]
            elif overlap and tail:
                # A token slice may start halfway through a UTF-8 character.
                # Choose a Unicode-safe suffix, then verify its token budget.
                low, high = 0, len(tail)
                while low < high:
                    middle = (low + high + 1) // 2
                    if count_tokens(tail[-middle:]) <= overlap:
                        low = middle
                    else:
                        high = middle - 1
                suffix = tail[-low:].strip() if low else ""
                current = [suffix] if suffix else []
            else:
                current = []
        if current and count_tokens("\n".join([*current, part])) > limit:
            current = []
        current.append(part)
    if current:
        chunks.append("\n".join(current))
    return chunks


def chunk_parent_child(text: str, config: ChunkConfig | None = None) -> list[ParentChunk]:
    config = config or ChunkConfig()
    if not text.strip():
        return []
    result: list[ParentChunk] = []
    for heading, body in _sections(text):
        prefix = heading + "\n" if heading else ""
        budget = config.parent_tokens - count_tokens(prefix)
        if budget < config.child_tokens:
            prefix = ""
            budget = config.parent_tokens
        for parent_body in _pack(_sentences(body, budget), budget):
            content = prefix + parent_body
            children = _pack(_sentences(content, config.child_tokens),
                             config.child_tokens, config.overlap_tokens)
            result.append(ParentChunk(content=content, children=children))
    return result
