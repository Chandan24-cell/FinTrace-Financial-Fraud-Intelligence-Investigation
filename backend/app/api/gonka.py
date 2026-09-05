"""GonkaRouter-backed financial claim verification."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/gonka", tags=["gonka"])

_MODEL = "deepseek-ai/DeepSeek-V4-Flash-0731"
_SYSTEM_PROMPT = """You are a financial claim verification assistant.
Analyze only the claim text provided by the user. Do not invent external evidence,
sources, or facts. If the text does not contain enough information to establish
truth, say that it cannot be fully verified. Do not reveal hidden chain-of-thought;
provide only a concise user-facing rationale.

Return ONLY valid JSON with exactly these fields:
{"truth_score": 0, "verdict": "Uncertain", "reasoning": "...",
 "evidence": ["..."], "confidence": 0.0, "caveats": ["..."]}

truth_score must be an integer from 0 to 100. confidence must be a number from
0 to 1. Verdict should be one of Supported, Likely Supported, Uncertain,
Likely False, or Unsupported."""


class VerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=12_000)

    @field_validator("text")
    @classmethod
    def non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must contain a claim to verify")
        return value.strip()


class VerificationResult(BaseModel):
    truth_score: int = Field(ge=0, le=100)
    verdict: str
    reasoning: str
    evidence: list[str]
    confidence: float = Field(ge=0, le=1)
    caveats: list[str]
    gonka_request_id: str = Field(min_length=1)
    status: str = "Verified through GonkaRouter"


class ModelsResponse(BaseModel):
    configured_model: str
    available: bool
    models: list[dict[str, Any]]


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            item.get("text", "") for item in content if isinstance(item, dict)
        )
    return ""


def _parse_model_json(content: Any) -> dict[str, Any]:
    text = _content_text(content).strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Gonka returned an unreadable verification response.",
        ) from exc
    if not isinstance(value, dict):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Gonka returned an invalid verification response.",
        )
    return value


async def _post_chat(text: str) -> dict[str, Any]:
    settings = get_settings()
    if not settings.gonka_api_key.strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gonka verification is not configured. Set GONKA_API_KEY on the backend.",
        )
    url = f"{settings.gonka_api_base_url.rstrip('/')}/v1/chat/completions"
    payload = {
        "model": settings.gonka_model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        "temperature": 0,
    }
    try:
        async with httpx.AsyncClient(timeout=settings.gonka_timeout_sec) as client:
            response = await client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {settings.gonka_api_key}"},
            )
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Gonka verification request failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to complete Gonka verification. Please try again.",
        ) from exc
    if not isinstance(body, dict) or not body.get("id"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Gonka returned no verification request identifier.",
        )
    return body


@router.post("/verify", response_model=VerificationResult)
async def verify_claim(payload: VerificationRequest) -> VerificationResult:
    response = await _post_chat(payload.text)
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Gonka returned no verification choice.",
        )
    message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
    values = _parse_model_json(message.get("content"))
    try:
        return VerificationResult(**values, gonka_request_id=str(response["id"]))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Gonka returned an invalid verification structure.",
        ) from exc


@router.get("/models", response_model=ModelsResponse)
async def list_models() -> ModelsResponse:
    settings = get_settings()
    if not settings.gonka_api_key.strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gonka verification is not configured. Set GONKA_API_KEY on the backend.",
        )
    url = f"{settings.gonka_api_base_url.rstrip('/')}/v1/models"
    try:
        async with httpx.AsyncClient(timeout=settings.gonka_timeout_sec) as client:
            response = await client.get(
                url,
                headers={"Authorization": f"Bearer {settings.gonka_api_key}"},
            )
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Gonka models request failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to reach Gonka models service.",
        ) from exc
    models = body.get("data", []) if isinstance(body, dict) else []
    return ModelsResponse(
        configured_model=settings.gonka_model,
        available=any(isinstance(item, dict) and item.get("id") == _MODEL for item in models),
        models=models if isinstance(models, list) else [],
    )