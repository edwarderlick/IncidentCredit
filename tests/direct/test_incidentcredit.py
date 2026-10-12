"""Direct-mode tests for IncidentCredit. Leader path only; no live consensus."""

from __future__ import annotations

import json

from tests.direct.helpers import (
    CITATION_EXCLUDED,
    CITATION_QUALIFYING,
    CITATION_SLA,
    CONTRACT,
    EXCLUDED_DESC,
    EXCLUSIONS,
    FUND,
    INC_END,
    INC_START,
    MAX_CREDIT,
    QUALIFYING_DESC,
    SLA,
    T0,
    T0_TS,
    WEI_PER_MINUTE,
    assert_conservation,
    claim,
    deploy,
    excluded_payload,
    exclusions_json,
    fund,
    hex_addr,
    mock_class,
    qualifying_payload,
    record,
)


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


def test_constructor_freezes_parties_and_sla(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    cfg = c.get_config()
    assert hex_addr(cfg["provider"]) == hex_addr(direct_alice)
    assert hex_addr(cfg["customer"]) == hex_addr(direct_bob)
    assert cfg["sla_text"] == SLA
    assert cfg["wei_per_minute"] == WEI_PER_MINUTE
    assert cfg["max_credit_per_incident"] == MAX_CREDIT
    ids = [item["id"] for item in cfg["exclusions"]]
    assert ids == [item["id"] for item in EXCLUSIONS]
    assert c.get_incident_count() == 0
    ledger = c.get_ledger()
    assert ledger["funded"] == 0
    assert_conservation(c)


def test_constructor_rejects_customer_equals_provider(
    direct_vm, direct_deploy, direct_alice
):
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("customer cannot equal provider"):
        deploy(direct_vm, direct_deploy, direct_alice, direct_alice)


def test_constructor_rejects_short_sla(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    with direct_vm.expect_revert("sla_text length invalid"):
        deploy(direct_vm, direct_deploy, direct_alice, direct_bob, sla="too short")


def test_constructor_rejects_long_sla(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    with direct_vm.expect_revert("sla_text length invalid"):
        deploy(
            direct_vm,
            direct_deploy,
            direct_alice,
            direct_bob,
            sla="S" * 4001,
        )


def test_constructor_rejects_empty_exclusions(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    with direct_vm.expect_revert("exclusions must contain 1 to 8 items"):
        deploy(direct_vm, direct_deploy, direct_alice, direct_bob, exclusions=[])


def test_constructor_rejects_nine_exclusions(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    items = [
        {"id": f"excl_{i}", "description": f"Exclusion number {i}"}
        for i in range(9)
    ]
    with direct_vm.expect_revert("exclusions must contain 1 to 8 items"):
        deploy(direct_vm, direct_deploy, direct_alice, direct_bob, exclusions=items)


def test_constructor_rejects_duplicate_exclusion_ids(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    items = [
        {"id": "scheduled_maintenance", "description": "A"},
        {"id": "scheduled_maintenance", "description": "B"},
    ]
    with direct_vm.expect_revert("duplicate exclusion id"):
        deploy(direct_vm, direct_deploy, direct_alice, direct_bob, exclusions=items)


def test_constructor_rejects_malformed_exclusions_json(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    direct_vm.warp(T0)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("exclusions_json is not valid JSON"):
        direct_deploy(
            CONTRACT,
            direct_bob,
            SLA,
            "not-json",
            WEI_PER_MINUTE,
            MAX_CREDIT,
            3600,
            3600,
            3600,
            86400,
        )


def test_constructor_rejects_money_bait_exclusion_keys(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    items = [
        {
            "id": "scheduled_maintenance",
            "description": "Maintenance",
            "wei": 1,
        }
    ]
    with direct_vm.expect_revert("extra money-bearing or nested bait keys"):
        deploy(direct_vm, direct_deploy, direct_alice, direct_bob, exclusions=items)


def test_constructor_rejects_bad_exclusion_id(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    items = [{"id": "NOT-VALID", "description": "Bad id format here"}]
    with direct_vm.expect_revert("exclusion id must match"):
        deploy(direct_vm, direct_deploy, direct_alice, direct_bob, exclusions=items)


def test_constructor_rejects_zero_rate(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    with direct_vm.expect_revert("wei_per_minute"):
        deploy(
            direct_vm,
            direct_deploy,
            direct_alice,
            direct_bob,
            wei_per_minute=0,
        )


def test_constructor_rejects_window_below_min(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    with direct_vm.expect_revert("report_window_seconds must be 60-2592000"):
        deploy(
            direct_vm,
            direct_deploy,
            direct_alice,
            direct_bob,
            report_window=59,
        )


def test_constructor_rejects_window_above_max(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    with direct_vm.expect_revert("sunset_seconds must be 60-2592000"):
        deploy(
            direct_vm,
            direct_deploy,
            direct_alice,
            direct_bob,
            sunset=2_592_001,
        )


def test_constructor_rejects_sunset_shorter_than_claim_plus_resolution(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    with direct_vm.expect_revert(
        "sunset_seconds must cover claim plus resolution windows"
    ):
        deploy(
            direct_vm,
            direct_deploy,
            direct_alice,
            direct_bob,
            claim_window=3600,
            resolution_window=3600,
            sunset=3600,
        )


# ---------------------------------------------------------------------------
# Funding
# ---------------------------------------------------------------------------


def test_fund_reserve_provider_only(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    with direct_vm.expect_revert("only provider may fund"):
        fund(direct_vm, c, direct_bob, FUND)
    fund(direct_vm, c, direct_alice, FUND)
    ledger = c.get_ledger()
    assert ledger["funded"] == FUND
    assert ledger["available"] == FUND
    assert ledger["reserved"] == 0
    assert_conservation(c)


def test_zero_value_fund_reverts(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    with direct_vm.expect_revert("value must be > 0"):
        fund(direct_vm, c, direct_alice, 0)


def test_protocol_value_on_non_payable_is_not_escrow(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, c, direct_alice, FUND)
    direct_vm.sender = direct_alice
    direct_vm.value = 10**16
    c.record_incident(INC_START, INC_END, QUALIFYING_DESC)
    direct_vm.value = 0
    ledger = c.get_ledger()
    assert ledger["funded"] == FUND
    assert_conservation(c)


def test_top_up_reserve(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, c, direct_alice, FUND)
    fund(direct_vm, c, direct_alice, FUND)
    assert c.get_ledger()["funded"] == FUND * 2
    assert_conservation(c)


# ---------------------------------------------------------------------------
# Record incident
# ---------------------------------------------------------------------------


def test_only_provider_records(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("only provider may record"):
        c.record_incident(INC_START, INC_END, QUALIFYING_DESC)


def test_record_valid_incident_freezes_facts(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    record(direct_vm, c, direct_alice)
    assert c.get_incident_count() == 1
    inc = c.get_incident("0")
    assert inc["start_ts"] == INC_START
    assert inc["end_ts"] == INC_END
    assert inc["description"] == QUALIFYING_DESC
    assert inc["status"] == "RECORDED"
    assert inc["payout_marker"] == "NONE"
    assert inc["computed_minutes"] == 60
    assert inc["max_possible_credit_wei"] == 60 * WEI_PER_MINUTE


def test_record_rejects_end_not_after_start(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    with direct_vm.expect_revert("end_ts must be greater than start_ts"):
        record(direct_vm, c, direct_alice, start=INC_END, end=INC_END)
    with direct_vm.expect_revert("end_ts must be greater than start_ts"):
        record(direct_vm, c, direct_alice, start=INC_END, end=INC_START)


def test_record_rejects_future_end(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    with direct_vm.expect_revert("incident end must be at or before now"):
        record(direct_vm, c, direct_alice, start=INC_START, end=INC_END + 1)


def test_record_boundary_end_equals_now(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    record(direct_vm, c, direct_alice, start=INC_START, end=T0_TS)
    assert c.get_incident("0")["end_ts"] == T0_TS


def test_record_rejects_overlap(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    record(direct_vm, c, direct_alice)
    with direct_vm.expect_revert("overlapping incident interval"):
        record(
            direct_vm,
            c,
            direct_alice,
            start=INC_START + 60,
            end=INC_END,
            description=QUALIFYING_DESC + " second overlap attempt.",
        )


def test_record_allows_adjacent_intervals(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    record(direct_vm, c, direct_alice)
    record(
        direct_vm,
        c,
        direct_alice,
        start=INC_START - 1800,
        end=INC_START,
        description=QUALIFYING_DESC + " adjacent earlier window.",
    )
    assert c.get_incident_count() == 2


def test_record_rejects_after_report_window(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(
        direct_vm,
        direct_deploy,
        direct_alice,
        direct_bob,
        report_window=60,
    )
    direct_vm.warp("2026-06-01T12:02:00+00:00")
    with direct_vm.expect_revert("report window closed"):
        record(direct_vm, c, direct_alice)


def test_record_rejects_short_description(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    with direct_vm.expect_revert("incident description too short"):
        record(direct_vm, c, direct_alice, description="too short")


def test_record_rejects_long_description(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    with direct_vm.expect_revert("incident description exceeds 1000 chars"):
        record(direct_vm, c, direct_alice, description="D" * 1001)


def test_record_rejects_interval_over_30_days(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    with direct_vm.expect_revert("incident interval exceeds 30 days"):
        record(
            direct_vm,
            c,
            direct_alice,
            start=INC_END - 2_592_001,
            end=INC_END,
        )


def test_incident_cap(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    for i in range(16):
        end = INC_END - i * 60
        start = end - 60
        record(
            direct_vm,
            c,
            direct_alice,
            start=start,
            end=end,
            description=f"{QUALIFYING_DESC} n={i}",
        )
    assert c.get_incident_count() == 16
    with direct_vm.expect_revert("incident cap reached"):
        record(
            direct_vm,
            c,
            direct_alice,
            start=INC_END - 17 * 60,
            end=INC_END - 16 * 60,
            description=f"{QUALIFYING_DESC} overflow",
        )


# ---------------------------------------------------------------------------
# Claim
# ---------------------------------------------------------------------------


def test_only_customer_claims(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, c, direct_alice)
    record(direct_vm, c, direct_alice)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("only customer may claim"):
        c.claim("0")


def test_claim_reserves_max_possible(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, c, direct_alice)
    record(direct_vm, c, direct_alice)
    claim(direct_vm, c, direct_bob)
    inc = c.get_incident("0")
    expected = 60 * WEI_PER_MINUTE
    assert inc["status"] == "CLAIMED"
    assert inc["reserved_wei"] == expected
    ledger = c.get_ledger()
    assert ledger["reserved"] == expected
    assert ledger["available"] == FUND - expected
    assert_conservation(c)


def test_duplicate_claim_reverts(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, c, direct_alice)
    record(direct_vm, c, direct_alice)
    claim(direct_vm, c, direct_bob)
    with direct_vm.expect_revert("incident is not claimable"):
        claim(direct_vm, c, direct_bob)


def test_claim_after_window_reverts(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(
        direct_vm,
        direct_deploy,
        direct_alice,
        direct_bob,
        claim_window=60,
    )
    fund(direct_vm, c, direct_alice)
    record(direct_vm, c, direct_alice)
    direct_vm.warp("2026-06-01T12:02:00+00:00")
    with direct_vm.expect_revert("claim window closed"):
        claim(direct_vm, c, direct_bob)


def test_claim_unknown_incident_reverts(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("unknown incident"):
        c.claim("0")


def test_claim_caps_reservation_to_available(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    small = 10**16
    fund(direct_vm, c, direct_alice, small)
    record(direct_vm, c, direct_alice)
    claim(direct_vm, c, direct_bob)
    inc = c.get_incident("0")
    assert inc["reserved_wei"] == small
    assert c.get_ledger()["available"] == 0
    assert_conservation(c)


def test_claim_with_zero_available_still_opens_resolution(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    record(direct_vm, c, direct_alice)
    claim(direct_vm, c, direct_bob)
    inc = c.get_incident("0")
    assert inc["status"] == "CLAIMED"
    assert inc["reserved_wei"] == 0
    mock_class(direct_vm, qualifying_payload())
    c.resolve("0")
    assert c.get_incident("0")["status"] == "QUALIFYING"
    assert c.get_incident("0")["credit_wei"] == 0
    assert_conservation(c)


# ---------------------------------------------------------------------------
# Resolve — happy paths
# ---------------------------------------------------------------------------


def test_qualifying_computes_integer_minutes_and_credit(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, c, direct_alice)
    record(direct_vm, c, direct_alice)
    claim(direct_vm, c, direct_bob)
    mock_class(direct_vm, qualifying_payload())
    direct_vm.sender = direct_alice
    c.resolve("0")
    inc = c.get_incident("0")
    assert inc["status"] == "QUALIFYING"
    assert inc["consequence"] == "QUALIFYING"
    assert inc["citation"] == CITATION_QUALIFYING
    assert inc["downtime_minutes"] == 60
    assert inc["credit_wei"] == 60 * WEI_PER_MINUTE
    assert inc["payout_marker"] == "CONSUMED"
    assert inc["reserved_wei"] == 0
    ledger = c.get_ledger()
    paid = 60 * WEI_PER_MINUTE
    assert ledger["reserved"] == 0
    assert ledger["available"] == FUND - paid
    assert ledger["credited"] == paid
    assert ledger["withdrawn"] == 0
    assert c.get_credit(direct_bob) == paid
    assert_conservation(c)


def test_excluded_releases_reservation(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, c, direct_alice)
    record(direct_vm, c, direct_alice, description=EXCLUDED_DESC)
    claim(direct_vm, c, direct_bob)
    mock_class(direct_vm, excluded_payload())
    c.resolve("0")
    inc = c.get_incident("0")
    assert inc["status"] == "EXCLUDED"
    assert inc["consequence"] == "scheduled_maintenance"
    assert inc["payout_marker"] == "RELEASED"
    assert inc["credit_wei"] == 0
    ledger = c.get_ledger()
    assert ledger["available"] == FUND
    assert ledger["reserved"] == 0
    assert ledger["credited"] == 0
    assert_conservation(c)


def test_credit_capped_by_max_per_incident(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    cap = 10**16
    c = deploy(
        direct_vm,
        direct_deploy,
        direct_alice,
        direct_bob,
        max_credit=cap,
    )
    fund(direct_vm, c, direct_alice)
    record(direct_vm, c, direct_alice)
    claim(direct_vm, c, direct_bob)
    assert c.get_incident("0")["reserved_wei"] == cap
    mock_class(direct_vm, qualifying_payload())
    c.resolve("0")
    assert c.get_incident("0")["credit_wei"] == cap
    assert_conservation(c)


def test_sub_minute_incident_pays_zero(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, c, direct_alice)
    record(direct_vm, c, direct_alice, start=INC_END - 59, end=INC_END)
    claim(direct_vm, c, direct_bob)
    assert c.get_incident("0")["reserved_wei"] == 0
    mock_class(direct_vm, qualifying_payload())
    c.resolve("0")
    assert c.get_incident("0")["downtime_minutes"] == 0
    assert c.get_incident("0")["credit_wei"] == 0
    assert_conservation(c)


def test_anyone_may_resolve(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, c, direct_alice)
    record(direct_vm, c, direct_alice)
    claim(direct_vm, c, direct_bob)
    mock_class(direct_vm, qualifying_payload())
    direct_vm.sender = direct_charlie
    c.resolve("0")
    assert c.get_incident("0")["status"] == "QUALIFYING"


def test_repeated_resolve_reverts(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, c, direct_alice)
    record(direct_vm, c, direct_alice)
    claim(direct_vm, c, direct_bob)
    mock_class(direct_vm, qualifying_payload())
    c.resolve("0")
    with direct_vm.expect_revert("resolve requires CLAIMED"):
        c.resolve("0")


def test_resolve_after_window_reverts(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(
        direct_vm,
        direct_deploy,
        direct_alice,
        direct_bob,
        resolution_window=60,
    )
    fund(direct_vm, c, direct_alice)
    record(direct_vm, c, direct_alice)
    claim(direct_vm, c, direct_bob)
    direct_vm.warp("2026-06-01T12:02:00+00:00")
    mock_class(direct_vm, qualifying_payload())
    with direct_vm.expect_revert("resolution window closed"):
        c.resolve("0")


# ---------------------------------------------------------------------------
# Adversarial LLM results
# ---------------------------------------------------------------------------


def test_conflicting_nested_consequence_reverts(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, c, direct_alice)
    record(direct_vm, c, direct_alice)
    claim(direct_vm, c, direct_bob)
    mock_class(
        direct_vm,
        {
            "consequence": "QUALIFYING",
            "citation": CITATION_QUALIFYING,
            "result": {"consequence": "scheduled_maintenance"},
        },
    )
    with direct_vm.expect_revert("nested consequence conflicts with top-level"):
        c.resolve("0")
    assert c.get_incident("0")["status"] == "CLAIMED"
    assert_conservation(c)


def test_unknown_exclusion_id_reverts(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, c, direct_alice)
    record(direct_vm, c, direct_alice)
    claim(direct_vm, c, direct_bob)
    mock_class(
        direct_vm,
        {"consequence": "acts_of_god", "citation": CITATION_SLA},
    )
    with direct_vm.expect_revert("unknown exclusion ID"):
        c.resolve("0")
    assert c.get_incident("0")["status"] == "CLAIMED"


def test_malformed_json_reverts(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, c, direct_alice)
    record(direct_vm, c, direct_alice)
    claim(direct_vm, c, direct_bob)
    mock_class(direct_vm, "{not json")
    with direct_vm.expect_revert("malformed JSON"):
        c.resolve("0")


def test_empty_citation_reverts(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, c, direct_alice)
    record(direct_vm, c, direct_alice)
    claim(direct_vm, c, direct_bob)
    mock_class(direct_vm, {"consequence": "QUALIFYING", "citation": ""})
    with direct_vm.expect_revert("citation empty or too short"):
        c.resolve("0")


def test_mismatched_citation_reverts(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, c, direct_alice)
    record(direct_vm, c, direct_alice)
    claim(direct_vm, c, direct_bob)
    mock_class(
        direct_vm,
        {
            "consequence": "QUALIFYING",
            "citation": "this phrase is nowhere in the frozen texts",
        },
    )
    with direct_vm.expect_revert("citation does not appear"):
        c.resolve("0")


def test_forbidden_wei_key_reverts(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, c, direct_alice)
    record(direct_vm, c, direct_alice)
    claim(direct_vm, c, direct_bob)
    mock_class(
        direct_vm,
        {
            "consequence": "QUALIFYING",
            "citation": CITATION_QUALIFYING,
            "wei": 12345,
        },
    )
    with direct_vm.expect_revert("forbidden money/time/address keys"):
        c.resolve("0")


def test_sla_citation_accepted(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, c, direct_alice)
    record(direct_vm, c, direct_alice)
    claim(direct_vm, c, direct_bob)
    mock_class(
        direct_vm,
        {"consequence": "QUALIFYING", "citation": CITATION_SLA},
    )
    c.resolve("0")
    assert c.get_incident("0")["citation"] == CITATION_SLA
    assert c.get_incident("0")["status"] == "QUALIFYING"


# ---------------------------------------------------------------------------
# Isolation across incidents
# ---------------------------------------------------------------------------


def test_rejected_claim_does_not_block_other_incident(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, c, direct_alice)
    record(direct_vm, c, direct_alice, description=EXCLUDED_DESC)
    record(
        direct_vm,
        c,
        direct_alice,
        start=INC_START - 7200,
        end=INC_START - 3600,
        description=QUALIFYING_DESC,
    )
    claim(direct_vm, c, direct_bob, "0")
    mock_class(direct_vm, excluded_payload())
    c.resolve("0")
    assert c.get_incident("0")["status"] == "EXCLUDED"
    claim(direct_vm, c, direct_bob, "1")
    mock_class(direct_vm, qualifying_payload())
    c.resolve("1")
    assert c.get_incident("1")["status"] == "QUALIFYING"
    assert_conservation(c)


def test_multiple_incidents_independent_reservations(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, c, direct_alice)
    record(direct_vm, c, direct_alice)
    record(
        direct_vm,
        c,
        direct_alice,
        start=INC_START - 7200,
        end=INC_START - 3600,
        description=EXCLUDED_DESC,
    )
    claim(direct_vm, c, direct_bob, "0")
    claim(direct_vm, c, direct_bob, "1")
    expected = 60 * WEI_PER_MINUTE
    ledger = c.get_ledger()
    assert ledger["reserved"] == expected * 2
    mock_class(direct_vm, excluded_payload())
    c.resolve("1")
    ledger = c.get_ledger()
    assert ledger["reserved"] == expected
    mock_class(direct_vm, qualifying_payload())
    c.resolve("0")
    ledger = c.get_ledger()
    assert ledger["reserved"] == 0
    assert ledger["credited"] == expected
    assert ledger["withdrawn"] == 0
    assert_conservation(c)


# ---------------------------------------------------------------------------
# Timeouts and sunset
# ---------------------------------------------------------------------------


def test_expire_unclaimed(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(
        direct_vm,
        direct_deploy,
        direct_alice,
        direct_bob,
        claim_window=60,
    )
    fund(direct_vm, c, direct_alice)
    record(direct_vm, c, direct_alice)
    with direct_vm.expect_revert("claim window still open"):
        c.expire_unclaimed("0")
    direct_vm.warp("2026-06-01T12:02:00+00:00")
    c.expire_unclaimed("0")
    assert c.get_incident("0")["status"] == "UNCLAIMED"
    with direct_vm.expect_revert("incident is not claimable"):
        claim(direct_vm, c, direct_bob)
    assert c.get_ledger()["available"] == FUND
    assert_conservation(c)


def test_timeout_unresolved_called_by_provider_credits_customer_full_reserve(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(
        direct_vm,
        direct_deploy,
        direct_alice,
        direct_bob,
        resolution_window=60,
    )
    fund(direct_vm, c, direct_alice)
    record(direct_vm, c, direct_alice)
    claim(direct_vm, c, direct_bob)
    with direct_vm.expect_revert("resolution window still open"):
        c.timeout_unresolved("0")
    direct_vm.warp("2026-06-01T12:02:00+00:00")
    direct_vm.sender = direct_alice
    c.timeout_unresolved("0")
    inc = c.get_incident("0")
    assert inc["status"] == "TIMEOUT"
    assert inc["consequence"] == "TIMEOUT_CREDIT"
    assert inc["payout_marker"] == "CONSUMED"
    held = 60 * WEI_PER_MINUTE
    assert inc["credit_wei"] == held
    ledger = c.get_ledger()
    assert ledger["reserved"] == 0
    assert ledger["available"] == FUND - held
    assert ledger["credited"] == held
    assert c.get_credit(direct_alice) == 0  # Provider earns NO bounty on timeout
    assert c.get_credit(direct_bob) == held  # Customer receives full credit
    assert_conservation(c)


def test_timeout_unresolved_called_by_customer_awards_full_reserve(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(
        direct_vm,
        direct_deploy,
        direct_alice,
        direct_bob,
        resolution_window=60,
    )
    fund(direct_vm, c, direct_alice)
    record(direct_vm, c, direct_alice)
    claim(direct_vm, c, direct_bob)
    held = 60 * WEI_PER_MINUTE
    direct_vm.warp("2026-06-01T12:02:00+00:00")
    direct_vm.sender = direct_bob
    c.timeout_unresolved("0")
    inc = c.get_incident("0")
    assert inc["status"] == "TIMEOUT"
    assert inc["consequence"] == "TIMEOUT_CREDIT"
    assert inc["payout_marker"] == "CONSUMED"
    assert inc["credit_wei"] == held
    ledger = c.get_ledger()
    assert ledger["reserved"] == 0
    assert ledger["available"] == FUND - held
    assert ledger["credited"] == held
    assert c.get_credit(direct_bob) == held
    assert c.get_credit(direct_alice) == 0
    assert_conservation(c)


def test_timeout_does_not_block_other_incident(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(
        direct_vm,
        direct_deploy,
        direct_alice,
        direct_bob,
        resolution_window=60,
    )
    fund(direct_vm, c, direct_alice)
    record(direct_vm, c, direct_alice)
    record(
        direct_vm,
        c,
        direct_alice,
        start=INC_START - 7200,
        end=INC_START - 3600,
        description=QUALIFYING_DESC + " second.",
    )
    claim(direct_vm, c, direct_bob, "0")
    claim(direct_vm, c, direct_bob, "1")
    direct_vm.warp("2026-06-01T12:02:00+00:00")
    c.timeout_unresolved("0")
    mock_class(direct_vm, qualifying_payload())
    with direct_vm.expect_revert("resolution window closed"):
        c.resolve("1")
    c.timeout_unresolved("1")
    assert c.get_incident("0")["status"] == "TIMEOUT"
    assert c.get_incident("1")["status"] == "TIMEOUT"
    assert_conservation(c)


def test_recover_unreserved_after_sunset(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(
        direct_vm,
        direct_deploy,
        direct_alice,
        direct_bob,
        sunset=180,
        claim_window=60,
        resolution_window=60,
    )
    fund(direct_vm, c, direct_alice)
    record(direct_vm, c, direct_alice)
    claim(direct_vm, c, direct_bob)
    reserved = c.get_incident("0")["reserved_wei"]
    with direct_vm.expect_revert("sunset not reached"):
        direct_vm.sender = direct_alice
        c.recover_unreserved()
    direct_vm.warp("2026-06-01T12:03:01+00:00")
    with direct_vm.expect_revert("claimed incident still open"):
        direct_vm.sender = direct_alice
        c.recover_unreserved()
    direct_vm.sender = direct_alice
    c.timeout_unresolved("0")
    # Timeout awards the reservation to customer (direct_bob)
    assert c.get_credit(direct_bob) == reserved
    assert c.get_credit(direct_alice) == 0
    direct_vm.sender = direct_alice
    c.recover_unreserved()
    ledger = c.get_ledger()
    assert ledger["available"] == 0
    assert ledger["reserved"] == 0
    assert ledger["credited"] == FUND
    assert ledger["returned"] == 0
    assert c.get_credit(direct_alice) == FUND - reserved
    assert c.get_credit(direct_bob) == reserved
    assert_conservation(c)
    with direct_vm.expect_revert("no unreserved funds"):
        direct_vm.sender = direct_alice
        c.recover_unreserved()
    with direct_vm.expect_revert("only provider may recover"):
        direct_vm.sender = direct_bob
        c.recover_unreserved()


def test_recover_does_not_take_reserved(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(
        direct_vm,
        direct_deploy,
        direct_alice,
        direct_bob,
        sunset=180,
        claim_window=60,
        resolution_window=60,
    )
    fund(direct_vm, c, direct_alice)
    record(direct_vm, c, direct_alice)
    claim(direct_vm, c, direct_bob)
    held = c.get_incident("0")["reserved_wei"]
    direct_vm.warp("2026-06-01T12:03:01+00:00")
    with direct_vm.expect_revert("claimed incident still open"):
        direct_vm.sender = direct_alice
        c.recover_unreserved()
    assert c.get_incident("0")["reserved_wei"] == held
    direct_vm.sender = direct_bob
    c.timeout_unresolved("0")
    ledger = c.get_ledger()
    assert ledger["reserved"] == 0
    assert ledger["available"] == FUND - held
    assert c.get_credit(direct_bob) == held
    assert c.get_credit(direct_alice) == 0
    assert_conservation(c)


def test_fund_and_record_after_sunset_revert(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(
        direct_vm,
        direct_deploy,
        direct_alice,
        direct_bob,
        sunset=180,
        claim_window=60,
        resolution_window=60,
    )
    fund(direct_vm, c, direct_alice)
    direct_vm.warp("2026-06-01T12:03:01+00:00")
    with direct_vm.expect_revert("sunset reached"):
        fund(direct_vm, c, direct_alice)
    with direct_vm.expect_revert("sunset reached"):
        record(direct_vm, c, direct_alice)


# ---------------------------------------------------------------------------
# Withdraw
# ---------------------------------------------------------------------------


def test_withdraw_no_credit_reverts(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("no credit"):
        c.withdraw()


def test_repeated_withdraw_after_consume(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, c, direct_alice)
    record(direct_vm, c, direct_alice)
    claim(direct_vm, c, direct_bob)
    mock_class(direct_vm, qualifying_payload())
    c.resolve("0")
    credit = c.get_credit(direct_bob)
    ledger = c.get_ledger()
    paid = 60 * WEI_PER_MINUTE
    assert ledger["credited"] == paid
    assert ledger["withdrawn"] == 0
    assert credit == paid
    if credit > 0:
        direct_vm.sender = direct_bob
        try:
            c.withdraw()
        except Exception:
            assert c.get_credit(direct_bob) == credit
        else:
            assert c.get_credit(direct_bob) == 0
            with direct_vm.expect_revert("no credit"):
                c.withdraw()
    assert_conservation(c)
    with direct_vm.expect_revert("resolve requires CLAIMED"):
        c.resolve("0")


def test_failed_transfer_does_not_spend_other_incident_reserve(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, c, direct_alice)
    record(direct_vm, c, direct_alice)
    record(
        direct_vm,
        c,
        direct_alice,
        start=INC_START - 7200,
        end=INC_START - 3600,
        description=EXCLUDED_DESC,
    )
    claim(direct_vm, c, direct_bob, "0")
    claim(direct_vm, c, direct_bob, "1")
    reserved_1 = c.get_incident("1")["reserved_wei"]
    mock_class(direct_vm, qualifying_payload())
    c.resolve("0")
    assert c.get_incident("1")["reserved_wei"] == reserved_1
    assert c.get_incident("1")["status"] == "CLAIMED"
    ledger = c.get_ledger()
    assert ledger["reserved"] == reserved_1
    assert_conservation(c)


def test_conservation_across_full_lifecycle(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(
        direct_vm,
        direct_deploy,
        direct_alice,
        direct_bob,
        sunset=10_000,
        claim_window=3600,
        resolution_window=60,
    )
    fund(direct_vm, c, direct_alice)
    record(direct_vm, c, direct_alice)
    record(
        direct_vm,
        c,
        direct_alice,
        start=INC_START - 7200,
        end=INC_START - 3600,
        description=EXCLUDED_DESC,
    )
    record(
        direct_vm,
        c,
        direct_alice,
        start=INC_START - 14400,
        end=INC_START - 10800,
        description=QUALIFYING_DESC + " later unclaimed.",
    )
    claim(direct_vm, c, direct_bob, "0")
    claim(direct_vm, c, direct_bob, "1")
    mock_class(direct_vm, qualifying_payload())
    c.resolve("0")
    mock_class(direct_vm, excluded_payload())
    c.resolve("1")
    direct_vm.warp("2026-06-01T13:02:00+00:00")
    c.expire_unclaimed("2")
    direct_vm.warp("2026-06-01T15:00:00+00:00")
    direct_vm.sender = direct_alice
    c.recover_unreserved()
    assert_conservation(c)
    assert c.get_incident("0")["status"] == "QUALIFYING"
    assert c.get_incident("1")["status"] == "EXCLUDED"
    assert c.get_incident("2")["status"] == "UNCLAIMED"
    ledger = c.get_ledger()
    assert ledger["available"] == 0
    assert ledger["reserved"] == 0
    paid = 60 * WEI_PER_MINUTE
    assert ledger["credited"] == FUND
    assert ledger["withdrawn"] == 0
    assert ledger["returned"] == 0
    assert c.get_credit(direct_bob) == paid
    assert c.get_credit(direct_alice) == FUND - paid
    assert_conservation(c)


def test_record_rejected_when_sunset_cannot_cover_remaining_windows(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(
        direct_vm,
        direct_deploy,
        direct_alice,
        direct_bob,
        sunset=180,
        claim_window=60,
        resolution_window=60,
    )
    fund(direct_vm, c, direct_alice)
    record(direct_vm, c, direct_alice)
    claim(direct_vm, c, direct_bob)
    assert c.get_incident("0")["status"] == "CLAIMED"
    direct_vm.warp("2026-06-01T12:01:01+00:00")
    with direct_vm.expect_revert(
        "insufficient time before sunset for claim and resolution"
    ):
        record(
            direct_vm,
            c,
            direct_alice,
            start=INC_START - 7200,
            end=INC_START - 3600,
            description=QUALIFYING_DESC + " too late to finish.",
        )


def test_timeout_bounty_pays_third_party_and_credits_customer(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie
):
    c = deploy(
        direct_vm,
        direct_deploy,
        direct_alice,
        direct_bob,
        resolution_window=60,
    )
    fund(direct_vm, c, direct_alice)
    record(direct_vm, c, direct_alice)
    claim(direct_vm, c, direct_bob)
    held = c.get_incident("0")["reserved_wei"]
    direct_vm.warp("2026-06-01T12:02:00+00:00")
    direct_vm.sender = direct_charlie
    c.timeout_unresolved("0")
    bounty = held * 100 // 10_000
    customer_credit = held - bounty
    inc = c.get_incident("0")
    assert inc["status"] == "TIMEOUT"
    assert inc["consequence"] == "TIMEOUT_CREDIT"
    assert inc["credit_wei"] == customer_credit
    assert inc["payout_marker"] == "CONSUMED"
    assert c.get_credit(direct_charlie) == bounty
    assert c.get_credit(direct_bob) == customer_credit
    assert c.get_credit(direct_alice) == 0
    ledger = c.get_ledger()
    assert ledger["reserved"] == 0
    assert ledger["available"] == FUND - held
    assert ledger["credited"] == held
    assert ledger["withdrawn"] == 0
    assert_conservation(c)


def test_timeout_zero_reservation_pays_no_bounty(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie
):
    c = deploy(
        direct_vm,
        direct_deploy,
        direct_alice,
        direct_bob,
        resolution_window=60,
    )
    record(direct_vm, c, direct_alice)
    claim(direct_vm, c, direct_bob)
    assert c.get_incident("0")["reserved_wei"] == 0
    direct_vm.warp("2026-06-01T12:02:00+00:00")
    direct_vm.sender = direct_charlie
    c.timeout_unresolved("0")
    assert c.get_incident("0")["status"] == "TIMEOUT"
    assert c.get_credit(direct_charlie) == 0
    assert c.get_ledger()["credited"] == 0
    assert_conservation(c)


def test_resolve_does_not_emit_until_withdraw(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(direct_vm, direct_deploy, direct_alice, direct_bob)
    fund(direct_vm, c, direct_alice)
    record(direct_vm, c, direct_alice)
    claim(direct_vm, c, direct_bob)
    mock_class(direct_vm, qualifying_payload())
    c.resolve("0")
    paid = 60 * WEI_PER_MINUTE
    assert c.get_credit(direct_bob) == paid
    ledger = c.get_ledger()
    assert ledger["credited"] == paid
    assert ledger["withdrawn"] == 0
    direct_vm.sender = direct_bob
    c.withdraw()
    assert c.get_credit(direct_bob) == 0
    ledger = c.get_ledger()
    assert ledger["credited"] == 0
    assert ledger["withdrawn"] == paid
    with direct_vm.expect_revert("no credit"):
        c.withdraw()
    assert_conservation(c)


def test_provider_recover_stays_credited_until_withdraw(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    c = deploy(
        direct_vm,
        direct_deploy,
        direct_alice,
        direct_bob,
        sunset=180,
        claim_window=60,
        resolution_window=60,
    )
    fund(direct_vm, c, direct_alice)
    record(direct_vm, c, direct_alice)
    direct_vm.warp("2026-06-01T12:01:01+00:00")
    c.expire_unclaimed("0")
    direct_vm.warp("2026-06-01T12:03:01+00:00")
    direct_vm.sender = direct_alice
    c.recover_unreserved()
    assert c.get_credit(direct_alice) == FUND
    ledger = c.get_ledger()
    assert ledger["credited"] == FUND
    assert ledger["returned"] == 0
    c.withdraw()
    assert c.get_credit(direct_alice) == 0
    ledger = c.get_ledger()
    assert ledger["credited"] == 0
    assert ledger["returned"] == FUND
    assert_conservation(c)
