from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.app.api import gonka
from backend.app.config import get_settings


class FakeResponse:
    def __init__(self, body: dict, status_code: int = 200) -> None:
        self._body = body
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise gonka.httpx.HTTPStatusError(
                "upstream failure", request=gonka.httpx.Request("POST", "https://gonka"), response=gonka.httpx.Response(self.status_code),
            )

    def json(self) -> dict:
        return self._body


class FakeClient:
    response: FakeResponse
    request_headers: dict[str, str] | None = None

    def __init__(self, **_: object) -> None:
        pass

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def post(self, _url: str, *, json: dict, headers: dict[str, str]) -> FakeResponse:
        FakeClient.request_headers = headers
        return self.response

    async def get(self, _url: str, *, headers: dict[str, str]) -> FakeResponse:
        FakeClient.request_headers = headers
        return self.response


def configure_key(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gonka_api_key", "test-only-key")


@pytest.mark.asyncio
async def test_empty_input_is_rejected() -> None:
    with pytest.raises(ValueError):
        gonka.VerificationRequest(text="   ")


@pytest.mark.asyncio
async def test_success_parses_result_and_preserves_request_id(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_key(monkeypatch)
    FakeClient.response = FakeResponse({
        "id": "chatcmpl-real-123",
        "choices": [{"message": {"content": '{"truth_score": 87, "verdict": "Likely Supported", "reasoning": "The text provides dates and amounts.", "evidence": ["FY2024 figure"], "confidence": 0.87, "caveats": ["No source document was supplied."]}'}}],
    })
    monkeypatch.setattr(gonka.httpx, "AsyncClient", FakeClient)

    result = await gonka.verify_claim(gonka.VerificationRequest(text="Revenue was INR 500 crore."))

    assert result.truth_score == 87
    assert result.verdict == "Likely Supported"
    assert result.gonka_request_id == "chatcmpl-real-123"
    assert FakeClient.request_headers == {"Authorization": "Bearer test-only-key"}


@pytest.mark.asyncio
async def test_missing_key_returns_service_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "gonka_api_key", "")
    with pytest.raises(HTTPException) as exc_info:
        await gonka.verify_claim(gonka.VerificationRequest(text="A claim."))
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_upstream_failure_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_key(monkeypatch)
    FakeClient.response = FakeResponse({}, status_code=502)
    monkeypatch.setattr(gonka.httpx, "AsyncClient", FakeClient)
    with pytest.raises(HTTPException) as exc_info:
        await gonka.verify_claim(gonka.VerificationRequest(text="A claim."))
    assert exc_info.value.status_code == 502
    assert "API key" not in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_models_endpoint_reports_configured_model(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_key(monkeypatch)
    FakeClient.response = FakeResponse({"data": [{"id": "deepseek-ai/DeepSeek-V4-Flash-0731"}]})
    monkeypatch.setattr(gonka.httpx, "AsyncClient", FakeClient)
    result = await gonka.list_models()
    assert result.available is True
    assert result.configured_model == "deepseek-ai/DeepSeek-V4-Flash-0731"
