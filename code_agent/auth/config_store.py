"""evil-code native config file read/write/generate.

This is the primary configuration path for evil-code itself
(not backward-compatible with Claude Code's .credentials.json or
cc-switch's settings.json).

Config file location: ~/.evil-code/config.yaml
Format: YAML (human-readable, comment-friendly)

Usage:
    from code_agent.auth.config_store import load_config, save_config, generate_config

    config = generate_config(
        api_key="sk-...",
        base_url="https://api.deepseek.com/anthropic",
        model="deepseek-v4-pro",
    )
    save_config(config)  # writes ~/.evil-code/config.yaml

    config = load_config()  # reads it back
    if config:
        print(config.api_key)

Pure function module — depends only on stdlib + PyYAML (optional: falls back to JSON).
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from code_agent.api.schemas import ApiBackend, AuthScheme

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path.home() / ".evil-code"
_CONFIG_FILE = _CONFIG_DIR / "config.yaml"


# ─── Config Data Model ───────────────────────────────────────────────────────────


@dataclass
class EvilCodeConfig:
    """Complete evil-code configuration.

    Stored as YAML in ~/.evil-code/config.yaml.
    All fields are optional — only set what you need.

    Attributes:
        api_backend: LLM backend protocol (chat_completions/messages/responses).
        base_url: API base URL.
        api_key: API key or OAuth access token.
        auth_scheme: Auth header scheme (bearer/x_api_key).
        model: Model ID.
        max_tokens: Default max output tokens.
        temperature: Default temperature.
    """

    api_backend: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    auth_scheme: str | None = None
    model: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None

    def is_present(self) -> bool:
        """True if at least api_key is configured."""
        return self.api_key is not None and self.api_key != ""


# ─── YAML Support (with JSON fallback) ────────────────────────────────────────────


def _atomic_replace(src: str, dst: str) -> None:
    """Atomically replace *dst* with *src*, with retries for Windows file locks.

    On Windows, ``os.replace`` can fail with ``PermissionError`` (errno 13,
    WinError 32) when the target file is briefly locked by antivirus, OneDrive,
    or another process.  This helper retries with exponential backoff, then
    falls back to delete + rename if replace keeps failing.
    """
    if os.name != "nt":
        os.replace(src, dst)
        return

    # ── Windows path ──────────────────────────────────────────────────────────
    max_attempts = 5
    base_delay = 0.05  # 50 ms

    for attempt in range(1, max_attempts + 1):
        try:
            os.replace(src, dst)
            return
        except PermissionError as exc:
            if attempt == max_attempts:
                # Last resort: try unlink + rename (still may fail, but gives
                # antivirus another cycle to release the handle).
                logger.warning(
                    "os.replace failed after %d attempts (%s); trying delete+rename",
                    max_attempts, exc,
                )
                try:
                    os.unlink(dst)
                    os.rename(src, dst)
                    return
                except OSError:
                    pass
                raise
            delay = base_delay * (2 ** (attempt - 1))
            logger.debug(
                "os.replace attempt %d/%d failed (%s); retrying in %.2fs",
                attempt, max_attempts, exc, delay,
            )
            time.sleep(delay)


def _load_yaml(path: Path) -> dict:
    """Load YAML file, falling back to JSON if PyYAML isn't installed."""
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        logger.debug("PyYAML not installed, trying JSON fallback for %s", path)
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except yaml.YAMLError:
        # Malformed YAML must degrade to "no config", not crash the CLI.
        logger.warning("Malformed YAML in %s; treating as empty", path)
        return {}


def _save_yaml(path: Path, data: dict) -> None:
    """Save dict as YAML, with JSON fallback."""
    try:
        import yaml  # type: ignore[import-untyped]

        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    except ImportError:
        logger.debug("PyYAML not installed, using JSON fallback for %s", path)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")


# ─── Public API ──────────────────────────────────────────────────────────────────


def load_config(config_path: Path | None = None) -> EvilCodeConfig | None:
    """Read evil-code config from ~/.evil-code/config.yaml.

    Args:
        config_path: Custom config file path (for testing).
                     Default: ~/.evil-code/config.yaml

    Returns:
        EvilCodeConfig if file exists and contains valid data.
        None if file doesn't exist or is unreadable.
    """
    path = config_path or _CONFIG_FILE

    try:
        raw = _load_yaml(path)
    except (OSError, json.JSONDecodeError):
        return None

    # A top-level YAML that is not a mapping (list, scalar) has no provider block.
    if not isinstance(raw, dict):
        return None

    provider = raw.get("provider", {})
    if not isinstance(provider, dict):
        return None

    config = EvilCodeConfig(
        api_backend=provider.get("api_backend"),
        base_url=provider.get("base_url"),
        api_key=provider.get("api_key"),
        auth_scheme=provider.get("auth_scheme"),
        model=provider.get("model"),
        max_tokens=provider.get("max_tokens"),
        temperature=provider.get("temperature"),
    )

    if not config.is_present():
        return None

    return config


def save_config(config: EvilCodeConfig, config_path: Path | None = None) -> None:
    """Atomically write evil-code config file.

    Creates ~/.evil-code/ directory if it doesn't exist.
    Uses atomic write: temp file in same directory -> os.replace.

    Args:
        config: EvilCodeConfig to persist.
        config_path: Custom path (for testing).
    """
    path = config_path or _CONFIG_FILE

    # Build provider block
    provider: dict[str, str | int | float | None] = {}
    if config.api_backend:
        provider["api_backend"] = config.api_backend
    if config.base_url:
        provider["base_url"] = config.base_url
    if config.api_key:
        provider["api_key"] = config.api_key
    if config.auth_scheme:
        provider["auth_scheme"] = config.auth_scheme
    if config.model:
        provider["model"] = config.model
    if config.max_tokens is not None:
        provider["max_tokens"] = config.max_tokens
    if config.temperature is not None:
        provider["temperature"] = config.temperature

    data = {"provider": provider}

    # Ensure directory exists
    path.parent.mkdir(parents=True, exist_ok=True)

    # Atomic write
    import tempfile

    fd, tmp_path_str = tempfile.mkstemp(dir=str(path.parent), prefix=".config_", suffix=".tmp")
    os.close(fd)  # Close the fd from mkstemp — we only need the path
    try:
        tmp_path = Path(tmp_path_str)
        _save_yaml(tmp_path, data)

        # On Windows, os.replace can fail with PermissionError when the target
        # file is briefly locked by antivirus, cloud sync, or a zombie process.
        # Retry with backoff before giving up.
        target_str = str(path)
        _atomic_replace(tmp_path_str, target_str)
    except BaseException:
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        raise

    # Restrict permissions (owner read/write only)
    try:
        os.chmod(str(path), 0o600)
    except OSError:
        pass


def generate_config(
    *,
    api_backend: str | ApiBackend = "auto",
    base_url: str = "https://api.deepseek.com/anthropic",
    api_key: str,
    auth_scheme: str | AuthScheme = "auto",
    model: str = "deepseek-v4-pro",
    max_tokens: int = 4096,
    temperature: float = 0.7,
) -> EvilCodeConfig:
    """Generate a EvilCodeConfig from explicit parameters.

    This is the "config generator" — callers (CLI, setup wizard) pass
    explicit values and get back a validated config ready to save.

    Args:
        api_backend: "auto" | "chat_completions" | "messages" | "responses".
                     "auto" means infer from base_url.
        base_url: API base URL.
        api_key: API key (required).
        auth_scheme: "auto" | "bearer" | "x_api_key".
                     "auto" means infer from base_url.
        model: Model ID.
        max_tokens: Max output tokens per request.
        temperature: Sampling temperature.

    Returns:
        EvilCodeConfig ready for save_config().
    """
    # Resolve "auto" values
    if api_backend == "auto":
        lower = base_url.lower()
        if "anthropic" in lower or "deepseek" in lower:
            api_backend = "messages"
        else:
            api_backend = "chat_completions"

    if auth_scheme == "auto":
        lower = base_url.lower()
        if "api.anthropic.com" in lower:
            auth_scheme = "x_api_key"
        else:
            auth_scheme = "bearer"

    return EvilCodeConfig(
        # Plain Enum.__str__ is "ApiBackend.CHAT_COMPLETIONS"; use .value so the
        # persisted backend name matches the wire format.
        api_backend=api_backend.value if isinstance(api_backend, ApiBackend) else api_backend,
        base_url=base_url,
        api_key=api_key,
        auth_scheme=auth_scheme.value if isinstance(auth_scheme, AuthScheme) else auth_scheme,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
    )
