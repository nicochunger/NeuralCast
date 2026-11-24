"""Read-only parser for station playlist_descriptions.md files."""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass

_HEADING_PATTERN = re.compile(r"^(#{2,4})\s+(.+?)\s*$")
_EXPLICIT_PLACEMENT_PATTERN = re.compile(
    r"^(?:placement(?:\s+notes)?|overlap(?:\s+notes)?)\s*:\s*(.+)$",
    re.IGNORECASE,
)
_SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÑ\"'(])")
_PLACEMENT_CUE_PATTERN = re.compile(
    r"\b("
    r"overlap|overlaps|prefer\b|use\s+[A-Z]|keep\s+|avoid\s+|retain\s+|"
    r"distinguish|choose\s+|preserve\s+|select\s+|belong(?:s)?\s+(?:more\s+naturally\s+)?in|"
    r"stronger\s+home|placement|boundary|boundaries|not\s+a\s+catch-all|"
    r"repeat(?:ing)?\s+|rather\s+than|distinct\s+from|neighboring|"
    r"exception|dedicated\s+homes?|its\s+own\s+home|their\s+own\s+homes?|"
    r"judge\s+individual|do\s+not\s+(?:remove|repeat|expand|broaden|treat)|"
    r"is\s+the\s+stronger\s+home|is\s+intentional|alone\s+do\s+not\s+make|"
    r"more\s+strongly|also\s+fit\s+[A-Z]"
    r")",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PlaylistEditorialCopy:
    """Editorial description and placement guidance for one playlist."""

    name: str
    description: str
    placement_notes: str


def normalize_heading_key(name: str) -> str:
    """Normalize a playlist name for heading-to-CSV lookup."""
    return " ".join(str(name or "").strip().casefold().split())


def parse_playlist_descriptions_text(markdown: str) -> dict[str, PlaylistEditorialCopy]:
    """Parse playlist descriptions keyed by normalized playlist name."""
    entries: dict[str, PlaylistEditorialCopy] = {}
    current_h2 = ""
    current_playlist: str | None = None
    current_sub_heading: str | None = None
    body_lines: list[str] = []
    explicit_placement_lines: list[str] = []

    def flush() -> None:
        nonlocal current_playlist, current_sub_heading, body_lines, explicit_placement_lines
        if current_playlist is None:
            return
        description, placement_notes = _split_editorial_sections(
            body_lines, explicit_placement_lines
        )
        key = normalize_heading_key(current_playlist)
        if key:
            entries[key] = PlaylistEditorialCopy(
                name=current_playlist.strip(),
                description=description,
                placement_notes=placement_notes,
            )
        current_playlist = None
        current_sub_heading = None
        body_lines = []
        explicit_placement_lines = []

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        match = _HEADING_PATTERN.match(line.strip())
        if match:
            level = len(match.group(1))
            title = match.group(2).strip()
            if level == 2:
                flush()
                current_h2 = title.casefold()
                continue
            if level == 3:
                flush()
                # Skip non-catalog subsections if they ever use ### under placement notes.
                if current_h2 and "placement" in current_h2 and "catalog" not in current_h2:
                    continue
                current_playlist = title
                current_sub_heading = None
                continue
            if level == 4 and current_playlist is not None:
                current_sub_heading = title.casefold()
                continue

        if current_playlist is None:
            continue

        if current_sub_heading and any(
            word in current_sub_heading for word in ("placement", "overlap", "notes")
        ):
            explicit_placement_lines.append(line)
        else:
            body_lines.append(line)

    flush()
    return entries


def load_playlist_descriptions(path: pathlib.Path) -> dict[str, PlaylistEditorialCopy]:
    """Read and parse a station's playlist_descriptions.md file safely."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    return parse_playlist_descriptions_text(text)


def _paragraphs_from_lines(lines: list[str]) -> list[str]:
    paragraphs: list[str] = []
    buffer: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            if buffer:
                paragraphs.append(" ".join(buffer))
                buffer = []
            continue
        cleaned = re.sub(r"^[-*]\s+", "", stripped)
        buffer.append(cleaned)
    if buffer:
        paragraphs.append(" ".join(buffer))
    return paragraphs


def _split_editorial_sections(
    body_lines: list[str],
    explicit_placement_lines: list[str],
) -> tuple[str, str]:
    paragraphs = _paragraphs_from_lines(body_lines)
    explicit_paragraphs = _paragraphs_from_lines(explicit_placement_lines)

    description_parts: list[str] = []
    placement_parts: list[str] = list(explicit_paragraphs)

    for paragraph in paragraphs:
        explicit_match = _EXPLICIT_PLACEMENT_PATTERN.match(paragraph)
        if explicit_match:
            placement_parts.append(explicit_match.group(1).strip())
        else:
            description_parts.append(paragraph)

    if placement_parts:
        return "\n\n".join(description_parts).strip(), "\n\n".join(placement_parts).strip()

    if len(description_parts) > 1:
        return description_parts[0].strip(), "\n\n".join(description_parts[1:]).strip()

    if not description_parts:
        return "", ""

    single = description_parts[0].strip()
    sentences = _split_sentences(single)
    if len(sentences) <= 1:
        return single, ""

    split_index: int | None = None
    for idx in range(1, len(sentences)):
        if _PLACEMENT_CUE_PATTERN.search(sentences[idx]):
            split_index = idx
            break

    if split_index is None:
        return single, ""

    description = " ".join(sentences[:split_index]).strip()
    placement_notes = " ".join(sentences[split_index:]).strip()
    return description, placement_notes


def _split_sentences(text: str) -> list[str]:
    # Protect common abbreviations like "Pt. 2" or ".Paak" from false sentence breaks.
    protected = re.sub(r"\b(Pt|Vol|No|St|Dr|Mr|Ms|Jr|Sr)\.\s+", r"\1<DOT> ", text)
    parts = _SENTENCE_SPLIT_PATTERN.split(protected)
    return [part.replace("<DOT>", ".").strip() for part in parts if part.strip()]
