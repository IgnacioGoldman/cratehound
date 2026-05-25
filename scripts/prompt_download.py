"""
Prompt-to-download workflow.
"""

from __future__ import annotations

from pathlib import Path

from .song_prompt import parse_prompt_spec
from .sources.muzpa_scraper import MuzpaDownloadResult, download_muzpa_tracks


async def download_from_prompt(
    prompt: str,
    *,
    release_date: str | None = None,
    downloads_dir: Path = Path("downloads"),
    headless: bool = True,
    timeout_ms: int = 25_000,
    style: str | None = None,
) -> list[MuzpaDownloadResult]:
    spec = parse_prompt_spec(prompt)
    return await download_muzpa_tracks(
        [track.to_muzpa_query() for track in spec.tracks],
        release_date=release_date,
        downloads_dir=downloads_dir,
        headless=headless,
        timeout_ms=timeout_ms,
        style=style or spec.style,
    )
