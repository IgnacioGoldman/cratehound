"""
Organize downloaded tracks into a crate folder and write a human report.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from .song_prompt import PromptTrack
from .sources.muzpa_scraper import MuzpaDownloadResult

AUDIO_EXTENSIONS = {".aif", ".aiff", ".flac", ".mp3", ".wav", ".zip"}


def organize_set_results(
    results: list[MuzpaDownloadResult],
    tracks: list[PromptTrack],
    downloads_dir: Path,
) -> list[MuzpaDownloadResult]:
    """Rename found downloads to tracklist order inside downloads_dir."""
    downloads_dir.mkdir(parents=True, exist_ok=True)
    organized: list[MuzpaDownloadResult] = []

    for index, result in enumerate(results):
        if result.path is None:
            organized.append(result)
            continue

        source = result.path
        if not source.exists():
            candidate = _find_by_filename(downloads_dir, result.filename)
            if candidate is None:
                organized.append(result)
                continue
            source = candidate

        track = tracks[index] if index < len(tracks) else None
        target = _ordered_path(downloads_dir, index, track, source)
        if source.resolve() != target.resolve():
            if target.exists():
                duplicate_dir = downloads_dir / "results" / "duplicates"
                duplicate_dir.mkdir(parents=True, exist_ok=True)
                source.replace(_unique_path(duplicate_dir / source.name))
            else:
                source.replace(target)

        organized.append(
            replace(
                result,
                path=target,
                filename=target.name,
            )
        )

    return organized


def write_markdown_report(
    results: list[MuzpaDownloadResult],
    tracks: list[PromptTrack],
    *,
    style: str | None,
    report_path: Path,
) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Download Results",
        "",
        f"Style: {style or 'not specified'}",
        "",
    ]

    for index, result in enumerate(results):
        track = tracks[index] if index < len(tracks) else None
        name = _track_display_name(track, result.query)
        if result.status in {"downloaded", "already_downloaded"}:
            relevance = _style_relevance(style, f"{name} {result.matched_text or result.filename or ''}")
            lines.append(f"- {index:03d} {name}, found, {relevance}")
        else:
            lines.append(f"- {index:03d} {name}, not found")

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _ordered_path(downloads_dir: Path, index: int, track: PromptTrack | None, source: Path) -> Path:
    stem = _track_display_name(track, source.stem)
    filename = f"{index:03d} {_safe_filename(stem)}{source.suffix.lower()}"
    return downloads_dir / filename


def _track_display_name(track: PromptTrack | None, fallback: str) -> str:
    if track is None:
        return fallback
    if track.artist:
        return f"{track.title} - {track.artist}"
    return track.title or fallback


def _style_relevance(style: str | None, text: str) -> str:
    lowered = text.lower()
    text_tokens = set(_tokens(lowered))
    desired = set(_tokens(style or ""))

    if "organic" in text_tokens:
        return "100% organic"
    if "innerbloom" in text_tokens and "organic" in desired:
        return "20% organic, 80% deep"
    if "deep" in text_tokens:
        return "20% organic, 80% deep"
    if "techno" in text_tokens:
        return "20% organic, 80% techno"
    if "progressive" in text_tokens:
        return "50% organic, 50% progressive"
    if "house" in text_tokens and "organic" in desired:
        return "70% organic, 30% house"
    if any(label in lowered for label in ("all day i dream", "anjunadeep", "amulanga")):
        return "80% organic"
    if "organic" in desired:
        return "60% organic"
    return "style relevance unknown"


def _find_by_filename(downloads_dir: Path, filename: str | None) -> Path | None:
    if not filename:
        return None
    for path in downloads_dir.rglob(filename):
        if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS:
            return path
    return None


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for duplicate in range(1, 1000):
        candidate = path.with_name(f"{path.stem} ({duplicate}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not choose a unique filename for {path}")


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[\\\\/:*?\"<>|]+", "_", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or "track"


def _tokens(value: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) > 1]
