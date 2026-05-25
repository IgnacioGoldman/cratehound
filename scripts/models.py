"""
Pydantic models for the prompt-to-download API.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class PromptDownloadRequest(BaseModel):
    prompt: str
    style: Optional[str] = None
    release_date: Optional[str] = None
    timeout_ms: int = 25_000


class PromptDownloadItem(BaseModel):
    query: str
    status: str
    path: Optional[str] = None
    filename: Optional[str] = None
    download_url: Optional[str] = None
    matched_text: Optional[str] = None
    score: float = 0
    message: Optional[str] = None


class PromptDownloadResponse(BaseModel):
    results: list[PromptDownloadItem]
