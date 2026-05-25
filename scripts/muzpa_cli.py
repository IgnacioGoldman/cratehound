"""
Local CLI for Muzpa release metadata scraping.

Examples:
    python -m scripts.muzpa_cli --tracklist tracklist.txt --output muzpa_matches.json
    python -m scripts.muzpa_cli --query "Children Gorje Hewek" --headed
    python -m scripts.muzpa_cli --query "Children Gorje Hewek" --download
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from pathlib import Path

from .parser import parse_tracklist
from .sources.muzpa_scraper import (
    MuzpaScraperError,
    MuzpaTrackQuery,
    download_muzpa_tracks,
    scrape_muzpa_tracks,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Search Muzpa releases from the project venv.")
    parser.add_argument("--tracklist", type=Path, help="Path to a timestamped DJ tracklist.")
    parser.add_argument("--query", action="append", default=[], help="Raw search query. Can be passed more than once.")
    parser.add_argument("--release-date", help="Optional release date to pass to the releases view, YYYY-MM-DD.")
    parser.add_argument("--limit", type=int, default=3, help="Maximum matches per query.")
    parser.add_argument("--timeout-ms", type=int, default=25_000, help="Playwright timeout in milliseconds.")
    parser.add_argument("--headed", action="store_true", help="Show the browser while scraping.")
    parser.add_argument("--download", action="store_true", help="Download the best matching Muzpa track.")
    parser.add_argument("--downloads-dir", type=Path, default=Path("downloads"), help="Directory for downloaded files.")
    parser.add_argument("--format", choices=["json", "csv"], default="json", help="Output format.")
    parser.add_argument("--output", type=Path, help="Write results to this file instead of stdout.")
    args = parser.parse_args()

    try:
        tracks = _load_tracks(args.tracklist, args.query)
        if not tracks:
            print("No tracks or queries supplied.", file=sys.stderr)
            return 2

        if args.download:
            results = asyncio.run(
                _download_tracks(
                    tracks,
                    release_date=args.release_date,
                    downloads_dir=args.downloads_dir,
                    headless=not args.headed,
                    timeout_ms=args.timeout_ms,
                )
            )
        else:
            results = asyncio.run(
                scrape_muzpa_tracks(
                    tracks,
                    release_date=args.release_date,
                    limit_per_track=args.limit,
                    headless=not args.headed,
                    timeout_ms=args.timeout_ms,
                )
            )
    except MuzpaScraperError as exc:
        action = "download" if args.download else "scrape"
        print(f"Muzpa {action} failed: {exc}", file=sys.stderr)
        return 1

    rendered = _render_results(results, args.format)
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        print(rendered)
    return 0


async def _download_tracks(
    tracks: list[MuzpaTrackQuery],
    *,
    release_date: str | None,
    downloads_dir: Path,
    headless: bool,
    timeout_ms: int,
):
    return await download_muzpa_tracks(
        tracks,
        release_date=release_date,
        downloads_dir=downloads_dir,
        headless=headless,
        timeout_ms=timeout_ms,
    )


def _load_tracks(tracklist_path: Path | None, raw_queries: list[str]) -> list[MuzpaTrackQuery]:
    tracks: list[MuzpaTrackQuery] = []

    if tracklist_path:
        text = tracklist_path.read_text(encoding="utf-8")
    elif not raw_queries and not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        text = ""

    if text.strip():
        parsed = parse_tracklist(text)
        if parsed:
            tracks.extend(MuzpaTrackQuery(title=track.title, artist=track.artist) for track in parsed)
        else:
            tracks.extend(MuzpaTrackQuery(title=line.strip(), artist="") for line in text.splitlines() if line.strip())

    tracks.extend(MuzpaTrackQuery(title=query.strip(), artist="") for query in raw_queries if query.strip())
    return tracks


def _render_results(results, output_format: str) -> str:
    if results and hasattr(results[0], "filename"):
        return _render_download_results(results, output_format)

    rows = [
        {
            "query": result.query,
            "status": result.status,
            "message": result.message,
            "matches": [
                {
                    "raw_text": match.raw_text,
                    "url": match.url,
                    "score": match.score,
                }
                for match in result.matches
            ],
        }
        for result in results
    ]

    if output_format == "json":
        return json.dumps({"results": rows}, indent=2, ensure_ascii=False)

    from io import StringIO

    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(["query", "status", "match_rank", "score", "url", "raw_text", "message"])
    for row in rows:
        if row["matches"]:
            for index, match in enumerate(row["matches"], start=1):
                writer.writerow([
                    row["query"],
                    row["status"],
                    index,
                    match["score"],
                    match["url"] or "",
                    match["raw_text"],
                    row["message"] or "",
                ])
        else:
            writer.writerow([row["query"], row["status"], "", "", "", "", row["message"] or ""])
    return buf.getvalue()


def _render_download_results(results, output_format: str) -> str:
    rows = [
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

    if output_format == "json":
        return json.dumps({"results": rows}, indent=2, ensure_ascii=False)

    from io import StringIO

    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(["query", "status", "filename", "path", "score", "download_url", "matched_text", "message"])
    for row in rows:
        writer.writerow([
            row["query"],
            row["status"],
            row["filename"] or "",
            row["path"] or "",
            row["score"],
            row["download_url"] or "",
            row["matched_text"] or "",
            row["message"] or "",
        ])
    return buf.getvalue()


if __name__ == "__main__":
    raise SystemExit(main())
