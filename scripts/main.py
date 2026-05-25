"""
FastAPI entrypoint for prompt-to-download Muzpa workflow.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException

from .models import PromptDownloadItem, PromptDownloadRequest, PromptDownloadResponse
from .prompt_download import download_from_prompt
from .sources.muzpa_scraper import (
    MuzpaCredentialsError,
    MuzpaDependencyError,
    MuzpaScraperError,
)

app = FastAPI(title="cratehound", version="0.1.0")

DOWNLOADS_DIR = Path("downloads")
DOWNLOADS_DIR.mkdir(exist_ok=True)


@app.post("/api/downloads", response_model=PromptDownloadResponse)
async def download_prompt(req: PromptDownloadRequest):
    if not req.prompt.strip():
        raise HTTPException(status_code=400, detail="No prompt supplied.")

    try:
        results = await download_from_prompt(
            req.prompt,
            style=req.style,
            release_date=req.release_date,
            downloads_dir=DOWNLOADS_DIR,
            timeout_ms=req.timeout_ms,
        )
    except MuzpaCredentialsError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except MuzpaDependencyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except MuzpaScraperError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if not results:
        raise HTTPException(status_code=400, detail="No song queries could be parsed from the prompt.")

    return PromptDownloadResponse(
        results=[
            PromptDownloadItem(
                query=result.query,
                status=result.status,
                path=str(result.path) if result.path else None,
                filename=result.filename,
                download_url=result.download_url,
                matched_text=result.matched_text,
                score=result.score,
                message=result.message,
            )
            for result in results
        ]
    )
