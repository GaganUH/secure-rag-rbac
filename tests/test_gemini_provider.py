import httpx
import pytest

import app.gemini_provider as gemini_provider


def test_gemini_request_uses_header_and_prepared_prompt(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "steps": [
                    {"type": "thought", "content": [{"type": "text", "text": "Do not use"}]},
                    {
                        "type": "model_output",
                        "content": [{"type": "text", "text": "Two days per week. [1]"}],
                    },
                ],
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(gemini_provider.httpx, "post", fake_post)
    answer = gemini_provider.GeminiAnswerProvider().generate("Authorized context")

    assert answer == "Two days per week. [1]"
    assert len(calls) == 1
    url, kwargs = calls[0]
    assert url.endswith("/v1beta/interactions")
    assert "test-key-not-real" not in url
    assert kwargs["headers"]["x-goog-api-key"] == "test-key-not-real"
    assert kwargs["json"] == {
        "model": "gemini-3.8-flash",
        "store": False,
        "input": "Authorized context",
    }


def test_gemini_provider_does_not_call_http_without_key(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(
        gemini_provider.httpx,
        "post",
        lambda *_args, **_kwargs: pytest.fail("No request should be made"),
    )

    with pytest.raises(gemini_provider.GeminiConfigurationError):
        gemini_provider.GeminiAnswerProvider().generate("prompt")


def test_gemini_error_does_not_expose_provider_response(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    request = httpx.Request("POST", "https://generativelanguage.googleapis.com")

    def fake_post(*_args, **_kwargs):
        return httpx.Response(
            403,
            text="secret prompt in provider error",
            request=request,
        )

    monkeypatch.setattr(gemini_provider.httpx, "post", fake_post)
    with pytest.raises(gemini_provider.GeminiGenerationError) as error:
        gemini_provider.GeminiAnswerProvider().generate("prompt")

    assert "secret prompt" not in str(error.value)
    assert error.value.category == "provider_http_error"
    assert error.value.provider_status == 403


def test_gemini_connection_error_is_safely_categorized(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")

    def fake_post(*_args, **_kwargs):
        raise httpx.ConnectError("private network detail")

    monkeypatch.setattr(gemini_provider.httpx, "post", fake_post)
    with pytest.raises(gemini_provider.GeminiGenerationError) as error:
        gemini_provider.GeminiAnswerProvider().generate("prompt")

    assert error.value.category == "connection_error"
    assert "private network detail" not in str(error.value)
