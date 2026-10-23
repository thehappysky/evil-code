"""Tests for auth/vault.py — AccountVault."""

from pathlib import Path

import pytest

from code_agent.auth.vault import AccountVault


@pytest.fixture
def vault(tmp_path):
    v = AccountVault(vault_path=tmp_path / "vault.json")
    return v


@pytest.fixture
def sample_token():
    return {
        "accessToken": "sk-test-12345",
        "refreshToken": "sk-refresh-67890",
        "expiresAt": 9999999999999,
    }


class TestAccountVault:
    def test_add_account(self, vault, sample_token):
        vault.add_account("work", sample_token, label="Work Account")
        assert vault.account_count == 1
        assert vault.active_name == "work"

    def test_add_multiple_accounts(self, vault, sample_token):
        vault.add_account("work", sample_token, label="Work")
        vault.add_account("personal", sample_token, label="Personal")
        assert vault.account_count == 2
        # First added is active
        assert vault.active_name == "work"

    def test_add_duplicate_name(self, vault, sample_token):
        vault.add_account("work", sample_token, label="Work")
        vault.add_account("work", sample_token, label="Work Updated")
        assert vault.account_count == 1
        assert vault.list_accounts()[0]["label"] == "Work Updated"

    def test_add_empty_name_raises(self, vault, sample_token):
        with pytest.raises(ValueError):
            vault.add_account("", sample_token)

    def test_remove_account(self, vault, sample_token):
        vault.add_account("work", sample_token)
        assert vault.remove_account("work")
        assert vault.account_count == 0
        assert vault.active_name is None

    def test_remove_nonexistent(self, vault):
        assert not vault.remove_account("nonexistent")

    def test_remove_active_updates_active(self, vault, sample_token):
        vault.add_account("work", sample_token)
        vault.add_account("personal", sample_token)
        vault.remove_account("work")  # work was active
        assert vault.active_name == "personal"

    def test_list_accounts(self, vault, sample_token):
        vault.add_account("work", sample_token, label="Work")
        accounts = vault.list_accounts()
        assert len(accounts) == 1
        assert accounts[0]["name"] == "work"
        assert accounts[0]["label"] == "Work"
        assert accounts[0]["is_active"]

    def test_get_active(self, vault, sample_token):
        vault.add_account("work", sample_token)
        active = vault.get_active()
        assert active is not None
        assert "token" in active
        assert active["token"] == sample_token

    def test_get_active_none(self, vault):
        assert vault.get_active() is None

    def test_get_account(self, vault, sample_token):
        vault.add_account("work", sample_token)
        acc = vault.get_account("work")
        assert acc is not None
        assert acc["token"] == sample_token

    def test_get_account_nonexistent(self, vault):
        assert vault.get_account("nonexistent") is None

    def test_switch_to(self, vault, sample_token):
        vault.add_account("work", sample_token, label="Work")
        vault.add_account("personal", sample_token, label="Personal")

        result = vault.switch_to("personal")
        assert vault.active_name == "personal"
        assert "token" in result

    def test_switch_to_nonexistent(self, vault):
        with pytest.raises(ValueError, match="not found"):
            vault.switch_to("nonexistent")

    def test_next_in_rotation(self, vault, sample_token):
        vault.add_account("a", sample_token)
        vault.add_account("b", sample_token)
        vault.add_account("c", sample_token)

        next_acc = vault.next_in_rotation()
        assert next_acc == "b"  # After active "a"

    def test_next_in_rotation_with_skip(self, vault, sample_token):
        vault.add_account("a", sample_token)
        vault.add_account("b", sample_token)
        vault.add_account("c", sample_token)

        next_acc = vault.next_in_rotation(skip={"b"})
        assert next_acc == "c"  # Skip b

    def test_next_in_rotation_wraps(self, vault, sample_token):
        vault.add_account("a", sample_token)
        vault.add_account("b", sample_token)

        vault.switch_to("b")
        next_acc = vault.next_in_rotation()
        assert next_acc == "a"  # Wraps around

    def test_next_in_rotation_empty(self, vault):
        assert vault.next_in_rotation() is None

    def test_save_and_load(self, vault, sample_token, tmp_path):
        """Vault should persist and reload correctly."""
        vault.add_account("work", sample_token, label="Work")
        vault.add_account("personal", sample_token, label="Personal")

        # Create a new vault pointing to same file
        vault2 = AccountVault(vault_path=tmp_path / "vault.json")
        vault2.load()
        assert vault2.account_count == 2
        assert vault2.active_name == "work"

    def test_account_count_property(self, vault, sample_token):
        assert vault.account_count == 0
        vault.add_account("a", sample_token)
        assert vault.account_count == 1
