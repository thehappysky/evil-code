"""`code-agent config` subcommand — manage provider configuration."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from code_agent.auth.config_store import generate_config, load_config, save_config
from code_agent.config.settings_writer import (
    _mask_key,
    clear_settings_config,
    save_settings_config,
    show_settings_config,
)


def run_config_command(args: list[str]) -> None:
    """Handle `code-agent config set/show/clear` subcommands."""
    if not args or args[0] in ("-h", "--help"):
        print("evil-code config -- manage provider configuration\n")
        print("Usage:")
        print("  code-agent config set [--api-key KEY] [--base-url URL] [--model MODEL]")
        print("                           [--auth-scheme bearer|x_api_key]")
        print("                           [--backend auto|chat_completions|messages|responses]")
        print("  code-agent config show")
        print("  code-agent config clear")
        print("")
        print("Only --api-key is required. Base URL defaults to DeepSeek, model to deepseek-v4-pro.")
        print("Omit arguments to enter interactive mode (prompts with defaults shown).")
        print("")
        print("Config files (written on 'set', read on startup):")
        print("  1. ~/.evil-code/config.yaml  (evil-code native, primary)")
        print("  2. ~/.claude/settings.json    (cc-switch compatible, fallback)")
        print("")
        print("Auth resolution order on startup:")
        print("  CLI --api-key  >  config.yaml  >  env vars  >  settings.json  >  OAuth")
        print("")
        print("Examples:")
        print("  code-agent config set --api-key sk-xxx --base-url https://api.deepseek.com/anthropic --model deepseek-v4-pro")
        print("  code-agent config set  # interactive mode")
        print("  code-agent config show")
        print("  code-agent config clear")
        return

    sub = args[0]

    if sub == "show":
        settings_summary = show_settings_config()
        evil_cfg = load_config()

        print("evil-code config files:\n")
        print(f"  Primary: ~/.evil-code/config.yaml")
        if evil_cfg is not None and evil_cfg.is_present():
            print(f"    [configured]")
            print(f"    API key:     {_mask_key(evil_cfg.api_key or '')}")
            print(f"    Base URL:    {evil_cfg.base_url or '(not set)'}")
            print(f"    Model:       {evil_cfg.model or '(not set)'}")
            print(f"    Backend:     {evil_cfg.api_backend or '(auto)'}")
            print(f"    Auth scheme: {evil_cfg.auth_scheme or '(auto)'}")
        else:
            print(f"    [not configured]")
        print("")
        print(f"  Fallback: {settings_summary.settings_path}")
        if settings_summary.is_configured:
            print(f"    [configured]")
            print(f"    API key:     {_mask_key(settings_summary.api_key or '')}")
            print(f"    Base URL:    {settings_summary.base_url or '(not set)'}")
            print(f"    Model:       {settings_summary.model or '(not set)'}")
            auth_scheme = (
                "Bearer (ANTHROPIC_AUTH_TOKEN)"
                if settings_summary.auth_token_present
                else "XApiKey (ANTHROPIC_API_KEY)"
            )
            print(f"    Auth scheme: {auth_scheme}")
        else:
            print(f"    [not configured]")
        return

    if sub == "clear":
        settings_path = clear_settings_config()
        evil_path = Path.home() / ".evil-code" / "config.yaml"
        if evil_path.exists():
            evil_path.unlink()
            print(f"Removed {evil_path}")
        print(f"Cleared auth config from {settings_path}")
        print("  (Other settings.json keys preserved.)")
        return

    if sub == "set":
        parser = argparse.ArgumentParser(
            prog="code-agent config set",
            description="Configure provider settings",
        )
        parser.add_argument("--api-key", default=None, help="API key (e.g. sk-xxx)")
        parser.add_argument("--base-url", default=None, help="API base URL")
        parser.add_argument("--model", default=None, help="Model ID")
        parser.add_argument(
            "--auth-scheme", default="bearer",
            choices=["bearer", "x_api_key"],
            help="Auth scheme",
        )
        parser.add_argument(
            "--backend", default="auto",
            choices=["auto", "chat_completions", "messages", "responses"],
            help="API backend protocol",
        )
        set_args = parser.parse_args(args[1:])

        api_key = set_args.api_key
        base_url = set_args.base_url
        model = set_args.model

        if api_key is None:
            api_key = input("API key (e.g. sk-xxx): ").strip()
        if not api_key:
            print("Error: API key is required.")
            sys.exit(1)

        _DEFAULT_BASE = "https://api.deepseek.com/anthropic"
        _DEFAULT_MODEL = "deepseek-v4-pro"

        if base_url is None:
            base_url = input(f"Base URL [{_DEFAULT_BASE}]: ").strip()
            if not base_url:
                base_url = _DEFAULT_BASE

        if model is None:
            model = input(f"Model [{_DEFAULT_MODEL}]: ").strip()
            if not model:
                model = _DEFAULT_MODEL

        # Write 1: evil-code native config.yaml
        evil_cfg = generate_config(
            api_key=api_key, base_url=base_url, model=model,
            api_backend=set_args.backend, auth_scheme=set_args.auth_scheme,
        )
        save_config(evil_cfg)
        evil_path = Path.home() / ".evil-code" / "config.yaml"

        # Write 2: settings.json (cc-switch compatible)
        use_auth_token = set_args.auth_scheme == "bearer"
        settings_path = save_settings_config(
            api_key=api_key, base_url=base_url, model=model,
            use_auth_token=use_auth_token,
        )

        auth_key = "ANTHROPIC_AUTH_TOKEN" if use_auth_token else "ANTHROPIC_API_KEY"
        print(f"\n[OK] Config saved:")
        print(f"  Primary:  {evil_path}")
        print(f"  Fallback: {settings_path}")
        print(f"  {auth_key}: {_mask_key(api_key)}")
        print(f"  Base URL: {base_url}")
        print(f"  Model: {model}")
        print(f"  Backend: {set_args.backend}")
        print(f"\nNow run: code-agent  (auto-login from config.yaml)")
        return

    print(f"Unknown config subcommand: {sub}")
    print("Use: code-agent config set/show/clear")
    sys.exit(1)
