# Phase 2 Module 6: auth/vault.py — Multi-Account Credential Vault

> Phase: 2 | Status: DONE | Created: 2026-07-25

---

## Scope

| Feature | Description |
|:---|:---|
| AccountVault | Multi-account credential vault with atomic writes, file locking, 0600 permissions |
| add_account | Add account with OAuth token blob + label |
| remove_account | Remove account, clear active if needed |
| list_accounts | List all accounts with name, label, is_active |
| get_active / get_account | Get active account token or specific account data |
| switch_to | Switch active account: pre-lock refresh -> lock -> checkpoint -> apply -> save |
| next_in_rotation | Find next account in rotation order (for auto-distribution) |

---

## Final Architecture

```
code_agent/auth/
├── vault.py             # AccountVault: CRUD + switch_to + rotation (208 lines)
```

---

## Implementation Status

| Item | Status | File |
|:---|:---|:---|
| AccountVault — __init__ with configurable vault_path | Done | `auth/vault.py` |
| load() — read vault.json, init empty on missing | Done | `auth/vault.py` |
| save() — atomic write: temp + fsync + os.replace + chmod 0600 | Done | `auth/vault.py` |
| add_account() — add token + label, auto-set active if first, add to rotation | Done | `auth/vault.py` |
| remove_account() — remove from accounts + rotation, update active | Done | `auth/vault.py` |
| list_accounts() — name, label, is_active for each account | Done | `auth/vault.py` |
| get_active() — return active account token dict or None | Done | `auth/vault.py` |
| get_account() — return specific account or None | Done | `auth/vault.py` |
| switch_to() — FileLock-guarded: set active -> save. Step 6 (live creds) is caller's responsibility | Done | `auth/vault.py` |
| next_in_rotation() — find next non-skipped account from active+1 | Done | `auth/vault.py` |
| account_count / active_name properties | Done | `auth/vault.py` |

**Progress: 11/11 items complete**

---

## Design Decisions

- **switch_to step 6 is caller's responsibility**: Vault manages accounts; caller writes live credentials file (decoupling)
- **Token refresh placeholder**: Full refresh-in-lock logic deferred; Phase 2 uses existing live tokens
- **Rotation supports skip set**: Allows excluding accounts that have API errors
- **Default path**: ~/.code-agent/vault.json (separate from Claude Code's ~/.claude/)
