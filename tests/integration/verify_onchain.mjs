import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { createAccount, createClient } from "genlayer-js";
import { studioDevnet } from "genlayer-js/chains";

const env = Object.fromEntries(
  readFileSync(new URL("../../.env", import.meta.url), "utf8")
    .split(/\r?\n/)
    .filter((line) => line && !line.startsWith("#") && line.includes("="))
    .map((line) => {
      const i = line.indexOf("=");
      return [line.slice(0, i), line.slice(i + 1)];
    }),
);

const account = createAccount(env.ACCOUNT_PRIVATE_KEY_1);
const client = createClient({
  chain: {
    ...studioDevnet,
    rpcUrls: { default: { http: ["https://studio-next.genlayer.com/api"] } },
  },
  endpoint: "https://studio-next.genlayer.com/api",
  account,
});

const jsonSafe = (value) => {
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
};

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
      const delay = Math.min(3000 * (i + 1), 12000);
      await new Promise((resolve) => setTimeout(resolve, delay));
    }
  }
  throw last;
}

function sha256Text(text) {
  return createHash("sha256").update(Buffer.from(text, "utf8")).digest("hex");
}

function normalizeSource(text) {
  return String(text)
    .replace(/\u001b\[[0-9;]*m/g, "")
    .replace(/\r\n/g, "\n")
    .replace(/^\s*[-\\|/]\s+Getting code[^\n]*\n/g, "")
    .replace(/\n+$/, "\n");
}

const localRaw = readFileSync(new URL("../../contracts/incidentcredit.py", import.meta.url));
const localText = localRaw.toString("utf8").replace(/\r\n/g, "\n");
const localSha = sha256Text(localText);

const chainIdHex = await withRetry("eth_chainId", () =>
  client.request({ method: "eth_chainId" }),
);
const chainId = Number.parseInt(chainIdHex, 16);

let deployRecord = {};
try {
  deployRecord = JSON.parse(
    readFileSync(new URL("../../deploy-result.json", import.meta.url), "utf8"),
  );
} catch {
  deployRecord = {};
}

const canonical =
  deployRecord.contractAddress || "0x310376D1c50cfD421bCa28Dfc3A8057253d7954f";
const deployedText = normalizeSource(
  await withRetry("getContractCode", () => client.getContractCode(canonical)),
);
const deployedSha = sha256Text(deployedText);
const sourceMatch =
  deployedText === localText || deployedText.trimEnd() === localText.trimEnd();

const feePolicy = jsonSafe(
  await withRetry("getCurrentFeePolicy", () => client.getCurrentFeePolicy()),
);
const estimate = jsonSafe(
  await withRetry("estimateTransactionFees", () => client.estimateTransactionFees()),
);

async function readAddr(address, { readIncident = true } = {}) {
  if (!address) {
    return { address: null, available: false, error: "No address recorded" };
  }
  const out = { address, available: true };
  for (const method of ["get_ledger", "get_incident_count", "get_config"]) {
    try {
      out[method] = jsonSafe(
        await client.readContract({ address, functionName: method, args: [] }),
      );
    } catch (err) {
      out[method] = { error: String(err).slice(0, 300) };
    }
  }
  const count = Number(out.get_incident_count || 0);
  if (readIncident && count > 0) {
    try {
      out.incident0 = jsonSafe(
        await client.readContract({
          address,
          functionName: "get_incident",
          args: ["0"],
        }),
      );
    } catch (err) {
      out.incident0 = { error: String(err).slice(0, 300) };
    }
  }
  return out;
}

let live = {};
try {
  live = JSON.parse(
    readFileSync(new URL("./live-results.json", import.meta.url), "utf8"),
  );
} catch {
  live = {};
}

const report = {
  verifiedAt: new Date().toISOString(),
  rpc: "https://studio-next.genlayer.com/api",
  chainId,
  localBytes: localRaw.length,
  localSha256: localSha,
  deployedBytes: Buffer.byteLength(deployedText, "utf8"),
  deployedSha256: deployedSha,
  sourceMatch,
  feePolicy,
  estimate,
  canonical: await readAddr(canonical, { readIncident: false }),
  supersededCanonical: "0x6b1Ce0B7bb94b978fd3CE0fD5981119D8dcCA1b0",
  qualifying: await readAddr(live.qualifying?.address),
  excluded: await readAddr(live.excluded?.address),
  timeout: await readAddr(live.timeout?.address),
  sunset: await readAddr(live.sunset?.address),
};

writeFileSync(
  new URL("../../deploy/onchain-verify.json", import.meta.url),
  JSON.stringify(report, null, 2),
);
writeFileSync(
  new URL("../../deploy/fee-profile.json", import.meta.url),
  JSON.stringify(
    {
      rpc: report.rpc,
      chainId,
      feePolicy,
      estimate,
    },
    null,
    2,
  ),
);

if (!sourceMatch) {
  console.error("SOURCE_MISMATCH", { localSha, deployedSha });
  process.exit(1);
}
console.log(
  JSON.stringify(
    {
      chainId,
      localSha256: localSha,
      deployedSha256: deployedSha,
      sourceMatch,
      feeValue: estimate?.feeValue,
      policyEnabled: feePolicy?.enabled,
      canonicalLedger: report.canonical.get_ledger,
      qualifying: {
        status: report.qualifying?.incident0?.status,
        consequence: report.qualifying?.incident0?.consequence,
        ledger: report.qualifying?.get_ledger,
      },
      excluded: {
        status: report.excluded?.incident0?.status,
        consequence: report.excluded?.incident0?.consequence,
        ledger: report.excluded?.get_ledger,
      },
      timeout: {
        status: report.timeout?.incident0?.status,
        consequence: report.timeout?.incident0?.consequence,
        ledger: report.timeout?.get_ledger,
      },
      sunset: {
        ledger: report.sunset?.get_ledger,
      },
    },
    null,
    2,
  ),
);
