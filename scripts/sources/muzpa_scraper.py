"""
Browser automation for Muzpa release search.

This module logs in with credentials from the local .env file, searches the
authenticated releases page, and can download files exposed by that authenticated
page. It does not bypass Muzpa access controls.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import quote_plus, unquote, urljoin

from dotenv import load_dotenv

MUZPA_RELEASES_URL = "https://srv.muzpa.com/#/media/releases"
MUZPA_SEARCH_URL = "https://srv.muzpa.com/#/search"
DEFAULT_TIMEOUT_MS = 25_000


class MuzpaScraperError(RuntimeError):
    """Base error for scraper failures."""


class MuzpaCredentialsError(MuzpaScraperError):
    """Raised when credentials are missing or rejected."""


class MuzpaDependencyError(MuzpaScraperError):
    """Raised when Playwright or a browser is unavailable."""


@dataclass(frozen=True)
class MuzpaTrackQuery:
    title: str
    artist: str

    @property
    def query(self) -> str:
        return f"{self.title} {self.artist}".strip()


@dataclass(frozen=True)
class MuzpaReleaseMatch:
    raw_text: str
    url: str | None
    score: float


@dataclass(frozen=True)
class MuzpaTrackResult:
    query: str
    status: str
    matches: list[MuzpaReleaseMatch]
    message: str | None = None


@dataclass(frozen=True)
class MuzpaDownloadResult:
    query: str
    status: str
    path: Path | None = None
    filename: str | None = None
    download_url: str | None = None
    matched_text: str | None = None
    score: float = 0
    message: str | None = None


@dataclass(frozen=True)
class MuzpaDownloadCandidate:
    raw_text: str
    download_url: str
    score: float
    format: str | None = None
    style_score: float = 0


@dataclass(frozen=True)
class MuzpaReleaseTrack:
    artist: str
    title: str
    genre: str
    quality: str
    raw_text: str
    release_id: str | None = None
    release_title: str | None = None
    label: str | None = None
    release_date: str | None = None


def _load_credentials() -> tuple[str, str]:
    root_env = _find_project_env()
    load_dotenv(root_env, override=True)

    user = os.getenv("MUZPA_USER", "").strip()
    password = os.getenv("MUZPA_PWD", "")
    if not user or not password:
        raise MuzpaCredentialsError("MUZPA_USER and MUZPA_PWD must be set in .env.")
    return user, password


def _find_project_env() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / ".env"
        if candidate.exists():
            return candidate
    return Path(".env")


async def scrape_muzpa_tracks(
    tracks: Iterable[MuzpaTrackQuery],
    *,
    release_date: str | None = None,
    limit_per_track: int = 3,
    headless: bool = True,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
) -> list[MuzpaTrackResult]:
    """Search Muzpa releases for each track and return visible row/card matches."""
    try:
        from playwright.async_api import Error as PlaywrightError
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise MuzpaDependencyError(
            "Playwright is not installed. Run `pip install -r requirements.txt` in the project venv."
        ) from exc

    user, password = _load_credentials()
    prepared_tracks = [track for track in tracks if track.query]
    if not prepared_tracks:
        return []

    try:
        async with async_playwright() as playwright:
            browser = await _launch_browser(playwright, headless)
            context = await browser.new_context()
            page = await context.new_page()
            page.set_default_timeout(timeout_ms)

            try:
                await page.goto(MUZPA_RELEASES_URL, wait_until="domcontentloaded")
                await _settle(page, timeout_ms)
                await _login_if_needed(page, user, password, timeout_ms)
                await _open_releases(page, release_date, timeout_ms)

                results: list[MuzpaTrackResult] = []
                for track in prepared_tracks:
                    result = await _search_one(page, track.query, limit_per_track, timeout_ms)
                    results.append(result)
                return results
            finally:
                await context.close()
                await browser.close()
    except MuzpaScraperError:
        raise
    except PlaywrightTimeoutError as exc:
        raise MuzpaScraperError("Muzpa timed out while loading or searching releases.") from exc
    except PlaywrightError as exc:
        message = str(exc)
        if "Executable doesn't exist" in message or "playwright install" in message:
            raise MuzpaDependencyError(
                "Playwright is installed, but Chromium is missing. Run `python -m playwright install chromium`."
            ) from exc
        raise MuzpaScraperError(message) from exc


async def download_muzpa_track(
    track: MuzpaTrackQuery,
    *,
    release_date: str | None = None,
    downloads_dir: Path = Path("downloads"),
    headless: bool = True,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    minimum_score: float = 0.6,
    style: str | None = None,
) -> MuzpaDownloadResult:
    """Download the best matching Muzpa track exposed to the authenticated user."""
    try:
        from playwright.async_api import Error as PlaywrightError
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise MuzpaDependencyError(
            "Playwright is not installed. Run `pip install -r requirements.txt` in the project venv."
        ) from exc

    if not track.query:
        return MuzpaDownloadResult(query=track.query, status="not_found", message="No query supplied.")

    downloads_dir.mkdir(parents=True, exist_ok=True)
    existing = _existing_download_result(track, downloads_dir)
    if existing is not None:
        return existing

    user, password = _load_credentials()

    try:
        async with async_playwright() as playwright:
            browser = await _launch_browser(playwright, headless)
            context = await browser.new_context(accept_downloads=True)
            page = await context.new_page()
            page.set_default_timeout(timeout_ms)

            try:
                await _prepare_muzpa_page(page, user, password, release_date, timeout_ms)
                return await _download_muzpa_track_on_page(
                    context,
                    page,
                    track,
                    downloads_dir=downloads_dir,
                    timeout_ms=timeout_ms,
                    minimum_score=minimum_score,
                    style=style,
                )
            finally:
                await context.close()
                await browser.close()
    except MuzpaScraperError:
        raise
    except PlaywrightTimeoutError as exc:
        raise MuzpaScraperError("Muzpa timed out while loading, searching, or downloading.") from exc
    except PlaywrightError as exc:
        message = str(exc)
        if "Executable doesn't exist" in message or "playwright install" in message:
            raise MuzpaDependencyError(
                "Playwright is installed, but Chromium is missing. Run `python -m playwright install chromium`."
            ) from exc
        raise MuzpaScraperError(message) from exc


async def download_muzpa_tracks(
    tracks: Iterable[MuzpaTrackQuery],
    *,
    release_date: str | None = None,
    downloads_dir: Path = Path("downloads"),
    headless: bool = True,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    minimum_score: float = 0.6,
    style: str | None = None,
) -> list[MuzpaDownloadResult]:
    """Download many tracks using one authenticated browser session."""
    try:
        from playwright.async_api import Error as PlaywrightError
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise MuzpaDependencyError(
            "Playwright is not installed. Run `pip install -r requirements.txt` in the project venv."
        ) from exc

    prepared_tracks = [track for track in tracks if track.query]
    if not prepared_tracks:
        return []

    downloads_dir.mkdir(parents=True, exist_ok=True)
    results_by_index: dict[int, MuzpaDownloadResult] = {}
    pending_tracks: list[tuple[int, MuzpaTrackQuery]] = []
    for index, track in enumerate(prepared_tracks):
        existing = _existing_download_result(track, downloads_dir)
        if existing is None:
            pending_tracks.append((index, track))
        else:
            results_by_index[index] = existing

    if not pending_tracks:
        return [results_by_index[index] for index in range(len(prepared_tracks))]

    user, password = _load_credentials()

    try:
        async with async_playwright() as playwright:
            browser = await _launch_browser(playwright, headless)
            context = await browser.new_context(accept_downloads=True)
            page = await context.new_page()
            page.set_default_timeout(timeout_ms)

            try:
                await _prepare_muzpa_page(page, user, password, release_date, timeout_ms)
                for index, track in pending_tracks:
                    results_by_index[index] = (
                        await _download_muzpa_track_on_page(
                            context,
                            page,
                            track,
                            downloads_dir=downloads_dir,
                            timeout_ms=timeout_ms,
                            minimum_score=minimum_score,
                            style=style,
                        )
                    )
                return [results_by_index[index] for index in range(len(prepared_tracks))]
            finally:
                await context.close()
                await browser.close()
    except MuzpaScraperError:
        raise
    except PlaywrightTimeoutError as exc:
        raise MuzpaScraperError("Muzpa timed out while loading, searching, or downloading.") from exc
    except PlaywrightError as exc:
        message = str(exc)
        if "Executable doesn't exist" in message or "playwright install" in message:
            raise MuzpaDependencyError(
                "Playwright is installed, but Chromium is missing. Run `python -m playwright install chromium`."
            ) from exc
        raise MuzpaScraperError(message) from exc


async def _settle(page, timeout_ms: int) -> None:
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        await asyncio.sleep(0.8)


async def _prepare_muzpa_page(page, user: str, password: str, release_date: str | None, timeout_ms: int) -> None:
    await page.goto(MUZPA_RELEASES_URL, wait_until="domcontentloaded")
    await _settle(page, timeout_ms)
    await _login_if_needed(page, user, password, timeout_ms)
    await _open_releases(page, release_date, timeout_ms)


async def _download_muzpa_track_on_page(
    context,
    page,
    track: MuzpaTrackQuery,
    *,
    downloads_dir: Path,
    timeout_ms: int,
    minimum_score: float,
    style: str | None,
) -> MuzpaDownloadResult:
    await _open_global_search(page, track.query, timeout_ms)

    candidates = await _extract_download_candidates(page, track.query, style=style)
    if not candidates:
        existing = _existing_download_result(track, downloads_dir)
        if existing is not None:
            return existing
        return MuzpaDownloadResult(
            query=track.query,
            status="not_found",
            message="No downloadable Muzpa track matched the visible results.",
        )

    best = candidates[0]
    if best.score < minimum_score:
        existing = _existing_download_result(track, downloads_dir)
        if existing is not None:
            return existing
        return MuzpaDownloadResult(
            query=track.query,
            status="not_found",
            matched_text=best.raw_text,
            download_url=best.download_url,
            score=best.score,
            message=(
                f"Best downloadable Muzpa match scored {best.score}, "
                f"below the {minimum_score} threshold."
            ),
        )

    title_score = _score_match(track.title, best.raw_text) if track.title else best.score
    if track.title and title_score < 0.75:
        existing = _existing_download_result(track, downloads_dir)
        if existing is not None:
            return existing
        return MuzpaDownloadResult(
            query=track.query,
            status="not_found",
            matched_text=best.raw_text,
            download_url=best.download_url,
            score=best.score,
            message=(
                "Best downloadable Muzpa match did not contain the requested title "
                f"closely enough. Title score: {title_score}."
            ),
        )

    existing = _find_existing_download(
        downloads_dir,
        track.query,
        best.raw_text,
        require_identity=bool(style),
        track=track,
    )
    if existing is not None:
        return MuzpaDownloadResult(
            query=track.query,
            status="already_downloaded",
            path=existing,
            filename=existing.name,
            download_url=best.download_url,
            matched_text=best.raw_text,
            score=best.score,
            message="Matching file already exists; skipped download.",
        )

    response = await context.request.get(best.download_url, timeout=timeout_ms)
    if not response.ok:
        message = await response.text()
        raise MuzpaScraperError(
            f"Muzpa download failed with HTTP {response.status}: {message[:180]}"
        )

    filename = _download_filename(response.headers, best.download_url, track, best.format)
    path = downloads_dir / filename
    if path.exists():
        return MuzpaDownloadResult(
            query=track.query,
            status="already_downloaded",
            path=path,
            filename=path.name,
            download_url=best.download_url,
            matched_text=best.raw_text,
            score=best.score,
            message="Exact filename already exists; skipped download.",
        )

    path.write_bytes(await response.body())

    return MuzpaDownloadResult(
        query=track.query,
        status="downloaded",
        path=path,
        filename=path.name,
        download_url=best.download_url,
        matched_text=best.raw_text,
        score=best.score,
    )


async def _launch_browser(playwright, headless: bool):
    from playwright.async_api import Error as PlaywrightError

    attempts = []
    browser_path = os.getenv("MUZPA_BROWSER_PATH", "").strip() or _default_chrome_path()
    browser_channel = os.getenv("MUZPA_BROWSER_CHANNEL", "chrome").strip()

    launch_options = []
    if browser_path:
        launch_options.append(("local Chrome", {"executable_path": browser_path}))
    if browser_channel:
        launch_options.append((f"{browser_channel} channel", {"channel": browser_channel}))
    launch_options.append(("Playwright Chromium", {}))

    for label, options in launch_options:
        try:
            return await playwright.chromium.launch(headless=headless, **options)
        except PlaywrightError as exc:
            attempts.append(f"{label}: {str(exc).splitlines()[0]}")

    detail = " | ".join(attempts)
    raise MuzpaDependencyError(
        "Could not launch a Chromium browser. "
        "Install Playwright Chromium with `python -m playwright install chromium`, "
        "or set MUZPA_BROWSER_PATH to a working Chrome/Chromium executable. "
        f"Attempts: {detail}"
    )


async def _login_if_needed(page, user: str, password: str, timeout_ms: int) -> None:
    if not await _looks_like_login(page):
        return

    await _api_login(page, user, password)
    await page.goto(MUZPA_RELEASES_URL, wait_until="domcontentloaded")
    await _settle(page, timeout_ms)
    if not await _looks_like_login(page):
        return

    login_input = await _first_visible(
        page,
        [
            "#login",
            'input[name="login"]',
            'input[type="email"]',
            'input[placeholder*="login" i]',
            'input[placeholder*="mail" i]',
        ],
    )
    password_input = await _first_visible(page, ["#password", 'input[name="password"]', 'input[type="password"]'])
    if login_input is None or password_input is None:
        raise MuzpaCredentialsError("Could not find Muzpa login fields.")

    await login_input.fill(user)
    await password_input.fill(password)

    submit = await _first_visible(page, ['button[type="submit"]', 'input[type="submit"]', "button:has-text('LOGIN')"])
    if submit is None:
        await password_input.press("Enter")
    else:
        await submit.click()

    await _settle(page, timeout_ms)
    if await _looks_like_login(page):
        message = await _login_error_text(page)
        raise MuzpaCredentialsError(message or "Muzpa rejected the configured credentials.")


async def _api_login(page, user: str, password: str) -> None:
    response = await page.context.request.post(
        "https://muzpa.com/api/session",
        data={"login": user, "password": password},
        headers={"Content-Type": "application/json"},
    )
    if response.ok:
        return

    try:
        body = await response.json()
        message = body.get("message") or body.get("code")
    except Exception:
        message = await response.text()
    raise MuzpaCredentialsError(message or f"Muzpa login failed with HTTP {response.status}.")


async def _open_releases(page, release_date: str | None, timeout_ms: int) -> None:
    url = MUZPA_RELEASES_URL
    if release_date:
        url = f"{url}?date={quote_plus(release_date)}"
    await page.goto(url, wait_until="domcontentloaded")
    await _settle(page, timeout_ms)

    if await _looks_like_login(page):
        raise MuzpaCredentialsError("Muzpa redirected back to login before opening releases.")


async def _open_global_search(page, query: str, timeout_ms: int) -> None:
    url = f"{MUZPA_SEARCH_URL}?text={quote_plus(query)}"
    await page.goto(url, wait_until="domcontentloaded")
    await _settle(page, min(timeout_ms, 8_000))
    await asyncio.sleep(1.0)

    if await _looks_like_login(page):
        raise MuzpaCredentialsError("Muzpa redirected back to login before opening search.")


async def _search_one(page, query: str, limit: int, timeout_ms: int) -> MuzpaTrackResult:
    if not await _search_query(page, query, timeout_ms):
        matches = await _extract_visible_matches(page, query, limit)
        return MuzpaTrackResult(
            query=query,
            status="found" if matches else "not_found",
            matches=matches,
            message="Could not identify a search box; scored the visible releases instead.",
        )

    matches = await _extract_visible_matches(page, query, limit)
    return MuzpaTrackResult(query=query, status="found" if matches else "not_found", matches=matches)


async def _search_query(page, query: str, timeout_ms: int) -> bool:
    search = await _find_search_input(page)
    if search is None:
        return False

    await search.fill("")
    await search.fill(query)
    await search.press("Enter")
    await asyncio.sleep(1.2)
    return True


async def _find_search_input(page):
    return await _first_visible(
        page,
        [
            'input[type="search"]',
            'input[placeholder*="search" i]',
            'input[aria-label*="search" i]',
            'input[name*="search" i]',
            'input[type="text"]:not(#login):not([name="login"])',
        ],
    )


async def _looks_like_login(page) -> bool:
    password_input = await _first_visible(page, ["#password", 'input[name="password"]', 'input[type="password"]'])
    if password_input is None:
        return False
    login_input = await _first_visible(page, ["#login", 'input[name="login"]', 'input[type="email"]'])
    return login_input is not None


async def _first_visible(page, selectors: list[str]):
    for selector in selectors:
        locator = page.locator(selector)
        try:
            count = await locator.count()
        except Exception:
            continue
        for index in range(min(count, 8)):
            candidate = locator.nth(index)
            try:
                if await candidate.is_visible():
                    return candidate
            except Exception:
                continue
    return None


async def _login_error_text(page) -> str | None:
    for selector in [".rec-form-message", ".message", "[role='alert']", ".error"]:
        locator = page.locator(selector)
        try:
            count = await locator.count()
        except Exception:
            continue
        for index in range(min(count, 4)):
            candidate = locator.nth(index)
            try:
                if await candidate.is_visible():
                    text = re.sub(r"\s+", " ", await candidate.inner_text(timeout=1_000)).strip()
                    if text:
                        return text[:180]
            except Exception:
                continue

    text = await page.locator("body").inner_text(timeout=2_000)
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return None
    for marker in ("Oops", "wrong", "invalid", "error"):
        index = normalized.lower().find(marker.lower())
        if index >= 0:
            return normalized[index : index + 180]
    return None


async def _extract_visible_matches(page, query: str, limit: int) -> list[MuzpaReleaseMatch]:
    candidates = await page.evaluate(
        """
        () => {
          const selectors = [
            "table tbody tr",
            ".table tbody tr",
            "[role='row']",
            ".release",
            ".media-release",
            ".media-list > *",
            ".list-group-item",
            ".card",
            "ms-release-track",
            "ms-release-audio-main",
            "ms-release-main",
            "li",
            "a[href*='release']"
          ];
          const seen = new Set();
          const rows = [];

          function visible(el) {
            const style = window.getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return style.visibility !== "hidden" &&
              style.display !== "none" &&
              rect.width > 0 &&
              rect.height > 0;
          }

          for (const selector of selectors) {
            for (const el of document.querySelectorAll(selector)) {
              if (!visible(el)) continue;
              const text = (el.innerText || el.textContent || "").replace(/\\s+/g, " ").trim();
              if (text.length < 4 || text.length > 1200 || seen.has(text)) continue;
              seen.add(text);

              const links = [];
              if (el.matches("a[href]")) links.push(el.href);
              for (const link of el.querySelectorAll("a[href]")) links.push(link.href);
              rows.push({ text, links });
            }
          }
          return rows;
        }
        """
    )

    scored = []
    for candidate in candidates:
        text = candidate.get("text", "")
        score = _score_match(query, text)
        if score <= 0:
            continue
        url = _first_non_download_url(candidate.get("links", []))
        scored.append(MuzpaReleaseMatch(raw_text=text[:1000], url=url, score=score))

    scored.sort(key=lambda item: item.score, reverse=True)
    return scored[:limit]


async def _extract_download_candidates(page, query: str, *, style: str | None = None) -> list[MuzpaDownloadCandidate]:
    candidates = await page.locator("a[href*='/dwnld/track/']").evaluate_all(
        """
        (links) => links.map((link) => {
          const track = link.closest("ms-release-track") || link.closest("ms-release-audio-main") || link.parentElement;
          const text = ((track && (track.innerText || track.textContent)) || link.innerText || "")
            .replace(/\\s+/g, " ")
            .trim();
          const href = link.href || link.getAttribute("href") || "";
          const format = (link.innerText || link.textContent || href.split(".").pop() || "")
            .replace(/\\s+/g, " ")
            .trim();
          return { text, href, format };
        }).filter((item) => item.href && item.text)
        """
    )

    scored: list[MuzpaDownloadCandidate] = []
    for candidate in candidates:
        text = candidate.get("text", "")
        score = _score_match(query, text)
        if score <= 0:
            continue
        style_score = _score_style(style, text)
        scored.append(
            MuzpaDownloadCandidate(
                raw_text=text[:1000],
                download_url=urljoin(MUZPA_RELEASES_URL, candidate.get("href", "")),
                score=score,
                format=(candidate.get("format") or None),
                style_score=style_score,
            )
        )

    scored.sort(
        key=lambda item: (_candidate_rank(item, style), item.score, _format_rank(item.format)),
        reverse=True,
    )
    return scored


def _score_match(query: str, text: str) -> float:
    query_tokens = _tokens(query)
    if not query_tokens:
        return 0

    text_tokens = set(_tokens(text))
    matched = sum(1 for token in query_tokens if token in text_tokens)
    if matched == 0:
        return 0

    score = matched / len(query_tokens)
    if query.lower() in text.lower():
        score += 0.5
    return round(score, 3)


def _candidate_rank(candidate: MuzpaDownloadCandidate, style: str | None) -> float:
    if not style:
        return candidate.score
    return candidate.score + (candidate.style_score * 0.45)


def _score_style(style: str | None, text: str) -> float:
    style_tokens = _style_tokens(style)
    if not style_tokens:
        return 0

    text_tokens = set(_tokens(text))
    matched = sum(1 for token in style_tokens if token in text_tokens)
    score = matched / len(style_tokens)

    lowered = text.lower()
    if "organic house" in lowered and {"organic", "house"}.intersection(style_tokens):
        score += 0.35
    if "downtempo" in lowered and ("organic" in style_tokens or "downtempo" in style_tokens):
        score += 0.15
    if "techno" in lowered and "organic" in style_tokens and "organic" not in lowered:
        score -= 0.25

    return round(max(score, 0), 3)


def _style_tokens(style: str | None) -> set[str]:
    if not style:
        return set()
    aliases = {
        "organic": {"organic", "downtempo"},
        "house": {"house"},
    }
    tokens = set(_tokens(style))
    expanded = set(tokens)
    for token in tokens:
        expanded.update(aliases.get(token, set()))
    return expanded


def _tokens(value: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) > 1]


def _default_chrome_path() -> str | None:
    path = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    return str(path) if path.exists() else None


def _first_non_download_url(urls: list[str]) -> str | None:
    for url in urls:
        lowered = url.lower()
        if "download" in lowered or "/dl" in lowered:
            continue
        return url
    return None


def _download_filename(
    headers: dict[str, str],
    download_url: str,
    track: MuzpaTrackQuery,
    file_format: str | None,
) -> str:
    disposition = headers.get("content-disposition", "")
    match = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", disposition, flags=re.I)
    if match:
        filename = unquote(match.group(1)).strip()
    else:
        filename = unquote(download_url.rstrip("/").split("/")[-1]).strip()

    if not filename or "." not in filename:
        ext = _extension_from_format(file_format) or ".mp3"
        filename = f"{track.artist} - {track.title}".strip(" -") + ext

    return _safe_filename(filename)


def _extension_from_format(file_format: str | None) -> str | None:
    if not file_format:
        return None
    normalized = file_format.lower().strip(". ")
    for ext in ("wav", "aiff", "aif", "flac", "mp3", "zip"):
        if ext in normalized:
            return f".{ext}"
    return None


def _format_rank(file_format: str | None) -> int:
    normalized = (file_format or "").lower()
    if any(ext in normalized for ext in ("wav", "aiff", "aif", "flac")):
        return 3
    if "mp3" in normalized:
        return 2
    if "zip" in normalized:
        return 1
    return 0


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[\\\\/:*?\"<>|]+", "_", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or "muzpa-download.mp3"


def _find_existing_download(
    downloads_dir: Path,
    query: str,
    matched_text: str,
    *,
    require_identity: bool = False,
    track: MuzpaTrackQuery | None = None,
) -> Path | None:
    if not downloads_dir.exists():
        return None

    audio_extensions = {".aif", ".aiff", ".flac", ".mp3", ".wav", ".zip"}
    matches: list[tuple[float, Path]] = []
    for path in downloads_dir.iterdir():
        if not path.is_file() or path.suffix.lower() not in audio_extensions:
            continue
        filename = path.stem
        if track and track.title and _score_match(track.title, filename) < 0.75:
            continue
        identity_score = _score_match(_track_identity_text(matched_text), filename)
        score = identity_score if require_identity else max(_score_match(query, filename), identity_score)
        threshold = 0.75 if require_identity else 0.85
        if score >= threshold:
            matches.append((score, path))

    if not matches:
        return None

    matches.sort(key=lambda item: (item[0], -_duplicate_suffix_number(item[1])), reverse=True)
    return matches[0][1]


def _existing_download_result(track: MuzpaTrackQuery, downloads_dir: Path) -> MuzpaDownloadResult | None:
    path = _find_existing_download(downloads_dir, track.query, track.query, track=track)
    if path is None:
        return None

    return MuzpaDownloadResult(
        query=track.query,
        status="already_downloaded",
        path=path,
        filename=path.name,
        matched_text=path.stem,
        score=_score_match(track.query, path.stem),
        message="Matching file already exists locally; skipped Muzpa search and download.",
    )


def _track_identity_text(value: str) -> str:
    cleaned = re.sub(r"\b(?:mp3|wav|aiff?|flac|zip)\b", " ", value, flags=re.I)
    cleaned = re.sub(
        r"\b(?:house|techno|progressive|organic|downtempo|melodic|indie|dance|trance|breaks|dubstep|garage)\b",
        " ",
        cleaned,
        flags=re.I,
    )
    return re.sub(r"\s+", " ", cleaned).strip()


def _duplicate_suffix_number(path: Path) -> int:
    match = re.search(r"\((\d+)\)$", path.stem)
    if not match:
        return 0
    return int(match.group(1))
