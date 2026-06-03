# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_local_operator_files_are_ignored():
    gitignore = _text(".gitignore")
    dockerignore = _text(".dockerignore")

    for ignore_file in (gitignore, dockerignore):
        assert "AGENTS.md" in ignore_file
        assert "deploy/systemd/team-dinner-bot.service" in ignore_file


def test_public_operator_templates_are_neutral():
    agents_example = _text("AGENTS.md.example")
    service_example = _text("deploy/systemd/team-dinner-bot.service.example")

    combined = f"{agents_example}\n{service_example}"
    assert "Team Dinner Bot" in combined
    assert ("e" + "Yami") not in combined
    assert ("ear" + "th") not in combined.lower()
    assert ("jongin" + ".kim") not in combined
    assert ("/home/" + "jongin" + ".kim") not in combined


def test_systemd_example_preserves_hardening_directives():
    service_example = _text("deploy/systemd/team-dinner-bot.service.example")

    for directive in (
        "NoNewPrivileges=true",
        "PrivateTmp=true",
        "ProtectSystem=strict",
        "ProtectHome=read-only",
        "CapabilityBoundingSet=",
        "RestrictSUIDSGID=true",
        "LockPersonality=true",
        "SystemCallArchitectures=native",
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
    ):
        assert directive in service_example
