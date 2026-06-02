# -*- coding: utf-8 -*-
from __future__ import annotations

import json


def test_provisioning_probe_classifies_current_slack_limit():
    from scripts import run_product_readiness_scenarios as runner

    result = runner.classify_provisioning_probe(
        admin_invite_error="not_allowed_token_type",
        scim_status_code=401,
        scim_body='{"Errors":{"description":"invalid_authentication","code":401}}',
    )

    assert result["can_create_test_users"] is False
    assert "admin.users.invite" in result["reason"]
    assert "SCIM" in result["reason"]


def test_local_product_readiness_covers_four_simulated_voters(tmp_path):
    from scripts import run_product_readiness_scenarios as runner

    result = runner.run_local_multi_user_scenarios(tmp_path / "product.db")

    assert result["ok"] is True
    assert result["voter_count"] == 4
    assert result["checks"]["poll_targets_four_humans"] == "passed"
    assert result["checks"]["target_snapshot_four_humans"] == "passed"
    assert result["checks"]["four_users_vote_and_tally"] == "passed"
    assert result["checks"]["toggle_removes_one_vote"] == "passed"
    assert result["checks"]["duplicate_poll_guard"] == "passed"
    assert result["checks"]["invalid_vote_rejected"] == "passed"
    assert result["checks"]["cancel_active_run"] == "passed"
    assert result["checks"]["no_vote_close"] == "passed"
    assert result["checks"]["booking_assignee_random_candidate"] == "passed"
    assert result["checks"]["booking_avoids_previous_assignee"] == "passed"


def test_product_readiness_catalog_covers_operational_risks():
    from scripts import run_product_readiness_scenarios as runner

    scenario_ids = {item["id"] for item in runner.build_scenario_catalog()}

    assert {
        "slack-provisioning-permission",
        "multi-user-vote-tally",
        "vote-toggle",
        "duplicate-run",
        "cancel-active-run",
        "no-vote-close",
        "invalid-stale-vote",
        "random-booking-assignee",
        "browser-session-preserved",
    }.issubset(scenario_ids)


def test_product_readiness_main_is_non_live_by_default(monkeypatch, capsys, tmp_path):
    from scripts import run_product_readiness_scenarios as runner

    monkeypatch.setattr(
        runner,
        "run_local_multi_user_scenarios",
        lambda _db_path: {"ok": True, "checks": {}, "voter_count": 4},
    )

    def fail_if_live_probe_runs():
        raise AssertionError("live Slack provisioning probe must be opt-in")

    monkeypatch.setattr(runner, "probe_slack_user_provisioning", fail_if_live_probe_runs)

    assert runner.main([]) == 0
    report = json.loads(capsys.readouterr().out)
    assert "slack_provisioning_probe" not in report


def test_product_readiness_live_probe_redacts_workspace_identifiers(monkeypatch, capsys):
    from scripts import run_product_readiness_scenarios as runner

    monkeypatch.setattr(
        runner,
        "run_local_multi_user_scenarios",
        lambda _db_path: {"ok": True, "checks": {}, "voter_count": 4},
    )
    monkeypatch.setattr(
        runner,
        "probe_slack_user_provisioning",
        lambda: {
            "can_create_test_users": False,
            "reason": "probe blocked",
            "workspace": {
                "team": "Private Team",
                "team_id": "T1234567890",
                "bot_user_id": "U1234567890",
                "bot_id": "B1234567890",
            },
        },
    )

    assert runner.main(["--live-probe"]) == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    probe = report["slack_provisioning_probe"]
    assert probe["can_create_test_users"] is False
    assert "workspace" not in probe
    assert "T1234567890" not in output


def test_public_probe_report_is_allowlisted_by_default():
    from scripts import run_product_readiness_scenarios as runner

    report = runner._public_probe_report(
        {
            "can_create_test_users": False,
            "admin_users_invite_error": "not_allowed_token_type",
            "scim_status_code": 401,
            "reason": "probe blocked",
            "workspace": {"team_id": "T1234567890"},
            "unexpected_team_id": "T9999999999",
            "raw_response": {"bot_user_id": "U1234567890"},
        }
    )

    assert report == {
        "can_create_test_users": False,
        "admin_users_invite_error": "not_allowed_token_type",
        "scim_status_code": 401,
        "reason": "probe blocked",
    }
