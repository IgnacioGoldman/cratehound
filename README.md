# Get Started

1. Create `.env` from .env.example

```bash
MUZPA_USER=your_muzpa_login_or_email
MUZPA_PWD=your_muzpa_password
MUZPA_BROWSER_CHANNEL=chrome
MUZPA_BROWSER_PATH=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome
```

2. Create an input file with a `## Tracks` section, or with a YouTube/SoundCloud link that can be used to discover the tracklist.

3. Ask AI with the following prompt

"Download set input/organic-1.md"

If the input file already has tracks, those are used directly. If it only has a YouTube/SoundCloud link, the downloader first tries to discover the tracklist, writes it back into the input file, then downloads the set.

Tracklist discovery is documented as a repo-local Codex skill in `.codex/skills/discover-dj-tracklists`.
