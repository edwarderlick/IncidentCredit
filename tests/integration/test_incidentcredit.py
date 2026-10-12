"""Studio Next live paths.

gltest 0.29.2 signs custom studio_next RPCs as studionet chain 61999, which
Studio Next (61997) rejects with InvalidChainId. These tests drive the same
qualifying / excluded / timeout flows through genlayer-js 2.0, which is the
client that successfully estimated fees and deployed on chain 61997.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUNNER = Path(__file__).resolve().parent / "run_live.mjs"
NODE = "node"


def _run(scenario: str) -> dict:
    proc = subprocess.run(
        [NODE, str(RUNNER), scenario],
        cwd=str(ROOT),
        capture_output=False,
        text=True,
        check=False,
    )
    results_path = Path(__file__).resolve().parent / "live-results.json"
    payload = {}
    if results_path.exists():
        payload = json.loads(results_path.read_text(encoding="utf-8"))
    if proc.returncode != 0:
        pytest.fail(
            f"live {scenario} failed (exit {proc.returncode}): "
            f"{payload.get(scenario, payload)}"
        )
    return payload[scenario]


@pytest.mark.slow
def test_qualifying_or_honest_classification():
    result = _run("qualifying")
    assert result["ok"] is True
    assert result["incident"]["consequence"] in {
        "QUALIFYING",
        "scheduled_maintenance",
        "force_majeure",
        "customer_caused",
    }
    assert result["ledger"]["conservation_ok"] is True
    if result["incident"]["status"] == "QUALIFYING":
        assert int(result["ledger"]["withdrawn"]) == 0
        assert int(result["ledger"]["credited"]) == int(result["incident"]["credit_wei"])
        after = result["ledgerAfterWithdraw"]
        assert int(after["credited"]) == 0
        assert int(after["withdrawn"]) == int(result["incident"]["credit_wei"])


@pytest.mark.slow
def test_excluded_or_honest_classification():
    result = _run("excluded")
    assert result["ok"] is True
    assert result["incident"]["consequence"] in {
        "QUALIFYING",
        "scheduled_maintenance",
        "force_majeure",
        "customer_caused",
    }
    assert result["ledger"]["conservation_ok"] is True


@pytest.mark.slow
def test_timeout_credits_customer_or_bounty():
    result = _run("timeout")
    assert result["ok"] is True
    assert result["incident"]["status"] == "TIMEOUT"
    assert result["incident"]["consequence"] == "TIMEOUT_CREDIT"
    assert result["incident"]["payout_marker"] == "CONSUMED"
    assert int(result["ledger"]["reserved"]) == 0
    assert result["ledger"]["conservation_ok"] is True
    assert int(result["incident"]["credit_wei"]) > 0
    assert int(result["ledger"]["credited"]) > 0


@pytest.mark.slow
def test_sunset_blocks_late_record_and_credits_recover_until_withdraw():
    result = _run("sunset")
    assert result["ok"] is True
    funded = int(result["ledger"]["funded"])
    assert funded > 0
    assert int(result["ledger"]["credited"]) == funded
    assert int(result["ledger"]["returned"]) == 0
    after = result["ledgerAfterWithdraw"]
    assert int(after["returned"]) == funded
    assert int(after["credited"]) == 0
    assert after["conservation_ok"] is True
