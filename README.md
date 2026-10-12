# IncidentCredit

**IncidentCredit** is a bilateral, frozen-SLA service-credit escrow primitive built natively for GenLayer Studio Next (test GEN, Chain ID `61997`). It allows an infrastructure provider and enterprise customer to lock an agreed SLA and exclusion schedule on-chain; when an acknowledged outage occurs, decentralized validator consensus evaluates the incident text directly against the frozen exclusion terms to determine compensation without trusted oracles or intermediaries.

This repository contains the smart contract ([contracts/incidentcredit.py](contracts/incidentcredit.py)), the direct test suite ([tests/direct/test_incidentcredit.py](tests/direct/test_incidentcredit.py)), reproducible Studio Next deployment and verification scripts, the ABI schema ([deploy/schema.json](deploy/schema.json)), and on-chain verification snapshots. There is no frontend or Transaction Kit.

---

## Why GenLayer Consensus is Consequential

Traditional smart contract escrows require rigid, structured oracles, centralized multi-sigs, or trusted administrative keys to report whether an outage breached an SLA or fell under an agreed contractual exception (such as scheduled maintenance, force majeure, or customer credential error). 

GenLayer makes AI consensus consequential by executing non-deterministic LLM evaluation directly within the validator execution loop:
1. **Decentralized Evaluation**: Multiple independent validators read the frozen incident description and the frozen exclusion clauses, executing an isolated non-deterministic inference call (`gl.nondet.call_llm`).
2. **Equivalence Principle**: Validators reach consensus on the classification (`QUALIFYING` vs. a specific matched exclusion ID like `scheduled_maintenance`) and supporting text citation directly from the contract terms.
3. **Deterministic Financial Settlement**: The model never emits wei amounts, percentage splits, timestamps, or recipient addresses. Once consensus determines the classification, deterministic on-chain Python logic computes exact wei credit from integer downtime minutes multiplied by `wei_per_minute` (capped by `max_credit_per_incident` and reserved escrow). Escrow is automatically adjusted without human intervention.

---

## Frozen Inputs

When initialized, the contract locks all contractual terms permanently:
- `provider`: Authorized infrastructure provider address who funds the reserve and records outages.
- `customer`: Authorized customer address entitled to file claims and receive service credits.
- `sla_text`: Immutable textual representation of the service level agreement (20 to 2,000 characters).
- `exclusions_json`: Schema-validated immutable array of contractual exclusions (1 to 8 items). Each exclusion specifies a unique `id` (2 to 32 characters, alphanumeric/dashes/underscores) and a plain-text `description` (10 to 200 characters).
- `wei_per_minute`: Fixed rate of service credit wei accrued per whole integer minute of downtime.
- `max_credit_per_incident`: Hard ceiling on the maximum credit payable for any single outage incident.
- `report_window_seconds`: Permissible window following outage end for provider to record it (60s to 30 days).
- `claim_window_seconds`: Permissible window following incident recording for customer to file a claim (60s to 30 days).
- `resolution_window_seconds`: Permissible window for validator consensus resolution before timeout (60s to 30 days).
- `sunset_seconds`: Total lifespan of the contract after deployment before leftover funds can be reclaimed (at least `claim_window + resolution_window + 60s`, up to 365 days).

---

## Exact State Transitions

```
deploy (provider, customer, SLA, exclusions, rate, cap, windows)
        │
        │ fund_reserve (provider, value > 0)
        ▼
   funded ledger
        │
        │ record_incident (provider, past interval, description)
        ▼
     RECORDED ── expire_unclaimed after claim window ──► UNCLAIMED (no reserve, marker: RELEASED)
        │
        │ claim (customer) reserves min(max possible credit, available)
        ▼
      CLAIMED
        ├── resolve ── QUALIFYING ── consume reservation, credit customer, leftover → available (marker: CONSUMED)
        ├── resolve ── EXCLUDED   ── release full reservation → available (marker: RELEASED)
        └── timeout_unresolved after resolution window ── TIMEOUT_CREDIT → credit customer (marker: CONSUMED)
                │
                ▼
         sunset reached
                │ recover_unreserved (provider) credits leftover available funds to provider
```

### Transition Specifications:
- `deploy`: Sets all configuration parameters and calculates `sunset_ts = deploy_ts + sunset_seconds`. Ledger starts unfunded (`funded = 0`).
- `fund_reserve` (provider only, payable): Adds deposited value to `available` and `funded`.
- `record_incident` (provider only): Records past non-overlapping downtime interval. Asserts `start_ts < end_ts <= now`, `(now - end_ts) <= report_window_seconds`, and crucially `now + claim_window + resolution_window <= sunset_ts`. Initial status: `RECORDED`.
- `expire_unclaimed` (anyone): Callable if `now > recorded_ts + claim_window_seconds` and status is `RECORDED`. Sets status to `UNCLAIMED`, marker to `RELEASED`. No funds were ever reserved.
- `claim` (customer only): Callable while `now <= recorded_ts + claim_window_seconds`. Computes `max_possible_credit_wei = min(minutes * wei_per_minute, max_credit_per_incident)`. Sets `reserved_wei = min(max_possible_credit, available)`. Moves `reserved_wei` from `available` to `reserved`. Sets status to `CLAIMED` and opens the resolution window.
- `resolve` (anyone): Runs non-deterministic LLM classification via GenLayer validators.
  - If classified `QUALIFYING`: credit amount is computed. `credited[customer] += credit_wei`. Remaining reservation `(reserved_wei - credit_wei)` returns to `available`. Status becomes `QUALIFYING`, payout marker becomes `CONSUMED`.
  - If classified as an exclusion ID: full `reserved_wei` returns to `available`. Status becomes `EXCLUDED`, payout marker becomes `RELEASED`.
  - Second `resolve` attempt on an incident with a payout marker set strictly reverts.
- `timeout_unresolved` (anyone): Callable if `now > claim_ts + resolution_window_seconds` and status remains `CLAIMED`. Executes the defensible timeout rule.
- `recover_unreserved` (provider only): Callable only when `now >= sunset_ts`. Moves all remaining unreserved `available` funds to `credited[provider]`. Sets `available = 0`.
- `withdraw` (beneficiary only): Executes Checks-Effects-Interactions (CEI) to transfer accumulated `credited[msg.sender]` via `emit_transfer`.

---

## Defensible Timeout Rule & Moral Hazard Resolution

In early prototype designs, if an incident timed out without validator resolution, the held reserve returned to the provider’s available balance. This created a severe **moral hazard**: if an outage was undeniably qualifying, the service provider was financially incentivized to delay consensus, submit malicious appeal rotations, or simply invoke `timeout_unresolved` themselves to avoid paying the customer.

IncidentCredit implements an incentive-aligned timeout policy:
1. **Customer Caller**: When invoked by the customer, they receive **100% of the reserved funds** (`held`).
2. **Third-Party Caller**: When invoked by a neutral third party, the caller receives a **1% caller bounty** (`TIMEOUT_BOUNTY_BPS = 100`), and the customer receives the remaining **99%**.
3. **Provider Caller**: When invoked by the provider, the caller receives **0 bounty**, and the customer receives **100% of the reservation**.

Any excess reservation above `max_possible_credit_wei` returns to `available`. Payout marker is set to `CONSUMED`.

### Tradeoff Analysis
- **Commercial Rationale**: Under standard enterprise SLA doctrine, an acknowledged downtime event documented by the provider is legally presumed compensable unless an agreed contractual exclusion is proven within the dispute resolution window. Defaulting to customer credit upon resolution timeout enforces accountability on both the provider and the validator network.
- **Tradeoff**: In the improbable event of an extended validator consensus outage during a genuinely excluded incident (e.g., scheduled maintenance), the customer receives default credit once the resolution window expires. This tradeoff is managed by selecting an appropriately sized `resolution_window_seconds` (e.g., 1 hour to 24 hours in production) to give validators adequate time to reach consensus.

---

## One-Time Payout Accounting & Invariants

The contract strictly enforces money conservation across all state transitions:

$$\text{available} + \text{reserved} + \text{credited} + \text{withdrawn} + \text{returned} == \text{funded}$$

- **Checks-Effects-Interactions (CEI)**: Credit balances are decremented before calling `emit_transfer`. Every credit can be emitted at most once. Calling `withdraw` with zero balance immediately reverts with `"no credit"`.
- **Queued vs. Finalized Transfers**: Calling `_Recipient(to).emit_transfer(value=amount)` in GenLayer emits an outbound transfer message within the transaction execution context. A queued message is not independent proof of final external settlement until the transaction reaches `FINALIZED` consensus.
- **What On-Chain Records Prove**: The finalized withdraw transaction and ledger prove that:
  1. The on-contract `credited` balance was legitimately earned through consensus or timeout.
  2. The credit was deducted atomically from the ledger.
  3. The transfer was successfully executed by the GenLayer VM, permanently incrementing `withdrawn` (for customer credits) or `returned` (for provider sunset recoveries).

---

## Size & Deadline Limits

| Parameter | Minimum | Maximum | Notes |
| :--- | :--- | :--- | :--- |
| `sla_text` | 20 chars | 2,000 chars | Frozen at deployment |
| `exclusions` count | 1 item | 8 items | Schema-validated JSON array |
| Exclusion `id` | 2 chars | 32 chars | Regex: `^[a-z0-9_-]+$` |
| Exclusion `description` | 10 chars | 200 chars | Non-empty plain text |
| Incident `description` | 20 chars | 500 chars | Frozen fact description |
| Outage Interval | 1 second | 30 days (2,592,000s) | Integer minutes computed on-chain |
| `report_window_seconds` | 60 seconds | 30 days (2,592,000s) | Post-incident report window |
| `claim_window_seconds` | 60 seconds | 30 days (2,592,000s) | Post-record claim window |
| `resolution_window_seconds`| 60 seconds | 30 days (2,592,000s) | Claim-to-resolution window |
| `sunset_seconds` | `claim + res + 60s` | 365 days (31,536,000s) | Lifecycle boundary |

---

## Limitations

1. **Bilateral Escrow**: Built specifically for one provider and one customer per deployment. It is not an open multi-tenant marketplace.
2. **Native Test GEN Only**: Designed for native GEN escrow; does not support ERC-20 tokens or multi-token reserves.
3. **Integer Minutes Resolution**: Downtime is computed in whole integer minutes (`(end_ts - start_ts) // 60`). Fractional seconds do not accrue fractional credit.
4. **Provider-Initiated Outages**: Outage intervals must be recorded by the provider (acknowledged incidents). The contract does not ingest raw client telemetry or ping monitors.

---

## Canonical Studio Next Deployment

Deployed and verified on GenLayer Studio Next (Chain ID `61997` / `0xf22d`).

| Parameter | Value |
| :--- | :--- |
| **Network RPC** | `https://studio-next.genlayer.com/api` |
| **Chain ID** | `61997` (`0xf22d`) |
| **Explorer** | [explorer-studio-dev.genlayer.com](https://explorer-studio-dev.genlayer.com) |
| **Canonical Contract Address** | [`0x310376D1c50cfD421bCa28Dfc3A8057253d7954f`](https://explorer-studio-dev.genlayer.com/address/0x310376D1c50cfD421bCa28Dfc3A8057253d7954f) |
| **Deploy Transaction** | [`0x66f7688350131916c8e0511c848121275e5f4d357c50d7b6ebfc56dac8e5049d`](https://explorer-studio-dev.genlayer.com/tx/0x66f7688350131916c8e0511c848121275e5f4d357c50d7b6ebfc56dac8e5049d) |
| **Transaction Status** | `FINALIZED` |
| **Execution Result** | `FINISHED_WITH_RETURN` |
| **Provider** | `0x74e8338F52617cBF3e4A2Aaa92ddA6665Ec4991F` |
| **Customer** | `0xac344A9aB8808d6fcfF1f8D18F794C3b24FE2634` |
| **Exact Source SHA-256** | `d431b69baaa2fa3a022cd8956ee9b217428719a998934d0a074026c790ddb05d` |
| **Byte Count** | `33421` bytes |
| **On-Chain Code Match** | `true` (byte-for-byte verified via `getContractCode`) |
| **Saved Fee Estimate** | Saved in [deploy/fee-profile.json](deploy/fee-profile.json) (`feeValue`: `100000000000010352`) |

> **Superseded Deployment**:  
> Prototype deployment [`0x6b1Ce0B7bb94b978fd3CE0fD5981119D8dcCA1b0`](https://explorer-studio-dev.genlayer.com/address/0x6b1Ce0B7bb94b978fd3CE0fD5981119D8dcCA1b0) (source hash `5e0cadb3...`) is **superseded** by canonical deployment `0x310376D1c50cfD421bCa28Dfc3A8057253d7954f`.

---

## Live Consensus Paths Evidence

Each path below was executed on GenLayer Studio Next using `tests/integration/run_live.mjs` and verified with `tests/integration/verify_onchain.mjs`. Transaction IDs and state snapshots are recorded in [tests/integration/live-results.json](tests/integration/live-results.json) and [deploy/onchain-verify.json](deploy/onchain-verify.json).

| Scenario | Contract Address | Key Transaction IDs | Final State & Verification |
| :--- | :--- | :--- | :--- |
| **QUALIFYING** | [`0xDFFE30624166A0c5A7b7Bc41Aed0808a5C506FA4`](https://explorer-studio-dev.genlayer.com/address/0xDFFE30624166A0c5A7b7Bc41Aed0808a5C506FA4) | Resolve: [`0x5ed7fd2e401fe61a66d8d6959c6fe292d8646958147580820ccd2fa88abf0e31`](https://explorer-studio-dev.genlayer.com/tx/0x5ed7fd2e401fe61a66d8d6959c6fe292d8646958147580820ccd2fa88abf0e31)<br>Withdraw: [`0xe1eec344cd5ed5ef0b355e119c00cd3671a97850adc25a22bf753f0421cd6524`](https://explorer-studio-dev.genlayer.com/tx/0xe1eec344cd5ed5ef0b355e119c00cd3671a97850adc25a22bf753f0421cd6524)<br>Second Resolve: [`0x7c2b5c1f1c0f31b66405b620333730d2cf1b9b9f833b5662106d81a2cc9f9463`](https://explorer-studio-dev.genlayer.com/tx/0x7c2b5c1f1c0f31b66405b620333730d2cf1b9b9f833b5662106d81a2cc9f9463) | Classified `QUALIFYING` with citation. 59 min × $10^{15} = 0.059$ GEN credited to customer and withdrawn. Available = 0.041 GEN. Re-resolve reverted. Conservation OK. |
| **EXCLUDED** | [`0x36Cd12BF7f25D5CBFaB31Db77BDab4a30149EA06`](https://explorer-studio-dev.genlayer.com/address/0x36Cd12BF7f25D5CBFaB31Db77BDab4a30149EA06) | Resolve: [`0x0fcca81c5d95aa1e9f560ae20a7fa667b3e4891e8832cd5fbca7f26309d182bb`](https://explorer-studio-dev.genlayer.com/tx/0x0fcca81c5d95aa1e9f560ae20a7fa667b3e4891e8832cd5fbca7f26309d182bb)<br>Second Resolve: [`0x81cea5c7afcd27a41224d7849156a29e0f26a1de5a8fba9fedce8b9aeb3c8b19`](https://explorer-studio-dev.genlayer.com/tx/0x81cea5c7afcd27a41224d7849156a29e0f26a1de5a8fba9fedce8b9aeb3c8b19) | Classified `scheduled_maintenance`. Reservation released to `available` (0.1 GEN). Zero customer credit paid. Re-resolve reverted. Conservation OK. |
| **TIMEOUT** | [`0xF7ddecd7d7c119fBa7bd9664FAaBE3eeeFD19267`](https://explorer-studio-dev.genlayer.com/address/0xF7ddecd7d7c119fBa7bd9664FAaBE3eeeFD19267) | Timeout: [`0x9a8e5fc657d2fdb6a715e24cd0599a4b0a91c9d7cd350ff49ccbc95d3e2f0a68`](https://explorer-studio-dev.genlayer.com/tx/0x9a8e5fc657d2fdb6a715e24cd0599a4b0a91c9d7cd350ff49ccbc95d3e2f0a68)<br>Withdraw: [`0xa7475302b2d64ad458b59c6ef36eb5591a6a93ca190c2d211a1cb790305a6014`](https://explorer-studio-dev.genlayer.com/tx/0xa7475302b2d64ad458b59c6ef36eb5591a6a93ca190c2d211a1cb790305a6014)<br>Resolve After: [`0x74ee034f1fe32182d35a408a911c810084cd5c5850963376b04cb9e6c86c89eb`](https://explorer-studio-dev.genlayer.com/tx/0x74ee034f1fe32182d35a408a911c810084cd5c5850963376b04cb9e6c86c89eb) | Resolution window elapsed. Invoked timeout. Transitioned to `TIMEOUT_CREDIT`. Customer credited 0.059 GEN and withdrawn. Late resolve reverted. Conservation OK. |
| **SUNSET** | [`0xa9416e31E008a7AA1376f879D47Aa234F6379C31`](https://explorer-studio-dev.genlayer.com/address/0xa9416e31E008a7AA1376f879D47Aa234F6379C31) | Early Recover: [`0xa1d7b041c842839f51ce7651d52a6b3c9ed131a9ccea5c7e16b17a66d2dfd61a`](https://explorer-studio-dev.genlayer.com/tx/0xa1d7b041c842839f51ce7651d52a6b3c9ed131a9ccea5c7e16b17a66d2dfd61a)<br>Sunset Recover: [`0xd8333c97509f44ba3c6c6866a7d3b1dfdf583c1b5c55dfa824e8d35763eb87f7`](https://explorer-studio-dev.genlayer.com/tx/0xd8333c97509f44ba3c6c6866a7d3b1dfdf583c1b5c55dfa824e8d35763eb87f7)<br>Withdraw: [`0x8f0454d5f9fcb754c07aeecba06658ce78e8e43dadd692ba6bfabd2c3d205fdc`](https://explorer-studio-dev.genlayer.com/tx/0x8f0454d5f9fcb754c07aeecba06658ce78e8e43dadd692ba6bfabd2c3d205fdc)<br>Late Record: [`0x4dd79c65fdd0c743478c83832e6dfc4c76e07a6900cff9220160800aa5c47187`](https://explorer-studio-dev.genlayer.com/tx/0x4dd79c65fdd0c743478c83832e6dfc4c76e07a6900cff9220160800aa5c47187) | Early recovery before sunset reverted. After sunset, provider recovered unreserved funds (0.1 GEN) and withdrew (`returned` = 0.1 GEN). Late incident record reverted. Conservation OK. |

---

## Direct Tests and Verification Suite

### Commands

```bash
# 1. AST Linting
npm run lint
# Direct: genvm-lint lint contracts/incidentcredit.py --json

# 2. Python Direct Unit Tests (69 tests)
npm run test:direct
# Direct: python -m pytest tests/direct/ -v --tb=short

# 3. Read-Only On-Chain Verification
npm run verify:onchain
# Direct: node tests/integration/verify_onchain.mjs
```

### Direct Test Matrix

| Suite / Check | Command | Status |
| :--- | :--- | :--- |
| **GenVM Linter** | `npm run lint` | `{"ok":true,"passed":3}` |
| **Direct Unit Tests** | `npm run test:direct` | **69 passed** in ~6.5s |
| **On-Chain Code Match** | `npm run verify:onchain` | **MATCH** (`sourceMatch: true`, SHA-256 identical) |
| **Schema Validation** | `deploy/schema.json` | Validated against GenVM contract method signatures |
| **Live Qualifying Snapshot** | `tests/integration/live-results.json` | Verified (`ok: true`, 0.059 GEN withdrawn) |
| **Live Excluded Snapshot** | `tests/integration/live-results.json` | Verified (`ok: true`, 0.1 GEN released) |
| **Live Timeout Snapshot** | `tests/integration/live-results.json` | Verified (`ok: true`, customer credit awarded & withdrawn) |
| **Live Sunset Snapshot** | `tests/integration/live-results.json` | Verified (`ok: true`, early recovery rejected, sunset recovery passed) |

---

## File Directory & Linked Artifacts

- Smart Contract Source: [contracts/incidentcredit.py](contracts/incidentcredit.py)
- Direct Test Suite: [tests/direct/test_incidentcredit.py](tests/direct/test_incidentcredit.py)
- Live Scenario Snapshots: [tests/integration/live-results.json](tests/integration/live-results.json)
- On-Chain Verification Snapshot: [deploy/onchain-verify.json](deploy/onchain-verify.json)
- Contract ABI Schema: [deploy/schema.json](deploy/schema.json)
- Deployment Script: [deploy/deployScript.ts](deploy/deployScript.ts)
- Saved Fee Estimate: [deploy/fee-profile.json](deploy/fee-profile.json)
