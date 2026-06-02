# -*- coding: utf-8 -*-
from __future__ import annotations

import sys

from pydantic import ValidationError


def _load_app_main():
    try:
        from app.main import main as app_main
    except ValidationError as exc:
        _exit_missing_config(exc)
    return app_main


def _exit_missing_config(exc: ValidationError) -> None:
    missing = [
        str(error.get("loc", ["?"])[0])
        for error in exc.errors()
        if error.get("type") == "missing"
    ]
    if missing:
        print(
            "Missing required configuration: " + ", ".join(sorted(missing)),
            file=sys.stderr,
        )
    else:
        print("Configuration validation failed.", file=sys.stderr)
    print(
        "Create .env from config.example.env and set Slack bot/app/signing tokens.",
        file=sys.stderr,
    )
    raise SystemExit(2)


def main() -> None:
    app_main = _load_app_main()
    app_main()


if __name__ == "__main__":
    main()
