"""
Tracklist parser – extracts timestamp, title, artist, and original line
from DJ tracklist text (YouTube / SoundCloud style).

Supported formats
-----------------
  00:00 The Great Escape - Volen Sentir
  1:05:17 - Icicle - Tim Green, Izhevski
  58:38 Sébastien Léger - Feel
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# ── regex ────────────────────────────────────────────────────────────────
# Matches H:MM:SS or MM:SS or M:SS at the start of a line
_TIMESTAMP_RE = re.compile(
    r"^(?P<ts>(?:\d{1,2}:)?\d{1,2}:\d{2})"  # timestamp
    r"\s*[-–—]?\s*"                            # optional separator
)
_LEADING_TIMESTAMP_RE = re.compile(
    r"^(?:\d{1,2}:)?\d{1,2}:\d{2}"  # timestamp
    r"\s*[-–—]?\s*"                 # optional separator
)


@dataclass
class ParsedTrack:
    timestamp: str
    title: str
    artist: str
    original_line: str
    seconds: int = field(default=0, repr=False)


def _timestamp_to_seconds(ts: str) -> int:
    """Convert a timestamp string like '1:05:17' or '02:00' to total seconds."""
    parts = ts.split(":")
    parts = [int(p) for p in parts]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return parts[0] * 60 + parts[1]


def _normalise_whitespace(text: str) -> str:
    """Collapse multiple spaces / tabs into a single space, strip edges."""
    return re.sub(r"\s+", " ", text).strip()


def _split_title_artist(body: str) -> tuple[str, str]:
    """
    Split body text into (title, artist).

    Default assumption: ``Title - Artist``.

    Splitting strategy:
      • uses the *first* `` - `` (space-hyphen-space) as separator.
      • en-dash (–) and em-dash (—) also accepted.
    """
    separators = [" - ", " – ", " — "]
    for sep in separators:
        idx = body.find(sep)
        if idx != -1:
            title = body[:idx].strip()
            artist = body[idx + len(sep):].strip()
            return title, artist
    # No separator found → whole body is the title, artist unknown
    return body.strip(), ""


def parse_tracklist(text: str) -> list[ParsedTrack]:
    """
    Parse a multi-line tracklist string into a list of ``ParsedTrack`` objects.

    Blank lines are silently skipped.
    """
    tracks: list[ParsedTrack] = []

    for raw_line in text.splitlines():
        line = _normalise_whitespace(raw_line)
        if not line:
            continue

        m = _TIMESTAMP_RE.match(line)
        if not m:
            continue  # skip lines without a leading timestamp

        ts = m.group("ts")
        body = line[m.end():]          # everything after the timestamp + separator
        body = _LEADING_TIMESTAMP_RE.sub("", body, count=1)
        body = _normalise_whitespace(body)

        title, artist = _split_title_artist(body)

        tracks.append(
            ParsedTrack(
                timestamp=ts,
                title=title,
                artist=artist,
                original_line=raw_line.strip(),
                seconds=_timestamp_to_seconds(ts),
            )
        )

    return tracks
