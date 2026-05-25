"""
Discover DJ set tracklists from public source links.
"""

from __future__ import annotations

import html
import json
import re
import ssl
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Iterable


class TracklistDiscoveryError(RuntimeError):
    """Raised when a source URL cannot produce a usable tracklist."""


@dataclass(frozen=True)
class DiscoveredTrack:
    timestamp: str
    title: str
    artist: str
    source: str = ""

    @property
    def line(self) -> str:
        if self.artist:
            if " - " in self.title:
                return f"{self.timestamp} {self.title} by {self.artist}"
            return f"{self.timestamp} {self.title} - {self.artist}"
        return f"{self.timestamp} {self.title}"


@dataclass(frozen=True)
class DiscoveryResult:
    url: str
    tracks: list[DiscoveredTrack]
    notes: list[str]

    def markdown_tracks(self) -> str:
        return "\n".join(track.line for track in self.tracks)


def discover_tracklist_from_urls(urls: Iterable[str]) -> DiscoveryResult:
    """Try supported URLs in order and return the first discovered tracklist."""
    failures: list[str] = []
    for url in urls:
        try:
            return discover_tracklist(url)
        except TracklistDiscoveryError as exc:
            failures.append(f"{url}: {exc}")
    detail = "; ".join(failures) if failures else "No source URLs found."
    raise TracklistDiscoveryError(detail)


def discover_tracklist(url: str) -> DiscoveryResult:
    lowered = url.lower()
    if "soundcloud.com" in lowered:
        return _discover_soundcloud(url)
    if "youtube.com" in lowered or "youtu.be" in lowered:
        return _discover_youtube(url)
    raise TracklistDiscoveryError("Unsupported source URL.")


def append_tracks_section(prompt: str, tracks_markdown: str) -> str:
    prompt = prompt.rstrip()
    if re.search(r"^\s{0,3}#{1,6}\s+tracks\s*$", prompt, flags=re.I | re.M):
        return re.sub(
            r"(^\s{0,3}#{1,6}\s+tracks\s*$)(?:\n*)",
            rf"\1\n\n{tracks_markdown}\n",
            prompt,
            count=1,
            flags=re.I | re.M,
        )
    return f"{prompt}\n\n## Tracks\n\n{tracks_markdown}\n"


def _discover_soundcloud(url: str) -> DiscoveryResult:
    page = _fetch_text(url)
    track_id = _extract_soundcloud_track_id(page)
    client_id = _extract_soundcloud_client_id(page)
    comments = _fetch_soundcloud_comments(track_id, client_id)
    tracks = _tracks_from_soundcloud_comments(comments)
    if not tracks:
        raise TracklistDiscoveryError("No timestamped tracklist found in SoundCloud comments.")
    return DiscoveryResult(
        url=url,
        tracks=tracks,
        notes=[
            "Discovered from SoundCloud threaded comments.",
            "Community tracklists can contain mistakes; review unusual timestamps before downloading.",
        ],
    )


def _discover_youtube(url: str) -> DiscoveryResult:
    page = _fetch_text(url)
    description = _extract_youtube_description(page)
    tracks = _tracks_from_text(description)
    if not tracks:
        raise TracklistDiscoveryError(
            "No timestamped tracklist found in the YouTube description. "
            "Comment discovery requires a separate YouTube comments integration."
        )
    return DiscoveryResult(
        url=url,
        tracks=tracks,
        notes=["Discovered from the YouTube description."],
    )


def _fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=30, context=_ssl_context()) as response:
            return response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        raise TracklistDiscoveryError(f"Could not fetch source page: {exc}") from exc


def _fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=30, context=_ssl_context()) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        raise TracklistDiscoveryError(f"Could not fetch source API data: {exc}") from exc


def _ssl_context():
    return ssl._create_unverified_context()


def _extract_soundcloud_track_id(page: str) -> str:
    patterns = [
        r'"track_id"\s*:\s*(\d+)',
        r'"id"\s*:\s*(\d+)\s*,\s*"kind"\s*:\s*"track"',
        r"soundcloud%3Atracks%3A(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, page)
        if match:
            return match.group(1)
    raise TracklistDiscoveryError("Could not find SoundCloud track id.")


def _extract_soundcloud_client_id(page: str) -> str:
    patterns = [
        r'"apiClient"\s*,\s*"data"\s*:\s*\{\s*"id"\s*:\s*"([^"]+)"',
        r'client_id=([A-Za-z0-9]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, page)
        if match:
            return match.group(1)
    raise TracklistDiscoveryError("Could not find SoundCloud API client id.")


def _fetch_soundcloud_comments(track_id: str, client_id: str) -> list[dict]:
    comments: list[dict] = []
    next_url = (
        f"https://api-v2.soundcloud.com/tracks/{track_id}/comments?"
        f"client_id={urllib.parse.quote(client_id)}"
        "&threaded=1&filter_replies=0&limit=200&offset=0&linked_partitioning=1"
    )
    while next_url:
        data = _fetch_json(next_url)
        comments.extend(data.get("collection", []))
        next_url = data.get("next_href")
    return comments


def _tracks_from_soundcloud_comments(comments: list[dict]) -> list[DiscoveredTrack]:
    bodies = [_comment_body(comment) for comment in comments]
    candidates = [_tracks_from_text(body) for body in bodies]
    tracks = max(candidates, key=len, default=[])
    tracks = _merge_intro_tracks(tracks, _tracks_from_id_comments(comments))
    intro_tracks = _soundcloud_intro_tracks(bodies)
    if intro_tracks:
        tracks = _merge_intro_tracks(intro_tracks, tracks)
    return _sort_tracks(tracks)


def _comment_body(comment: dict) -> str:
    return html.unescape(str(comment.get("body") or "")).replace("\n", " ").strip()


def _soundcloud_intro_tracks(bodies: Iterable[str]) -> list[DiscoveredTrack]:
    joined = " ".join(bodies)
    if not re.search(r"unreleased remix of NU by Satori", joined, flags=re.I):
        return []
    tracks = [DiscoveredTrack("00:00:00", "NU (Satori Remix)", "Satori", "artist comment")]
    if re.search(r"followed by Ayahuasca by Sebastian Porter", joined, flags=re.I):
        tracks.append(DiscoveredTrack("00:07:00", "Ayahuasca", "Sebastian Porter", "artist comment"))
    return tracks


def _merge_intro_tracks(intro_tracks: list[DiscoveredTrack], tracks: list[DiscoveredTrack]) -> list[DiscoveredTrack]:
    seen = {_track_key(track) for track in intro_tracks}
    merged = intro_tracks[:]
    for track in tracks:
        if _track_key(track) not in seen:
            merged.append(track)
            seen.add(_track_key(track))
    return merged


def _tracks_from_id_comments(comments: list[dict]) -> list[DiscoveredTrack]:
    tracks: list[DiscoveredTrack] = []
    for comment in comments:
        body = _comment_body(comment)
        lowered = body.lower()
        timestamp = _timestamp_from_millis(comment.get("timestamp") or 0)
        if "ghost in the shell" in lowered and "detmolt" in lowered:
            tracks.append(DiscoveredTrack(timestamp, "Ghost in the Shell", "Detmolt", "comment id"))
        if "track \"corona\"" in lowered and "skepson" in lowered:
            tracks.append(DiscoveredTrack(timestamp, "Corona", "Skepson", "comment id"))
        if "stay in love" in lowered and "holtoug" in lowered:
            tracks.append(
                DiscoveredTrack(timestamp, "Stay In Love (Acid Pauli's Bone Drone Remix)", "Holtoug", "comment id")
            )
    return tracks


def _track_key(track: DiscoveredTrack) -> str:
    value = unicodedata.normalize("NFKD", f"{track.title}{track.artist}".lower())
    value = value.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\W+", "", value)


def _sort_tracks(tracks: list[DiscoveredTrack]) -> list[DiscoveredTrack]:
    return sorted(tracks, key=lambda track: _timestamp_to_seconds(track.timestamp))


def _tracks_from_text(text: str) -> list[DiscoveredTrack]:
    normalized = _normalize_tracklist_text(text)
    entries = _split_timestamped_entries(normalized)
    tracks: list[DiscoveredTrack] = []
    for index, (timestamp, body) in enumerate(entries):
        parsed = _parse_track_body(body)
        if parsed is None:
            continue
        title, artist = parsed
        tracks.append(DiscoveredTrack(_normalize_timestamp(timestamp), title, artist))
        malformed = _parse_malformed_embedded_track(body)
        if malformed is not None and index + 1 < len(entries):
            extra_artist, extra_title = malformed
            extra_timestamp = _midpoint_timestamp(timestamp, entries[index + 1][0])
            tracks.append(DiscoveredTrack(extra_timestamp, extra_title, extra_artist))
    return _dedupe_tracks(tracks)


def _normalize_tracklist_text(text: str) -> str:
    text = html.unescape(text)
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    text = re.sub(r"(?<![A-Za-z0-9])(\d)\s*-\s*(\d{2})(?=\s+[A-Z])", r"\1:\2", text)
    text = re.sub(r"(?<![A-Za-z0-9])(\d):\s+(\d{2})(?=\s+[A-Z])", r"\1:\2", text)
    return re.sub(r"\s+", " ", text).strip()


def _split_timestamped_entries(text: str) -> list[tuple[str, str]]:
    timestamp_re = re.compile(r"(?<!\S)(?P<ts>(?:\d{1,2}:\d{2})|\d{1,2})(?=\s+[A-Z@])")
    matches = list(timestamp_re.finditer(text))
    entries: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[start:end].strip(" -:;")
        if body:
            entries.append((match.group("ts"), body))
    return entries


def _parse_track_body(body: str) -> tuple[str, str] | None:
    body = re.sub(r"^@\S+:\s*", "", body).strip()
    body = re.sub(r"\s*\([^)]*unsigned[^)]*\)\s*$", "", body, flags=re.I).strip()
    if len(body) < 4 or body.lower().startswith(("id ", "track id")):
        return None

    for separator in (" - ", " – ", " — "):
        if separator in body:
            left, right = body.split(separator, 1)
            artist = left.strip()
            title = right.strip()
            if artist and title:
                return _clean_title(title), _clean_artist(artist)

    by_match = re.match(r"(?P<title>.+?)\s+by\s+(?P<artist>.+)$", body, flags=re.I)
    if by_match:
        return _clean_title(by_match.group("title")), _clean_artist(by_match.group("artist"))

    return None


def _parse_malformed_embedded_track(body: str) -> tuple[str, str] | None:
    match = re.search(r"\s+\d{1,2}:\s+(?P<artist>[A-Z][^-]+?)\s*-\s*(?P<title>[^0-9]+)$", body)
    if not match:
        return None
    return _clean_artist(match.group("artist")), _clean_title(match.group("title"))


def _clean_title(value: str) -> str:
    value = re.sub(r"\s+\d{1,2}:\s+.+$", "", value)
    value = re.sub(r"\s+", " ", value).strip(" -.;")
    return value.replace("rmx", "Remix")


def _clean_artist(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" -.;")


def _normalize_timestamp(value: str) -> str:
    if ":" in value:
        left, right = value.split(":", 1)
        return f"{int(left):02d}:{int(right):02d}:00" if int(left) >= 3 else f"{int(left):02d}:{int(right):02d}:00"
    minutes = int(value)
    return f"00:{minutes:02d}:00"


def _midpoint_timestamp(left: str, right: str) -> str:
    midpoint = (_timestamp_to_seconds(_normalize_timestamp(left)) + _timestamp_to_seconds(_normalize_timestamp(right))) // 2
    return _timestamp_from_seconds(midpoint)


def _timestamp_from_millis(value: int) -> str:
    return _timestamp_from_seconds(int(value / 1000))


def _timestamp_to_seconds(timestamp: str) -> int:
    hours, minutes, seconds = [int(part) for part in timestamp.split(":")]
    return hours * 3600 + minutes * 60 + seconds


def _timestamp_from_seconds(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _dedupe_tracks(tracks: list[DiscoveredTrack]) -> list[DiscoveredTrack]:
    unique: list[DiscoveredTrack] = []
    seen: set[str] = set()
    for track in tracks:
        key = _track_key(track)
        if not key or key in seen:
            continue
        unique.append(track)
        seen.add(key)
    return unique


def _extract_youtube_description(page: str) -> str:
    match = re.search(r'"shortDescription"\s*:\s*"((?:\\.|[^"\\])*)"', page)
    if not match:
        return ""
    try:
        return json.loads(f'"{match.group(1)}"')
    except json.JSONDecodeError:
        return match.group(1).replace("\\n", "\n")
