"""
Prompt-first downloader.

Examples:
    python -m scripts.download_cli "Children Gorje Hewek"
    python -m scripts.download_cli --prompt-file input/organic-1.md
    printf "Children Gorje Hewek\\nZenna Siah" | python -m scripts.download_cli
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .organizer import organize_set_results, write_markdown_report
from .prompt_download import download_from_prompt
from .song_prompt import parse_prompt_spec
from .sources.muzpa_scraper import MuzpaScraperError
from .tracklist_discovery import (
    TracklistDiscoveryError,
    append_tracks_section,
    discover_tracklist_from_urls,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Download song prompts through your authenticated Muzpa account.")
    parser.add_argument("prompt", nargs="*", help="Song query text. For multiple songs, use quotes or stdin.")
    parser.add_argument("--prompt-file", type=Path, help="Read song prompt text from a file.")
    parser.add_argument("--style", help="Optional style hint, e.g. 'Organic | House'.")
    parser.add_argument("--release-date", help="Optional release date to pass to the releases view, YYYY-MM-DD.")
    parser.add_argument("--downloads-dir", type=Path, default=Path("downloads"), help="Directory for downloaded files.")
    parser.add_argument("--set-name", help="Folder name under downloads. Defaults to the prompt filename.")
    parser.add_argument("--timeout-ms", type=int, default=25_000, help="Playwright timeout in milliseconds.")
    parser.add_argument("--headed", action="store_true", help="Show the browser while downloading.")
    parser.add_argument(
        "--no-discover",
        action="store_true",
        help="Do not discover tracklists from source links when the prompt has no tracks.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Parse the prompt and print queries without downloading.")
    parser.add_argument("--output", type=Path, help="Write JSON results to this file instead of stdout.")
    args = parser.parse_args()

    prompt = _read_prompt(args.prompt, args.prompt_file)
    if not prompt.strip():
        print("No prompt supplied.", file=sys.stderr)
        return 2

    try:
        prompt, spec, discovery_notes = _resolve_prompt(prompt, args.prompt_file, discover=not args.no_discover)
    except TracklistDiscoveryError as exc:
        print(f"Tracklist discovery failed: {exc}", file=sys.stderr)
        return 1
    style = args.style or spec.style
    set_name = args.set_name or (args.prompt_file.stem if args.prompt_file else None)
    downloads_dir = args.downloads_dir / set_name if set_name else args.downloads_dir

    if args.dry_run:
        rendered = json.dumps(
            {
                "style": style,
                "downloads_dir": str(downloads_dir),
                "tracks": [
                    {"query": track.query, "title": track.title, "artist": track.artist}
                    for track in spec.tracks
                ],
                "discovery_notes": discovery_notes,
            },
            indent=2,
            ensure_ascii=False,
        )
    else:
        try:
            results = asyncio.run(
                download_from_prompt(
                    prompt,
                    style=style,
                    release_date=args.release_date,
                    downloads_dir=downloads_dir,
                    headless=not args.headed,
                    timeout_ms=args.timeout_ms,
                )
            )
        except MuzpaScraperError as exc:
            print(f"Download failed: {exc}", file=sys.stderr)
            return 1
        results = organize_set_results(results, spec.tracks, downloads_dir)
        if set_name:
            write_markdown_report(
                results,
                spec.tracks,
                style=style,
                report_path=downloads_dir / "results" / "output.md",
            )
        rendered = _render_results(results)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        print(rendered)
    return 0


def _resolve_prompt(prompt: str, prompt_file: Path | None, *, discover: bool):
    spec = parse_prompt_spec(prompt)
    if spec.tracks or not discover:
        return prompt, spec, []
    if not spec.source_urls:
        return prompt, spec, []

    discovery = discover_tracklist_from_urls(spec.source_urls)
    prompt = append_tracks_section(prompt, discovery.markdown_tracks())
    if prompt_file:
        prompt_file.write_text(prompt + "\n", encoding="utf-8")
    return prompt, parse_prompt_spec(prompt), discovery.notes


def _read_prompt(parts: list[str], prompt_file: Path | None) -> str:
    if prompt_file:
        return prompt_file.read_text(encoding="utf-8")
    if parts:
        return " ".join(parts)
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return ""


def _render_results(results) -> str:
    return json.dumps(
        {
            "results": [
                {
                    "query": result.query,
                    "status": result.status,
                    "path": str(result.path) if result.path else None,
                    "filename": result.filename,
                    "download_url": result.download_url,
                    "matched_text": result.matched_text,
                    "score": result.score,
                    "message": result.message,
                }
                for result in results
            ]
        },
        indent=2,
        ensure_ascii=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
