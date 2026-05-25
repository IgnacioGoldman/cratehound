"""
Parse loose song prompts into Muzpa search queries.

This is intentionally forgiving: the user can paste a timestamped tracklist,
a numbered list, bullets, or one plain query per line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .parser import parse_tracklist
from .sources.muzpa_scraper import MuzpaTrackQuery


@dataclass(frozen=True)
class PromptTrack:
    title: str
    artist: str = ""
    original_line: str = ""

    @property
    def query(self) -> str:
        return f"{self.title} {self.artist}".strip()

    def to_muzpa_query(self) -> MuzpaTrackQuery:
        return MuzpaTrackQuery(title=self.title, artist=self.artist)


@dataclass(frozen=True)
class PromptSpec:
    tracks: list[PromptTrack] = field(default_factory=list)
    style: str | None = None
    source_urls: list[str] = field(default_factory=list)


def parse_song_prompt(prompt: str) -> list[PromptTrack]:
    """Return one track query per useful line in a loose user prompt."""
    return parse_prompt_spec(prompt).tracks


def parse_prompt_spec(prompt: str) -> PromptSpec:
    """Parse style metadata and track queries from loose text or markdown."""
    style = _extract_markdown_section(prompt, "style")
    track_text = _extract_markdown_section(prompt, "tracks")
    source_urls = _extract_source_urls(prompt)
    if track_text is not None:
        tracks = _parse_track_lines(track_text)
    elif source_urls:
        tracks = []
    else:
        tracks = _parse_track_lines(prompt)
    return PromptSpec(tracks=tracks, style=style, source_urls=source_urls)


def _parse_track_lines(text: str) -> list[PromptTrack]:
    tracks: list[PromptTrack] = []

    for raw_line in text.splitlines():
        line = _clean_line(raw_line)
        if not line:
            continue

        parsed = parse_tracklist(line)
        if parsed:
            for track in parsed:
                tracks.append(
                    PromptTrack(title=track.title, artist=track.artist, original_line=raw_line.strip())
                )
            continue

        title, artist = _split_song_line(line)
        if title:
            tracks.append(PromptTrack(title=title, artist=artist, original_line=raw_line.strip()))

    return _dedupe_tracks(tracks)


def _extract_markdown_section(prompt: str, section_name: str) -> str | None:
    current: str | None = None
    lines: list[str] = []
    wanted = section_name.strip().lower()

    for raw_line in prompt.splitlines():
        heading = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", raw_line)
        if heading:
            current = heading.group(1).strip().lower()
            continue
        if current == wanted:
            lines.append(raw_line)

    text = "\n".join(lines).strip()
    return text or None


def _extract_source_urls(prompt: str) -> list[str]:
    urls: list[str] = []
    for match in re.finditer(r"https?://[^\s<>)]+", prompt):
        url = match.group(0).rstrip(".,;")
        lowered = url.lower()
        if any(host in lowered for host in ("soundcloud.com", "youtube.com", "youtu.be")):
            urls.append(url)
    return urls


def _clean_line(line: str) -> str:
    cleaned = line.strip()
    if cleaned.startswith("#"):
        return ""
    if re.match(r"https?://", cleaned, flags=re.I):
        return ""
    cleaned = re.sub(r"^```.*$", "", cleaned)
    cleaned = re.sub(r"^(?:[-*•]|\d+[.)])\s+", "", cleaned)
    cleaned = re.sub(r"^(?:song|track|download)\s*:\s*", "", cleaned, flags=re.I)
    if _looks_like_comment_metadata(cleaned):
        return ""
    return re.sub(r"\s+", " ", cleaned).strip(" \t'\"")


def _looks_like_comment_metadata(line: str) -> bool:
    normalized = line.strip().lower()
    if not normalized:
        return True
    if normalized.startswith("@"):
        return True
    metadata_prefixes = (
        "pinned by ",
        "fijado por ",
        "hace ",
        "edited",
        "editado",
    )
    return normalized.startswith(metadata_prefixes)


def _split_song_line(line: str) -> tuple[str, str]:
    by_match = re.match(r"(?P<title>.+?)\s+by\s+(?P<artist>.+)$", line, flags=re.I)
    if by_match:
        return by_match.group("title").strip(), by_match.group("artist").strip()

    for separator in (" - ", " – ", " — "):
        if separator in line:
            title, artist = line.split(separator, 1)
            return title.strip(), artist.strip()

    return line.strip(), ""


def _dedupe_tracks(tracks: list[PromptTrack]) -> list[PromptTrack]:
    seen: set[str] = set()
    unique: list[PromptTrack] = []

    for track in tracks:
        key = track.query.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(track)

    return unique
