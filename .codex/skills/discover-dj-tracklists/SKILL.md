---
name: discover-dj-tracklists
description: Discover DJ set tracklists from public YouTube or SoundCloud links, especially when a cratehound input file has a source link but no ## Tracks section. Use when asked to identify tracks, build an input/*.md tracklist, inspect SoundCloud comments, inspect YouTube descriptions/comments, or prepare a set for the Muzpa downloader before downloading.
---

# Discover DJ Tracklists

## Workflow

1. Inspect the source page metadata first.
   - SoundCloud: fetch the public page, capture the track id from the hydratable sound data, and capture the API client id from the hydratable apiClient data.
   - YouTube: inspect the page/player metadata and description for timestamped tracklists before trying comments.

2. Prefer authoritative and timestamped sources.
   - Use the uploader description when it contains a tracklist.
   - For SoundCloud, query threaded comments with:
     `https://api-v2.soundcloud.com/tracks/{track_id}/comments?client_id={client_id}&threaded=1&filter_replies=0&limit=200&offset=0&linked_partitioning=1`
   - Search comments for full TL/tracklist posts, DJ/uploader replies, and timestamped ID confirmations.

3. Convert findings into cratehound input format.
   - Keep sections as `## SoundCloud` or `## Youtube`, `## Style`, and `## Tracks`.
   - Use `HH:MM:SS Title - Artist` lines because cratehound assumes title first.
   - If a title itself contains ` - `, write `HH:MM:SS Title by Artist` so the parser keeps the title intact.
   - Preserve timestamps from the best source; if a comment has malformed timing, estimate from nearby track boundaries and note the uncertainty.

4. Verify before download.
   - Run `.venv/bin/python -m scripts.download_cli --prompt-file input/<name>.md --dry-run`.
   - Check that title/artist fields are not reversed and that style is preserved.
   - Then run the normal download command if requested.

## SoundCloud Notes

- The page may not show comments without JavaScript, but the API endpoint does once the client id and track id are known.
- Threaded comments can include replies as separate collection items; do not rely only on top-level visible HTML.
- Strong clues include `TL`, `tracklist`, `ID`, `Track ID`, artist replies, and comments containing `Artist - Title`.
- Community tracklists often contain typos. Prefer uploader corrections over community text.

## YouTube Notes

- Start with the description and pinned/top comments.
- If comments are not accessible from local tools, use web search with exact video title plus `tracklist`, `track id`, and notable set/venue terms.
- If no public tracklist exists, say so clearly; do not invent IDs from audio alone without an audio-recognition tool.
