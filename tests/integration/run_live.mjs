import { readFileSync, writeFileSync } from "node:fs";
import { createAccount, createClient } from "genlayer-js";
import { studioDevnet } from "genlayer-js/chains";

const ROOT = new URL("../../", import.meta.url);
const ENV_PATH = new URL("../../.env", import.meta.url);

const SLA =
  "Provider guarantees 99.9% monthly uptime for the production API. " +
  "Scheduled maintenance windows posted forty eight hours in advance are excluded. " +
  "Force majeure including regional power grid failure is excluded. " +
  "Outages caused by invalid customer credentials are excluded.";

const EXCLUSIONS = JSON.stringify([
  {
    id: "scheduled_maintenance",
    description: "Scheduled maintenance posted forty eight hours in advance",
  },
  {
    id: "force_majeure",
    description: "Regional power grid failure and other force majeure",
  },
  {
    id: "customer_caused",
    description: "Outages caused by invalid customer credentials",
  },
]);

const QUALIFYING_DESC =
  "API returned HTTP 500 for all /v1/orders requests due to a failed " +
  "database failover. No maintenance notice was posted.";
const EXCLUDED_DESC =
  "API was unavailable during the scheduled maintenance window posted " +
  "forty eight hours in advance for a cluster upgrade.";

const WEI_PER_MINUTE = 10n ** 15n;
const MAX_CREDIT = 10n ** 18n;
const FUND = 10n ** 17n; // 0.1 GEN per live deploy so four paths fit the provider balance
const ALLOWED = new Set([
  "QUALIFYING",
  "scheduled_maintenance",
  "force_majeure",
  "customer_caused",
]);

function loadEnv() {
  return Object.fromEntries(
    readFileSync(ENV_PATH, "utf8")
      .split(/\r?\n/)
      .filter((line) => line && !line.startsWith("#") && line.includes("="))
      .map((line) => {
        const i = line.indexOf("=");
        return [line.slice(0, i), line.slice(i + 1)];
      }),
  );
}

function jsonSafe(value) {
  if (typeof value === "bigint") return value.toString();
  if (Array.isArray(value)) return value.map(jsonSafe);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value)
        .filter(([, v]) => v !== undefined)
        .map(([k, v]) => [k, jsonSafe(v)]),
    );
  }
  return value;
}

const STATUS_BY_NUMBER = {
  0: "UNINITIALIZED",
  1: "PENDING",
  2: "PROPOSING",
  3: "COMMITTING",
  4: "REVEALING",
  5: "ACCEPTED",
  6: "UNDETERMINED",
  7: "FINALIZED",
  8: "CANCELED",
};

function txStatus(tx) {
  if (tx?.statusName) return String(tx.statusName);
  const raw = tx?.status;
  if (raw in STATUS_BY_NUMBER) return STATUS_BY_NUMBER[raw];
  if (STATUS_BY_NUMBER[Number(raw)]) return STATUS_BY_NUMBER[Number(raw)];
  return String(raw || "");
}

function txExecution(tx) {
  return String(tx?.txExecutionResultName || tx?.executionResult || "");
}

function isFinalized(tx) {
  return /finalized/i.test(txStatus(tx));
}

function isSuccessful(tx) {
  const exec = txExecution(tx).toLowerCase();
  if (!isFinalized(tx)) return false;
  if (!exec) return false;
  if (exec.includes("fail") || exec.includes("error") || exec.includes("revert") || exec.includes("rollback")) {
    return false;
  }
  return exec.includes("finished_with_return") || exec.includes("success");
}

function assertReceipt(tx, label, { expectSuccess = true } = {}) {
  const status = txStatus(tx);
  const exec = txExecution(tx);
  const ok = isSuccessful(tx);
  if (expectSuccess && !ok) {
    throw new Error(`${label} expected success, status=${status} execution=${exec}`);
  }
  if (!expectSuccess && ok) {
    throw new Error(`${label} expected execution error, status=${status} execution=${exec}`);
  }
  if (!isFinalized(tx)) {
    throw new Error(`${label} not finalized, status=${status} execution=${exec}`);
  }
}

function makeClient(key) {
  const account = createAccount(key);
  const client = createClient({
    chain: {
      ...studioDevnet,
      rpcUrls: { default: { http: ["https://studio-next.genlayer.com/api"] } },
    },
    endpoint: "https://studio-next.genlayer.com/api",
    account,
  });
  return { account, client };
}

async function waitTx(client, hash, retries = 120) {
  return client.waitForTransactionReceipt({
    hash,
    waitUntil: "finalized",
    retries,
    interval: 4000,
  });
}

async function withRetry(label, fn, attempts = 10) {
  let last;
  for (let i = 0; i < attempts; i++) {
    try {
      return await fn();
    } catch (err) {
      last = err;
      const msg = String(err);
      if (
        !/ETIMEDOUT|fetch failed|unknown RPC|socket|econnreset|network|ECONNREFUSED|ENOTFOUND|UND_ERR|gateway|timeout|502|503|504/i.test(
          msg,
        )
      ) {
        throw err;
      }
      const delay = Math.min(4000 * (i + 1), 15000);
      console.log("RETRY", label, i + 1, msg.replace(/\s+/g, " ").slice(0, 160));
      await new Promise((resolve) => setTimeout(resolve, delay));
    }
  }
  throw last;
}

async function sendWrite(client, fees, params) {
  const hash = await withRetry(`write ${params.functionName}`, () =>
    client.writeContract({ ...params, fees }),
  );
  const receipt = await withRetry(`wait ${params.functionName}`, () =>
    waitTx(client, hash),
  );
  return {
    hash,
    receipt,
    ok: isSuccessful(receipt),
    statusName: txStatus(receipt),
    execution: txExecution(receipt),
  };
}

function bountyOf(held) {
  const n = BigInt(held);
  if (n <= 0n) return 0n;
  let bounty = (n * 100n) / 10000n;
  if (bounty < 1n) bounty = 1n;
  if (bounty > n) bounty = n;
  return bounty;
}

function interval() {
  const now = Math.floor(Date.now() / 1000);
  return [now - 3600, now - 60];
}

function expectedCredit(start, end, reserved) {
  let minutes = Math.floor((end - start) / 60);
  let raw = BigInt(minutes) * WEI_PER_MINUTE;
  if (raw > MAX_CREDIT) raw = MAX_CREDIT;
  const held = BigInt(reserved);
  if (raw > held) raw = held;
  return { minutes, credit: raw };
}

async function deployCase(provider, fees, windows = {}) {
  const report = windows.report ?? 86400;
  const claim = windows.claim ?? 3600;
  const resolution = windows.resolution ?? 3600;
  const sunset = windows.sunset ?? 86400;
  const code = new Uint8Array(
    readFileSync(new URL("../../contracts/incidentcredit.py", import.meta.url)),
  );
  const hash = await withRetry("deployContract", () =>
    provider.client.deployContract({
      code,
      args: [
        provider.customerAddress,
        SLA,
        EXCLUSIONS,
        WEI_PER_MINUTE,
        MAX_CREDIT,
        report,
        claim,
        resolution,
        sunset,
      ],
      fees,
    }),
  );
  const receipt = await withRetry("wait deploy", () =>
    waitTx(provider.client, hash),
  );
  assertReceipt(receipt, "deploy", { expectSuccess: true });
  const address =
    receipt.txDataDecoded?.contractAddress ?? receipt.recipient;
  if (!address) throw new Error("deploy receipt missing address");
  return { hash, address, receipt };
}

async function sleepUntilUnix(unix, padSeconds = 2) {
  const now = Math.floor(Date.now() / 1000);
  const sec = unix - now + padSeconds;
  if (sec > 0) {
    console.log("SLEEP", sec, "s until", unix);
    await new Promise((resolve) => setTimeout(resolve, sec * 1000));
  }
}

async function runQualifying(ctx) {
  const { provider, customer, fees } = ctx;
  const deployed = await deployCase(provider, fees, { resolution: 3600 });
  console.log("QUALIFYING_ADDRESS", deployed.address);
  const addr = deployed.address;

  const fund = await sendWrite(provider.client, fees, {
    address: addr,
    functionName: "fund_reserve",
    args: [],
    value: FUND,
  });
  console.log("QUALIFYING_FUND", fund.hash, fund.ok);
  if (!fund.ok) throw new Error("fund_reserve failed");

  const [start, end] = interval();
  const record = await sendWrite(provider.client, fees, {
    address: addr,
    functionName: "record_incident",
    args: [start, end, QUALIFYING_DESC],
  });
  console.log("QUALIFYING_RECORD", record.hash, record.ok);
  if (!record.ok) {
    throw new Error(
      `record_incident failed ${record.statusName} ${record.execution}`,
    );
  }

  const claim = await sendWrite(customer.client, fees, {
    address: addr,
    functionName: "claim",
    args: ["0"],
  });
  console.log("QUALIFYING_CLAIM", claim.hash, claim.ok);
  if (!claim.ok) throw new Error("claim failed");

  const claimed = await provider.client.readContract({
    address: addr,
    functionName: "get_incident",
    args: ["0"],
  });
  const reserved = claimed.reserved_wei;

  const resolve = await sendWrite(provider.client, fees, {
    address: addr,
    functionName: "resolve",
    args: ["0"],
    consensusMaxRotations: 5,
  });
  console.log("QUALIFYING_RESOLVE", resolve.hash, resolve.ok);
  if (!resolve.ok) throw new Error("resolve failed");

  const incident = await provider.client.readContract({
    address: addr,
    functionName: "get_incident",
    args: ["0"],
  });
  const ledger = await provider.client.readContract({
    address: addr,
    functionName: "get_ledger",
    args: [],
  });
  console.log("QUALIFYING_INCIDENT", jsonSafe(incident));
  console.log("QUALIFYING_LEDGER", jsonSafe(ledger));

  if (!ledger.conservation_ok) throw new Error("conservation failed");
  if (!ALLOWED.has(incident.consequence)) {
    throw new Error(`unexpected consequence ${incident.consequence}`);
  }

  const note =
    incident.status === "QUALIFYING"
      ? null
      : `Studio classified as ${incident.status} / ${incident.consequence}`;
  let withdrawHash = null;
  let ledgerAfterWithdraw = null;
  if (incident.status === "QUALIFYING") {
    const exp = expectedCredit(start, end, reserved);
    if (incident.consequence !== "QUALIFYING") throw new Error("status/consequence mismatch");
    if (BigInt(incident.credit_wei) !== exp.credit) {
      throw new Error(`credit ${incident.credit_wei} != ${exp.credit}`);
    }
    if (BigInt(ledger.credited) !== exp.credit) {
      throw new Error(`resolve must credit on-contract, credited=${ledger.credited}`);
    }
    if (BigInt(ledger.withdrawn) !== 0n) {
      throw new Error("resolve must not auto-withdraw");
    }
    const wd = await sendWrite(customer.client, fees, {
      address: addr,
      functionName: "withdraw",
      args: [],
    });
    console.log("QUALIFYING_WITHDRAW", wd.hash, wd.ok);
    if (!wd.ok) throw new Error("withdraw failed");
    withdrawHash = wd.hash;
    ledgerAfterWithdraw = await provider.client.readContract({
      address: addr,
      functionName: "get_ledger",
      args: [],
    });
    if (BigInt(ledgerAfterWithdraw.credited) !== 0n) {
      throw new Error("credited should be 0 after withdraw");
    }
    if (BigInt(ledgerAfterWithdraw.withdrawn) !== exp.credit) {
      throw new Error(`withdrawn ${ledgerAfterWithdraw.withdrawn} != ${exp.credit}`);
    }
    if (!ledgerAfterWithdraw.conservation_ok) throw new Error("conservation after withdraw");
  }

  const second = await sendWrite(provider.client, fees, {
    address: addr,
    functionName: "resolve",
    args: ["0"],
  });
  assertReceipt(second.receipt, "second resolve", { expectSuccess: false });

  return {
    path: "qualifying",
    address: addr,
    deployHash: deployed.hash,
    fundHash: fund.hash,
    recordHash: record.hash,
    claimHash: claim.hash,
    resolveHash: resolve.hash,
    withdrawHash,
    secondResolveHash: second.hash,
    incident: jsonSafe(incident),
    ledger: jsonSafe(ledger),
    ledgerAfterWithdraw: jsonSafe(ledgerAfterWithdraw),
    honestNote: note,
  };
}

async function runExcluded(ctx) {
  const { provider, customer, fees } = ctx;
  const deployed = await deployCase(provider, fees, { resolution: 3600 });
  console.log("EXCLUDED_ADDRESS", deployed.address);
  const addr = deployed.address;

  const fund = await sendWrite(provider.client, fees, {
    address: addr,
    functionName: "fund_reserve",
    args: [],
    value: FUND,
  });
  if (!fund.ok) throw new Error("fund_reserve failed");

  const [start, end] = interval();
  const record = await sendWrite(provider.client, fees, {
    address: addr,
    functionName: "record_incident",
    args: [start, end, EXCLUDED_DESC],
  });
  if (!record.ok) {
    throw new Error(
      `record_incident failed ${record.statusName} ${record.execution}`,
    );
  }

  const claim = await sendWrite(customer.client, fees, {
    address: addr,
    functionName: "claim",
    args: ["0"],
  });
  if (!claim.ok) throw new Error("claim failed");

  const resolve = await sendWrite(provider.client, fees, {
    address: addr,
    functionName: "resolve",
    args: ["0"],
    consensusMaxRotations: 5,
  });
  console.log("EXCLUDED_RESOLVE", resolve.hash, resolve.ok);
  if (!resolve.ok) throw new Error("resolve failed");

  const incident = await provider.client.readContract({
    address: addr,
    functionName: "get_incident",
    args: ["0"],
  });
  const ledger = await provider.client.readContract({
    address: addr,
    functionName: "get_ledger",
    args: [],
  });
  console.log("EXCLUDED_INCIDENT", jsonSafe(incident));
  console.log("EXCLUDED_LEDGER", jsonSafe(ledger));

  if (!ledger.conservation_ok) throw new Error("conservation failed");
  if (!ALLOWED.has(incident.consequence)) {
    throw new Error(`unexpected consequence ${incident.consequence}`);
  }
  const note =
    incident.status === "EXCLUDED" &&
    incident.consequence === "scheduled_maintenance"
      ? null
      : `Studio classified as ${incident.status} / ${incident.consequence}`;

  const second = await sendWrite(provider.client, fees, {
    address: addr,
    functionName: "resolve",
    args: ["0"],
  });
  assertReceipt(second.receipt, "second resolve", { expectSuccess: false });

  return {
    path: "excluded",
    address: addr,
    deployHash: deployed.hash,
    fundHash: fund.hash,
    recordHash: record.hash,
    claimHash: claim.hash,
    resolveHash: resolve.hash,
    secondResolveHash: second.hash,
    incident: jsonSafe(incident),
    ledger: jsonSafe(ledger),
    honestNote: note,
  };
}

async function runTimeout(ctx) {
  const { provider, customer, fees } = ctx;
  const deployed = await deployCase(provider, fees, { resolution: 60 });
  console.log("TIMEOUT_ADDRESS", deployed.address);
  const addr = deployed.address;

  const fund = await sendWrite(provider.client, fees, {
    address: addr,
    functionName: "fund_reserve",
    args: [],
    value: FUND,
  });
  if (!fund.ok) throw new Error("fund_reserve failed");

  const [start, end] = interval();
  const record = await sendWrite(provider.client, fees, {
    address: addr,
    functionName: "record_incident",
    args: [start, end, QUALIFYING_DESC],
  });
  if (!record.ok) {
    throw new Error(
      `record_incident failed ${record.statusName} ${record.execution}`,
    );
  }

  const claim = await sendWrite(customer.client, fees, {
    address: addr,
    functionName: "claim",
    args: ["0"],
  });
  if (!claim.ok) throw new Error("claim failed");

  console.log("TIMEOUT_SLEEP 65s");
  await new Promise((resolve) => setTimeout(resolve, 65_000));

  const claimed = await provider.client.readContract({
    address: addr,
    functionName: "get_incident",
    args: ["0"],
  });
  const reservedWei = BigInt(claimed.reserved_wei);

  const timeout = await sendWrite(customer.client, fees, {
    address: addr,
    functionName: "timeout_unresolved",
    args: ["0"],
  });
  console.log("TIMEOUT_TX", timeout.hash, timeout.ok);
  if (!timeout.ok) throw new Error("timeout_unresolved failed");

  const incident = await provider.client.readContract({
    address: addr,
    functionName: "get_incident",
    args: ["0"],
  });
  const ledger = await provider.client.readContract({
    address: addr,
    functionName: "get_ledger",
    args: [],
  });
  console.log("TIMEOUT_INCIDENT", jsonSafe(incident));
  console.log("TIMEOUT_LEDGER", jsonSafe(ledger));

  if (incident.status !== "TIMEOUT") throw new Error(`status ${incident.status}`);
  if (incident.consequence !== "TIMEOUT_CREDIT") {
    throw new Error(`consequence ${incident.consequence}`);
  }
  if (incident.payout_marker !== "CONSUMED") throw new Error("marker");
  if (BigInt(incident.credit_wei) !== reservedWei) {
    throw new Error(`credit ${incident.credit_wei} != ${reservedWei}`);
  }
  if (!ledger.conservation_ok) throw new Error("conservation failed");
  if (BigInt(ledger.reserved) !== 0n) throw new Error("reserved not 0");
  if (BigInt(ledger.credited) !== reservedWei) {
    throw new Error(`credited ${ledger.credited} != ${reservedWei}`);
  }
  if (BigInt(ledger.available) !== FUND - reservedWei) {
    throw new Error("available should be FUND - reservedWei");
  }
  const customerAddr = customer.account.address ?? customer.account;
  const callerCredit = await provider.client.readContract({
    address: addr,
    functionName: "get_credit",
    args: [customerAddr],
  });
  if (BigInt(callerCredit) !== reservedWei) {
    throw new Error(`caller credit ${callerCredit} != ${reservedWei}`);
  }

  // Withdraw customer credit
  const wd = await sendWrite(customer.client, fees, {
    address: addr,
    functionName: "withdraw",
    args: [],
  });
  if (!wd.ok) throw new Error("customer withdraw failed");

  const ledgerAfterWithdraw = await provider.client.readContract({
    address: addr,
    functionName: "get_ledger",
    args: [],
  });
  if (BigInt(ledgerAfterWithdraw.withdrawn) !== reservedWei) {
    throw new Error("withdrawn != reservedWei");
  }
  if (BigInt(ledgerAfterWithdraw.credited) !== 0n) {
    throw new Error("credited != 0 after withdraw");
  }
  if (!ledgerAfterWithdraw.conservation_ok) throw new Error("conservation after withdraw failed");

  const resolve = await sendWrite(provider.client, fees, {
    address: addr,
    functionName: "resolve",
    args: ["0"],
  });
  assertReceipt(resolve.receipt, "resolve after timeout", { expectSuccess: false });

  return {
    path: "timeout",
    address: addr,
    deployHash: deployed.hash,
    fundHash: fund.hash,
    recordHash: record.hash,
    claimHash: claim.hash,
    timeoutHash: timeout.hash,
    withdrawHash: wd.hash,
    resolveAfterHash: resolve.hash,
    reserved: reservedWei.toString(),
    incident: jsonSafe(incident),
    ledger: jsonSafe(ledger),
    ledgerAfterWithdraw: jsonSafe(ledgerAfterWithdraw),
  };
}

async function runSunset(ctx) {
  const { provider, customer, fees } = ctx;
  const deployed = await deployCase(provider, fees, {
    report: 86400,
    claim: 60,
    resolution: 60,
    sunset: 360,
  });
  console.log("SUNSET_ADDRESS", deployed.address);
  const addr = deployed.address;

  const fund = await sendWrite(provider.client, fees, {
    address: addr,
    functionName: "fund_reserve",
    args: [],
    value: FUND,
  });
  if (!fund.ok) throw new Error("fund_reserve failed");

  // Verify early recover before sunset fails cleanly (tested right after fund when sunset is far away)
  const earlyRecover = await sendWrite(provider.client, fees, {
    address: addr,
    functionName: "recover_unreserved",
    args: [],
  });
  assertReceipt(earlyRecover.receipt, "recover before sunset", {
    expectSuccess: false,
  });

  const [start, end] = interval();
  const record = await sendWrite(provider.client, fees, {
    address: addr,
    functionName: "record_incident",
    args: [start, end, QUALIFYING_DESC],
  });
  if (!record.ok) {
    throw new Error(
      `record_incident failed ${record.statusName} ${record.execution}`,
    );
  }

  const cfg = await provider.client.readContract({
    address: addr,
    functionName: "get_config",
    args: [],
  });
  const lateAt = Number(cfg.sunset_ts) - 60 - 60 + 1;
  await sleepUntilUnix(lateAt, 1);

  const lateRecord = await sendWrite(provider.client, fees, {
    address: addr,
    functionName: "record_incident",
    args: [start - 7200, end - 7200, QUALIFYING_DESC + " too late."],
  });
  assertReceipt(lateRecord.receipt, "late record", { expectSuccess: false });

  const lateClaim = await sendWrite(customer.client, fees, {
    address: addr,
    functionName: "claim",
    args: ["0"],
  });
  assertReceipt(lateClaim.receipt, "late claim", { expectSuccess: false });

  const expire = await sendWrite(provider.client, fees, {
    address: addr,
    functionName: "expire_unclaimed",
    args: ["0"],
  });
  if (!expire.ok) throw new Error("expire_unclaimed failed");

  await sleepUntilUnix(Number(cfg.sunset_ts), 2);
  const recover = await sendWrite(provider.client, fees, {
    address: addr,
    functionName: "recover_unreserved",
    args: [],
  });
  if (!recover.ok) throw new Error("recover_unreserved failed");

  const ledger = await provider.client.readContract({
    address: addr,
    functionName: "get_ledger",
    args: [],
  });
  if (BigInt(ledger.available) !== 0n) throw new Error("available should be 0");
  if (BigInt(ledger.credited) !== FUND) {
    throw new Error("recover must credit provider, not auto-return");
  }
  if (BigInt(ledger.returned) !== 0n) throw new Error("returned must wait for withdraw");

  const wd = await sendWrite(provider.client, fees, {
    address: addr,
    functionName: "withdraw",
    args: [],
  });
  if (!wd.ok) throw new Error("provider withdraw failed");
  const after = await provider.client.readContract({
    address: addr,
    functionName: "get_ledger",
    args: [],
  });
  if (BigInt(after.returned) !== FUND) throw new Error("returned != funded");
  if (BigInt(after.credited) !== 0n) throw new Error("credited should be 0");
  if (!after.conservation_ok) throw new Error("conservation after recover withdraw");

  return {
    path: "sunset",
    address: addr,
    deployHash: deployed.hash,
    fundHash: fund.hash,
    recordHash: record.hash,
    lateRecordHash: lateRecord.hash,
    lateClaimHash: lateClaim.hash,
    expireHash: expire.hash,
    earlyRecoverHash: earlyRecover.hash,
    recoverHash: recover.hash,
    withdrawHash: wd.hash,
    ledger: jsonSafe(ledger),
    ledgerAfterWithdraw: jsonSafe(after),
  };
}

const runners = {
  qualifying: runQualifying,
  excluded: runExcluded,
  timeout: runTimeout,
  sunset: runSunset,
};

const scenario = process.argv[2] || "all";
const env = loadEnv();
const provider = makeClient(env.ACCOUNT_PRIVATE_KEY_1);
provider.customerAddress = env.INCIDENTCREDIT_CUSTOMER;
const customer = makeClient(env.ACCOUNT_PRIVATE_KEY_2);
const feesEstimate = await withRetry("estimateFees", () =>
  provider.client.estimateTransactionFees(),
);
const fees = {
  feeValue: feesEstimate.feeValue,
  distribution: feesEstimate.distribution,
};
const ctx = { provider, customer, fees };

const names =
  scenario === "all" ? ["qualifying", "excluded", "timeout", "sunset"] : [scenario];
const results = {};
let failed = false;
for (const name of names) {
  try {
    results[name] = await runners[name](ctx);
    results[name].ok = true;
  } catch (err) {
    failed = true;
    results[name] = { ok: false, error: String(err) };
    console.error(name, "FAILED", err);
  }
}

const outPath = new URL("./live-results.json", import.meta.url);
let previous = {};
try {
  previous = JSON.parse(readFileSync(outPath, "utf8"));
} catch {
  previous = {};
}
writeFileSync(
  outPath,
  JSON.stringify(jsonSafe({ ...previous, ...results }), null, 2),
);
console.log("WROTE", outPath.pathname);
if (failed) process.exit(1);
