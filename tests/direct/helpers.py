"""Shared helpers for IncidentCredit direct tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import gltest.direct.wasi_mock as wasi_mock

CONTRACT = "contracts/incidentcredit.py"

SLA = (
    "Provider guarantees 99.9% monthly uptime for the production API. "
    "Scheduled maintenance windows posted forty eight hours in advance are excluded. "
    "Force majeure including regional power grid failure is excluded. "
    "Outages caused by invalid customer credentials are excluded."
)

EXCLUSIONS = [
    {
        "id": "scheduled_maintenance",
        "description": "Scheduled maintenance posted forty eight hours in advance",
    },
    {
        "id": "force_majeure",
        "description": "Regional power grid failure and other force majeure",
    },
    {
        "id": "customer_caused",
        "description": "Outages caused by invalid customer credentials",
    },
]

QUALIFYING_DESC = (
    "API returned HTTP 500 for all /v1/orders requests due to a failed "
    "database failover. No maintenance notice was posted."
)

EXCLUDED_DESC = (
    "API was unavailable during the scheduled maintenance window posted "
    "forty eight hours in advance for a cluster upgrade."
)

WEI_PER_MINUTE = 10**15
MAX_CREDIT = 10**18
FUND = 10**18
REPORT_WINDOW = 86_400
CLAIM_WINDOW = 3600
RESOLUTION_WINDOW = 3600
SUNSET = 86_400

T0 = "2026-06-01T12:00:00+00:00"
T0_TS = int(datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp())
INC_START = T0_TS - 3600
INC_END = T0_TS

CITATION_QUALIFYING = "failed database failover"
CITATION_EXCLUDED = "scheduled maintenance window posted"
CITATION_SLA = "99.9% monthly uptime"


def exclusions_json(items=None) -> str:
    return json.dumps(items if items is not None else EXCLUSIONS)


def hex_addr(addr) -> str:
    if hasattr(addr, "as_hex"):
        return str(addr.as_hex).lower()
    if isinstance(addr, (bytes, bytearray)):
        return "0x" + bytes(addr).hex()
    s = str(addr).lower()
    if s.startswith("0x"):
        return s
    return "0x" + s


def deploy(
    direct_vm,
    direct_deploy,
    provider,
    customer,
    sla=SLA,
    exclusions=None,
    wei_per_minute=WEI_PER_MINUTE,
    max_credit=MAX_CREDIT,
    report_window=REPORT_WINDOW,
    claim_window=CLAIM_WINDOW,
    resolution_window=RESOLUTION_WINDOW,
    sunset=SUNSET,
    warp=T0,
):
    if warp is not None:
        direct_vm.warp(warp)
    direct_vm.sender = provider
    return direct_deploy(
        CONTRACT,
        customer,
        sla,
        exclusions_json(exclusions),
        wei_per_minute,
        max_credit,
        report_window,
        claim_window,
        resolution_window,
        sunset,
    )


def fund(direct_vm, contract, provider, amount=FUND):
    direct_vm.sender = provider
    direct_vm.value = amount
    contract.fund_reserve()
    direct_vm.value = 0


def record(
    direct_vm,
    contract,
    provider,
    start=INC_START,
    end=INC_END,
    description=QUALIFYING_DESC,
):
    direct_vm.sender = provider
    contract.record_incident(start, end, description)


def claim(direct_vm, contract, customer, incident_id="0"):
    direct_vm.sender = customer
    contract.claim(incident_id)


def mock_class(direct_vm, payload):
    if hasattr(direct_vm, "clear_mocks"):
        direct_vm.clear_mocks()
    if isinstance(payload, str):
        body = payload
    else:
        body = json.dumps(payload)
    direct_vm.mock_llm(r".*", body)


def qualifying_payload():
    return {"consequence": "QUALIFYING", "citation": CITATION_QUALIFYING}


def excluded_payload(exclusion_id="scheduled_maintenance"):
    return {"consequence": exclusion_id, "citation": CITATION_EXCLUDED}


def assert_conservation(contract):
    ledger = contract.get_ledger()
    assert ledger["conservation_ok"] is True
    total = (
        ledger["available"]
        + ledger["reserved"]
        + ledger["credited"]
        + ledger["withdrawn"]
        + ledger["returned"]
    )
    assert total == ledger["funded"]
