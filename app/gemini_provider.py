"""Send only prepared, authorized prompts to the Gemini API."""

import os
import re

import httpx


DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"


class GeminiConfigurationError(Exception):
    """The local Gemini setup is incomplete."""


class GeminiGenerationError(Exception):
    """Gemini could not return a usable answer."""

    def __init__(self, category: str, provider_status: int | None = None) -> None:
        self.category = category
        self.provider_status = provider_status
        super().__init__(category)


class GeminiAnswerProvider:
    def generate(self, prompt: str) -> str:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise GeminiConfigurationError("GEMINI_API_KEY is not configured.")

        model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
        if not re.fullmatch(r"[A-Za-z0-9._-]+", model):
            raise GeminiConfigurationError("GEMINI_MODEL is invalid.")

        try:
            response = httpx.post(
                "https://generativelanguage.googleapis.com/v1beta/interactions",
                headers={"x-goog-api-key": api_key},
                json={"model": model, "store": False, "input": prompt},
                timeout=60.0,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("status") != "completed":
                raise GeminiGenerationError("incomplete_response")
            answer = "\n".join(
                part["text"]
                for step in payload["steps"]
                if step.get("type") == "model_output"
                for part in step.get("content", [])
                if part.get("type") == "text" and isinstance(part.get("text"), str)
            )
        except httpx.HTTPStatusError as exc:
            raise GeminiGenerationError(
                "provider_http_error", exc.response.status_code
            ) from exc
        except httpx.RequestError as exc:
            raise GeminiGenerationError("connection_error") from exc
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            raise GeminiGenerationError("invalid_response") from exc

        if not answer or not answer.strip():
            raise GeminiGenerationError("empty_response")
        return answer.strip()
