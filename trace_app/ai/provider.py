"""LLM provider abstraction.

The rest of TRACE talks to :class:`LLMProvider` and knows nothing about any
vendor SDK. Swapping providers means adding one subclass and one line in
``build_provider``; nothing in the pipeline changes.

Failures are translated into a small set of exceptions carrying messages
written for a user to read. Raw vendor errors are logged, never displayed,
because they can contain request details.
"""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod

from config import LLMConfig

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_MAX_OUTPUT_TOKENS = 8192


class LLMError(RuntimeError):
    """Base class for provider failures. The message is safe to display."""


class LLMNotConfigured(LLMError):
    """No API key is available, so no model can be called."""


class LLMUnavailable(LLMError):
    """The provider could not be reached, timed out, or rate-limited."""


class LLMBadResponse(LLMError):
    """The provider replied, but not with usable JSON."""


# --------------------------------------------------------------------------
# JSON recovery
# --------------------------------------------------------------------------

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def extract_json_object(raw: str | None) -> dict:
    """Pull a JSON object out of a model reply.

    Handles the usual deviations — a code fence, a sentence of preamble — but
    does not attempt to repair genuinely broken JSON, because silently
    "fixing" a malformed reply risks inventing content.
    """
    if not raw or not raw.strip():
        raise LLMBadResponse("The model returned an empty response.")

    text = _FENCE.sub("", raw.strip())
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise LLMBadResponse(
                "The model did not return JSON that TRACE could read."
            ) from None
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            raise LLMBadResponse(
                "The model returned malformed JSON that TRACE could not parse."
            ) from None

    if not isinstance(parsed, dict):
        raise LLMBadResponse("The model returned JSON that was not an object.")
    return parsed


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------

class LLMProvider(ABC):
    """Returns a JSON object for a system/user prompt pair."""

    name: str = "provider"
    model: str = ""

    @abstractmethod
    def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        """Call the model and return the parsed JSON object it produced."""


class GeminiProvider(LLMProvider):
    """Google Gemini via the ``google-genai`` SDK."""

    name = "gemini"

    def __init__(self, api_key: str, model: str, base_url: str | None = None) -> None:
        if not api_key:
            raise LLMNotConfigured("No API key was provided for Gemini.")
        self.model = model
        self._api_key = api_key
        self._base_url = base_url
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:  # pragma: no cover - dependency is pinned
            raise LLMError(
                "The Google Gemini client library is not installed."
            ) from exc

        options: dict = {"timeout": DEFAULT_TIMEOUT_SECONDS * 1000}
        if self._base_url:
            options["base_url"] = self._base_url
        try:
            self._client = genai.Client(
                api_key=self._api_key,
                http_options=types.HttpOptions(**options),
            )
        except Exception as exc:
            logger.warning("Gemini client could not be created: %s", exc)
            raise LLMError("The Gemini client could not be initialised.") from exc
        return self._client

    def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        from google.genai import types

        client = self._get_client()
        request_config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            # Deterministic as far as the provider allows, so the same sources
            # tend to give the same reconstruction.
            temperature=0.0,
            max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
        )

        try:
            response = client.models.generate_content(
                model=self.model, contents=user_prompt, config=request_config
            )
        except Exception as exc:
            raise self._translate(exc) from exc

        return extract_json_object(getattr(response, "text", None))

    @staticmethod
    def _translate(exc: Exception) -> LLMError:
        """Map a vendor error to a message a user can act on."""
        logger.warning("Gemini request failed: %s", exc)
        status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        text = str(exc).lower()

        if status in (401, 403) or "api key" in text or "permission" in text:
            return LLMNotConfigured(
                "The Gemini API key was rejected. Check that LLM_API_KEY is "
                "correct and that the key has access to the configured model."
            )
        if status == 404 or "not found" in text:
            return LLMError(
                "The configured Gemini model was not found. Check LLM_MODEL."
            )
        if status == 429 or "quota" in text or "rate limit" in text:
            return LLMUnavailable(
                "The Gemini API rate limit or quota was reached. Wait a moment "
                "and run the reconstruction again."
            )
        if "timeout" in text or "deadline" in text:
            return LLMUnavailable(
                "The Gemini API did not respond in time. Try again, or reduce "
                "the number of sources."
            )
        if status is not None and 500 <= int(status) < 600:
            return LLMUnavailable(
                "The Gemini API reported a server error. Try again shortly."
            )
        return LLMUnavailable(
            "TRACE could not reach the Gemini API. Check the network "
            "connection and the provider configuration."
        )


#: Registry of supported providers.
_PROVIDERS = {"gemini": GeminiProvider}

SUPPORTED_PROVIDERS = tuple(_PROVIDERS)


def build_provider(settings: LLMConfig) -> LLMProvider:
    """Create the configured provider, or explain why it cannot be created."""
    if not settings.is_configured:
        raise LLMNotConfigured(
            "No AI provider is configured. Set LLM_API_KEY to enable AI "
            "reconstruction."
        )
    provider_class = _PROVIDERS.get(settings.provider)
    if provider_class is None:
        supported = ", ".join(SUPPORTED_PROVIDERS)
        raise LLMNotConfigured(
            f"LLM_PROVIDER is set to “{settings.provider}”, which TRACE does "
            f"not support. Supported providers: {supported}."
        )
    return provider_class(
        api_key=settings.api_key or "",
        model=settings.model,
        base_url=settings.base_url,
    )
