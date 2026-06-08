# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_DOCS = (
    "README.md",
    "SETUP.md",
    "docs/PUBLISH_CHECKLIST.md",
    "docs/SLACK_EVENT_SETUP.md",
    "docs/SLASH_COMMAND_SETUP.md",
    "docs/TEST_RESULTS.md",
    "docs/TEST_SCENARIOS.md",
    "docs/TEST_PLAN_FULL.md",
    "assets/README.md",
)
PUBLIC_RISK_PATTERNS = (
    "e" + "Yami",
    "eo" + "yami",
    "ey" + "ami",
    "A0" + "B50P",
    "C0" + "4S",
    "T0" + "32",
    "U0" + "31",
    "U0" + "32",
    "김" + "보근",
    "김" + "종인",
    "송" + "인걸",
    "유" + "도곤",
    "jongin" + ".kim",
    "epa" + "pyrus",
    "C:" + "\\workspace",
)
PUBLIC_SCRIPT_FILES = (
    "scripts/run_scenario_tests.py",
    "scripts/seed_test_channel_schedule.py",
    "scripts/run_product_readiness_scenarios.py",
)
SLACK_ID_SHAPED_LITERAL = re.compile(r"\b[CTUW][A-Z0-9]{8,}\b")
ALLOWED_PUBLIC_SLACK_ID_LITERALS = {
    "USLACKBOT",
}


def test_public_docs_are_neutralized():
    for rel in PUBLIC_DOCS:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for pattern in PUBLIC_RISK_PATTERNS:
            assert pattern not in text, f"{pattern!r} remains in {rel}"


def test_slack_manifests_use_public_default_names():
    for rel in ("slack-app-manifest.json", "slack-app-manifest.ascii.json"):
        data = json.loads((ROOT / rel).read_text(encoding="utf-8"))

        assert data["display_information"]["name"] == "Team Dinner Bot"
        assert data["features"]["bot_user"]["display_name"] == "Team Dinner Bot"


def test_public_asset_filenames_are_neutral():
    public_asset_names = [path.name for path in (ROOT / "assets").iterdir()]

    assert public_asset_names
    for name in public_asset_names:
        assert ("ey" + "ami") not in name.lower()


def test_public_scripts_do_not_ship_slack_id_shaped_literals():
    for rel in PUBLIC_SCRIPT_FILES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        matches = {
            match.group(0)
            for match in SLACK_ID_SHAPED_LITERAL.finditer(text)
            if match.group(0) not in ALLOWED_PUBLIC_SLACK_ID_LITERALS
        }
        assert not matches, f"{rel} contains Slack ID-shaped literals: {sorted(matches)}"
