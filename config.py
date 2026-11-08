"""Central configuration for TRACE.

Secrets are read from the process environment first, then from Streamlit's
secrets manager (so the same code works locally with a ``.env`` file and on
Streamlit Community Cloud with its Secrets UI).

Nothing in this module ever logs or echoes a secret value.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

# Load a local .env if present. We parse it by hand rather than add a
# python-dotenv dependency for six lines of work.
_ENV_FILE = PROJECT_ROOT / ".env"


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            # Real environment variables win over the file.
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        # A malformed or unreadable .env must not stop the app from starting.
        pass


_load_env_file(_ENV_FILE)


def _from_streamlit_secrets(name: str) -> str | None:
    """Read ``name`` from st.secrets, tolerating every failure mode.

    Accessing st.secrets raises if no secrets file exists, and importing
    streamlit outside a Streamlit runtime is also possible but noisy, so this
    is defensive on purpose.
    """
    try:
        import streamlit as st

        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        return None
    return None


def get_setting(name: str, default: str | None = None) -> str | None:
    """Resolve a setting from the environment, then Streamlit secrets."""
    value = os.environ.get(name)
    if value not in (None, ""):
        return value
    value = _from_streamlit_secrets(name)
    if value not in (None, ""):
        return value
    return default


def _get_int_setting(name: str, default: int) -> int:
    raw = get_setting(name)
    if raw is None:
        return default
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

DB_PATH = Path(get_setting("TRACE_DB_PATH") or (PROJECT_ROOT / "data" / "trace.db"))
UPLOAD_DIR = Path(get_setting("TRACE_UPLOAD_DIR") or (PROJECT_ROOT / "data" / "uploads"))

#: Per-file upload ceiling. Documented in the README and enforced in
#: ``trace_app.utils.file_validation``; ``.streamlit/config.toml`` carries a
#: matching ``maxUploadSize`` so Streamlit rejects oversized files early too.
MAX_UPLOAD_MB = _get_int_setting("TRACE_MAX_UPLOAD_MB", 10)
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

ALLOWED_EXTENSIONS = ("pdf", "docx", "txt")


def ensure_directories() -> None:
    """Create the storage directories if they do not exist yet."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------
# LLM provider
# --------------------------------------------------------------------------

DEFAULT_LLM_PROVIDER = "gemini"
#: Verified working end to end against the live API. Google retires models,
#: so this is overridable with LLM_MODEL and the 404 handler says as much.
DEFAULT_LLM_MODEL = "gemini-3.6-flash"


@dataclass(frozen=True)
class LLMConfig:
    """Resolved LLM settings. ``api_key`` is never rendered or logged."""

    provider: str
    model: str
    api_key: str | None
    base_url: str | None

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def describe(self) -> str:
        """A safe, human-readable summary that excludes the key itself."""
        state = "configured" if self.is_configured else "no API key"
        return f"{self.provider} / {self.model} ({state})"


def get_llm_config() -> LLMConfig:
    """Resolve LLM configuration from the environment or Streamlit secrets."""
    return LLMConfig(
        provider=(get_setting("LLM_PROVIDER") or DEFAULT_LLM_PROVIDER).strip().lower(),
        model=(get_setting("LLM_MODEL") or DEFAULT_LLM_MODEL).strip(),
        api_key=get_setting("LLM_API_KEY"),
        base_url=get_setting("LLM_BASE_URL"),
    )


# --------------------------------------------------------------------------
# Product copy used in more than one screen
# --------------------------------------------------------------------------

APP_NAME = "TRACE"
APP_TAGLINE = "Project Knowledge and Handover System"
APP_DESCRIPTION = (
    "TRACE turns scattered project records into a source-backed, reviewable "
    "handover so the next person can continue work without guessing."
)
AI_DISCLAIMER = (
    "AI-generated handover items are drafts and must be reviewed. Evidence "
    "links support verification but do not guarantee that an interpretation "
    "is correct."
)
UPLOAD_PRIVACY_WARNING = (
    "Do not upload confidential material to a public demo. Use synthetic, "
    "public, anonymised, or explicitly permissioned documents only."
)
