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
import time
from abc import ABC, abstractmethod

from config import LLMConfig

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 120

#: Ceiling on the reply. The model allows far more, but a longer reply is
#: slower and more exposed to the gateway timeouts this API produces under
#: load. 8,192 was too tight: relationships are emitted last, after every
#: item and its evidence, so a long findings list could push them past the
#: limit and they would simply be absent with no indication why.
DEFAULT_MAX_OUTPUT_TOKENS = 16384

#: Gemini 3.x models reason before answering by default. For this task that is
#: counterproductive and measurably so: reconstruction is structured
#: extraction with exact quoting, not open reasoning, and the thinking tokens
#: come out of the same output budget. Measured on the CampusConnect demo,
#: thinking enabled returned a truncated 2.4 KB reply in 27.8 s, while
#: thinking disabled returned a complete 12.5 KB reply in 11.3 s. The slower
#: path also ran close enough to the upstream gateway timeout to fail
#: intermittently. Older models reject the setting, so it degrades gracefully.
DISABLE_MODEL_THINKING = True

#: Transient provider failures (502, 503, 429) are common enough on shared
#: capacity that a single one should not sink a demo. Only ``LLMUnavailable``
#: is retried -- a bad key, a retired model, or an unparseable reply are all
#: permanent, and retrying them would just waste the user's time.
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = (2.0, 5.0)

#: Models to fall back to when the configured one stays unavailable. Google's
#: shared capacity returns 502/503 often enough that a single saturated model
#: would otherwise fail a live demo outright -- observed in 2 of 4 runs during
#: development. Each of these was verified to answer this workload. The model
#: that actually produced a draft is recorded on the reconstruction, so a
#: report never names a model that did not write it.
# Only models verified to answer this workload through the SDK's API version.
# "gemini-flash-latest" was in this list and removed: the alias resolves for
# the REST endpoint but returns 404 "not found for API version v1" through the
# client, so it wasted a step in the chain every time the primary was down.
DEFAULT_FALLBACK_MODELS = (
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
)


class LLMError(RuntimeError):
    """Base class for provider failures. The message is safe to display."""


class LLMNotConfigured(LLMError):
    """No API key is available, so no model can be called."""


class LLMUnavailable(LLMError):
    """The provider could not be reached, timed out, or rate-limited."""


class LLMBadResponse(LLMError):
    """The provider replied, but not with usable JSON."""


class LLMTruncated(LLMBadResponse):
    """The reply hit the output ceiling and was cut off mid-answer.

    Distinct from unparseable output: the model was working correctly and
    simply ran out of room, so whatever came last in the schema is missing.
    Worth saying out loud rather than presenting a partial answer as whole.
    """


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

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
        fallback_models: tuple[str, ...] | None = None,
    ) -> None:
        if not api_key:
            raise LLMNotConfigured("No API key was provided for Gemini.")
        self.model = model
        #: The model that last produced a reply, which may be a fallback.
        self.model_used: str | None = None
        #: Notes about any fallback, surfaced to the user with the draft.
        self.notes: list[str] = []
        self._fallbacks = (
            DEFAULT_FALLBACK_MODELS if fallback_models is None else fallback_models
        )
        self._api_key = api_key
        self._base_url = base_url
        self._client = None

    def _candidate_models(self) -> list[str]:
        """The configured model first, then distinct fallbacks."""
        ordered = [self.model]
        for name in self._fallbacks:
            if name and name not in ordered:
                ordered.append(name)
        return ordered

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

    def _build_config(self, system_prompt: str, disable_thinking: bool):
        from google.genai import types

        options: dict = {
            "system_instruction": system_prompt,
            "response_mime_type": "application/json",
            # Deterministic as far as the provider allows, so the same sources
            # tend to give the same reconstruction.
            "temperature": 0.0,
            "max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
        }
        if disable_thinking:
            options["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        return types.GenerateContentConfig(**options)

    @staticmethod
    def _is_thinking_rejection(exc: Exception) -> bool:
        """Whether this model simply does not accept the thinking setting."""
        text = str(exc).lower()
        return "thinking" in text and (
            "not supported" in text
            or "unsupported" in text
            or "invalid" in text
            or "unknown" in text
        )

    def _call(self, model: str, user_prompt: str, config):
        return self._get_client().models.generate_content(
            model=model, contents=user_prompt, config=config
        )

    def _generate_with_model(
        self, model: str, system_prompt: str, user_prompt: str
    ) -> dict:
        """One model, with bounded retries for transient failures only."""
        config = self._build_config(system_prompt, DISABLE_MODEL_THINKING)
        last_error: LLMError | None = None

        for attempt in range(MAX_ATTEMPTS):
            try:
                response = self._call(model, user_prompt, config)
            except Exception as exc:
                if DISABLE_MODEL_THINKING and self._is_thinking_rejection(exc):
                    # This model predates the setting; drop it and try again.
                    logger.info(
                        "Model %s rejected thinking_config; retrying without it", model
                    )
                    config = self._build_config(system_prompt, False)
                    continue

                error = self._translate(exc)
                if not isinstance(error, LLMUnavailable) or attempt == MAX_ATTEMPTS - 1:
                    raise error from exc

                delay = RETRY_BACKOFF_SECONDS[
                    min(attempt, len(RETRY_BACKOFF_SECONDS) - 1)
                ]
                logger.info(
                    "%s unavailable (attempt %s/%s); retrying in %ss",
                    model,
                    attempt + 1,
                    MAX_ATTEMPTS,
                    delay,
                )
                last_error = error
                time.sleep(delay)
                continue

            self._check_not_truncated(response)
            return extract_json_object(getattr(response, "text", None))

        raise last_error or LLMUnavailable("The provider could not be reached.")

    @staticmethod
    def _check_not_truncated(response) -> None:
        """Raise if the model stopped because it ran out of output room.

        Without this the reply simply ends early and the missing findings look
        like findings that were never there.
        """
        try:
            reason = str(getattr(response.candidates[0], "finish_reason", "") or "")
        except (AttributeError, IndexError):
            return
        if "MAX_TOKENS" in reason.upper():
            raise LLMTruncated(
                "The model ran out of room before finishing its answer, so "
                "part of it is missing. Reduce the number of sources, or "
                "raise the output limit."
            )

    def generate_json(self, system_prompt: str, user_prompt: str) -> dict:
        """Try the configured model, then fall back if it stays unavailable.

        Only availability failures move on to the next model. A rejected key,
        a malformed reply, or a schema failure are reported immediately,
        because trying a different model would not fix them.
        """
        candidates = self._candidate_models()
        last_error: LLMError | None = None

        for position, model in enumerate(candidates):
            try:
                payload = self._generate_with_model(model, system_prompt, user_prompt)
            except LLMUnavailable as exc:
                last_error = exc
                logger.warning("Model %s unavailable; trying the next one", model)
                continue
            except LLMError as exc:
                # Permanent for this model. A retired model is worth skipping;
                # anything else (bad key, unparseable reply) is not.
                if position < len(candidates) - 1 and "not available" in str(exc):
                    last_error = exc
                    continue
                raise

            self.model_used = model
            if position > 0:
                note = (
                    f"The configured model ({self.model}) was unavailable, so "
                    f"this draft was produced by {model} instead."
                )
                if note not in self.notes:
                    self.notes.append(note)
                logger.info("Fell back from %s to %s", self.model, model)
            return payload

        raise last_error or LLMUnavailable(
            "No configured Gemini model could be reached. Try again shortly."
        )

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
        if status == 404 or "not found" in text or "no longer available" in text:
            return LLMError(
                "The configured Gemini model is not available to this API key. "
                "Google retires models over time, so set LLM_MODEL to a current "
                "one. The error from Google was: "
                + str(exc)[:300]
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


def fallback_models_from_setting(raw: str | None) -> tuple[str, ...] | None:
    """Parse a comma-separated LLM_FALLBACK_MODELS value."""
    if raw is None:
        return None
    names = tuple(name.strip() for name in str(raw).split(",") if name.strip())
    return names
