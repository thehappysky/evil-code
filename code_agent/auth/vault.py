"""Multi-account credential vault.

Ported from cc-switch vault.py, adapted for code_agent package naming.

Storage: ~/.code-agent/vault.json

Format::

    {
      "accounts": {"name": {"token": {...OAuth blob...}, "label": "work"}},
      "active": "name",
      "rotation": ["name1", "name2"]
    }

Security:
    - File permissions: 0600 (user-only read/write)
    - Atomic writes: temp file + os.replace + os.fsync
    - Cross-process file locking

Coexistence with auth/token_store.py:

    vault.json    ← Source of Truth (all accounts)
    .credentials.json  ← Live File (current active token)

    vault is the "database" — stores all accounts' tokens.
    token_store is the "output" — writes current active token to live file.

    switch_to() flow:
        1. Pre-lock: check target token expiry -> refresh if needed
        2. Lock: checkpoint active account (sync live token back to vault)
        3. Lock: apply refreshed token to vault entry
        4. Lock: set active = target name
        5. Lock: save vault
        6. Write live credentials file (via auth/token_store.py)
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from code_agent.storage.file_lock import FileLock

logger = logging.getLogger(__name__)

DEFAULT_VAULT_PATH = Path.home() / ".code-agent" / "vault.json"


class AccountVault:
    """Multi-account credential vault.

    Usage::

        vault = AccountVault()
        vault.add_account("work", token_dict, label="Work Account")
        vault.switch_to("work")
        active = vault.get_active()
    """

    def __init__(self, vault_path: Path | None = None):
        self._path = vault_path or DEFAULT_VAULT_PATH
        self._data: dict[str, Any] = {
            "accounts": {},
            "active": None,
            "rotation": [],
        }

    # ── Read / Write ────────────────────────────────────────────────────────

    def load(self) -> None:
        """Load vault from disk.

        If vault.json doesn't exist, initializes empty vault in memory.
        """
        if not self._path.exists():
            self._data = {"accounts": {}, "active": None, "rotation": []}
            return

        try:
            content = self._path.read_text(encoding="utf-8")
            self._data = json.loads(content)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load vault from %s: %s", self._path, exc)
            self._data = {"accounts": {}, "active": None, "rotation": []}

    def save(self) -> None:
        """Save vault to disk atomically.

        Uses temp file + fsync + os.replace for crash-safety.
        Sets file permissions to 0600 (user-only).
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)

        tmp_path = self._path.with_suffix(".tmp")
        try:
            content = json.dumps(self._data, ensure_ascii=False, indent=2)
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(str(tmp_path), str(self._path))
            # Set restrictive permissions
            try:
                os.chmod(str(self._path), 0o600)
            except OSError:
                pass  # Windows may not support chmod
        except OSError:
            try:
                os.unlink(str(tmp_path))
            except OSError:
                pass
            raise

    # ── Account Management ──────────────────────────────────────────────────

    def add_account(
        self, name: str, token: dict[str, Any], label: str = ""
    ) -> None:
        """Add an account to the vault.

        Currently depends on an existing live token (from .credentials.json
        or manually provided). Growth path: future extensions can implement
        independent OAuth device-flow login here.

        Args:
            name: Account name (e.g., "work", "personal").
            token: OAuth token blob (dict with access_token, refresh_token, etc.).
            label: Human-readable label.
        """
        if not name:
            raise ValueError("Account name cannot be empty")

        self._data["accounts"][name] = {
            "token": token,
            "label": label,
        }

        # Add to rotation if not already present
        if name not in self._data["rotation"]:
            self._data["rotation"].append(name)

        # Set as active if first account
        if self._data["active"] is None:
            self._data["active"] = name

        self.save()
        logger.info("Added account '%s' (label='%s')", name, label)

    def remove_account(self, name: str) -> bool:
        """Remove an account from the vault.

        Args:
            name: Account name to remove.

        Returns:
            True if the account was found and removed.
        """
        if name not in self._data["accounts"]:
            return False

        del self._data["accounts"][name]

        # Remove from rotation
        if name in self._data["rotation"]:
            self._data["rotation"].remove(name)

        # Clear active if it was this account
        if self._data["active"] == name:
            remaining = list(self._data["accounts"].keys())
            self._data["active"] = remaining[0] if remaining else None

        self.save()
        logger.info("Removed account '%s'", name)
        return True

    def list_accounts(self) -> list[dict[str, Any]]:
        """List all accounts with metadata.

        Returns:
            List of dicts with keys: name, label, is_active.
        """
        result: list[dict[str, Any]] = []
        for name, data in self._data["accounts"].items():
            result.append({
                "name": name,
                "label": data.get("label", ""),
                "is_active": name == self._data["active"],
            })
        return result

    def get_active(self) -> dict[str, Any] | None:
        """Get the currently active account's token.

        Returns:
            Token dict, or None if no active account.
        """
        active_name = self._data.get("active")
        if not active_name or active_name not in self._data["accounts"]:
            return None

        return dict(self._data["accounts"][active_name])

    def get_account(self, name: str) -> dict[str, Any] | None:
        """Get a specific account's data.

        Args:
            name: Account name.

        Returns:
            Account data dict, or None if not found.
        """
        if name not in self._data["accounts"]:
            return None
        return dict(self._data["accounts"][name])

    # ── Switching ───────────────────────────────────────────────────────────

    def switch_to(self, name: str) -> dict[str, Any]:
        """Switch active account.

        Steps (matching cc-switch switch_to() exactly):
        1. Pre-lock: check target token expiry -> refresh if needed
           (network call happens OUTSIDE file lock)
        2. Lock: checkpoint active account (sync live token back to vault)
        3. Lock: apply refreshed token to vault entry
        4. Lock: set active = target name
        5. Lock: save vault

        Step 6 (write live credentials file) is the caller's responsibility
        — typically done via auth/token_store.save_token().

        Args:
            name: Account name to switch to.

        Returns:
            Token dict of the newly active account.

        Raises:
            ValueError: If account name doesn't exist.
        """
        if name not in self._data["accounts"]:
            raise ValueError(f"Account '{name}' not found in vault")

        # Step 1: Pre-lock — check token expiry
        target_token = self._data["accounts"][name].get("token", {})
        # Token refresh would happen here if we detect expiry
        # (skipped in Phase 2 — caller handles refresh separately)

        # Steps 2-5: Under file lock
        with FileLock(self._path) as _lock:
            # Step 2: Checkpoint active account
            old_active = self._data.get("active")
            # (in full implementation: sync live token back to vault)

            # Step 3-4: Apply + set active
            self._data["active"] = name

            # Step 5: Save
            self.save()

        logger.info(
            "Switched active account: '%s' -> '%s'",
            old_active, name,
        )
        return dict(self._data["accounts"][name])

    def next_in_rotation(
        self, skip: set[str] | None = None
    ) -> str | None:
        """Find next account in rotation order.

        Starts from active+1, optionally skipping a set of names.
        Used for automatic account rotation to distribute API usage.

        Args:
            skip: Set of account names to skip.

        Returns:
            Name of next account, or None if no valid next.
        """
        rotation = self._data.get("rotation", [])
        if not rotation:
            return None

        active = self._data.get("active")
        skip_set = skip or set()

        # Find starting index (after current active)
        start_idx = 0
        if active and active in rotation:
            start_idx = (rotation.index(active) + 1) % len(rotation)

        # Search for next non-skipped account
        for i in range(len(rotation)):
            idx = (start_idx + i) % len(rotation)
            candidate = rotation[idx]
            if candidate not in skip_set and candidate in self._data["accounts"]:
                return candidate

        return None

    # ── Convenience ─────────────────────────────────────────────────────────

    @property
    def account_count(self) -> int:
        """Number of accounts in the vault."""
        return len(self._data.get("accounts", {}))

    @property
    def active_name(self) -> str | None:
        """Name of the currently active account."""
        return self._data.get("active")
