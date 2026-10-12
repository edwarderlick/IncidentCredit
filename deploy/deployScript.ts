import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { createAccount, createClient } from "genlayer-js";
import { studioDevnet } from "genlayer-js/chains";

const ROOT = new URL("../", import.meta.url);
const RPC = "https://studio-next.genlayer.com/api";
const CHAIN_ID = 61997;

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

const WEI_PER_MINUTE = 10n ** 15n;
const MAX_CREDIT = 10n ** 18n;
const REPORT_WINDOW = 86_400;
const CLAIM_WINDOW = 3_600;
const RESOLUTION_WINDOW = 3_600;
const SUNSET = 2_592_000;

function loadEnv() {
  return Object.fromEntries(
    readFileSync(new URL("../.env", import.meta.url), "utf8")
      .split(/\r?\n/)
      .filter((line) => line && !line.startsWith("#") && line.includes("="))
      .map((line) => {
        const i = line.indexOf("=");
        return [line.slice(0, i), line.slice(i + 1)];
      }),
  );
}

function jsonSafe(value: unknown): unknown {
  if (typeof value === "bigint") return value.toString();
  if (Array.isArray(value)) return value.map(jsonSafe);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .filter(([, v]) => v !== undefined)
        .map(([k, v]) => [k, jsonSafe(v)]),
    );
  }
  return value;
}

const STATUS_BY_NUMBER: Record<number, string> = {
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

function txStatus(tx: any): string {
  if (tx?.statusName) return String(tx.statusName);
  const raw = tx?.status;
  if (typeof raw === "number" && STATUS_BY_NUMBER[raw]) return STATUS_BY_NUMBER[raw];
  if (STATUS_BY_NUMBER[Number(raw)]) return STATUS_BY_NUMBER[Number(raw)];
  return String(raw || "");
}

function txExecution(tx: any): string {
  return String(tx?.txExecutionResultName || tx?.executionResult || "");
}

function assertDeployReceipt(tx: any) {
  const status = txStatus(tx);
  const exec = txExecution(tx);
  if (!/finalized/i.test(status)) {
    throw new Error(`deploy not finalized: status=${status} execution=${exec}`);
  }
  const execLc = exec.toLowerCase();
  if (!execLc.includes("finished_with_return")) {
    throw new Error(
      `deploy execution was not FINISHED_WITH_RETURN: status=${status} execution=${exec}`,
    );
  }
}

function sha256Text(text: string): string {
  return createHash("sha256").update(Buffer.from(text, "utf8")).digest("hex");
}

function normalizeSource(text: string): string {
  return String(text)
    .replace(/\u001b\[[0-9;]*m/g, "")
    .replace(/\r\n/g, "\n")
    .replace(/\n+$/, "\n");
}

function makeClient(privateKey: string) {
  const account = createAccount(privateKey);
  const client = createClient({
    chain: {
      ...studioDevnet,
      rpcUrls: { default: { http: [RPC] } },
    },
    endpoint: RPC,
    account,
  });
  return { account, client };
}

async function withRetry<T>(label: string, fn: () => Promise<T>, attempts = 10): Promise<T> {
  let last: unknown;
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

export default async function main(injected?: any) {
  const env = loadEnv();
  const customer = env.INCIDENTCREDIT_CUSTOMER;
  if (!customer) {
    throw new Error("INCIDENTCREDIT_CUSTOMER must be set");
  }

  let client = injected;
  if (!client || typeof client.estimateTransactionFees !== "function") {
    const built = makeClient(env.ACCOUNT_PRIVATE_KEY_1);
    client = built.client;
  }

  const chainIdHex = await withRetry("eth_chainId", () =>
    client.request({ method: "eth_chainId" }),
  );
  const chainId = Number.parseInt(chainIdHex, 16);
  if (chainId !== CHAIN_ID) {
    throw new Error(`expected chain ${CHAIN_ID}, got ${chainId}`);
  }

  const sla = env.INCIDENTCREDIT_SLA || SLA;
  const exclusions = env.INCIDENTCREDIT_EXCLUSIONS || EXCLUSIONS;
  const weiPerMinute = BigInt(env.INCIDENTCREDIT_WEI_PER_MINUTE || WEI_PER_MINUTE.toString());
  const maxCredit = BigInt(env.INCIDENTCREDIT_MAX_CREDIT || MAX_CREDIT.toString());
  const reportWindow = Number(env.INCIDENTCREDIT_REPORT_WINDOW || REPORT_WINDOW);
  const claimWindow = Number(env.INCIDENTCREDIT_CLAIM_WINDOW || CLAIM_WINDOW);
  const resolutionWindow = Number(
    env.INCIDENTCREDIT_RESOLUTION_WINDOW || RESOLUTION_WINDOW,
  );
  const sunset = Number(env.INCIDENTCREDIT_SUNSET || SUNSET);

  const sourceBytes = readFileSync(new URL("../contracts/incidentcredit.py", import.meta.url));
  const sourceText = sourceBytes.toString("utf8").replace(/\r\n/g, "\n");
  const sourceSha256 = sha256Text(sourceText);

  const estimate = await withRetry("estimateTransactionFees", () =>
    client.estimateTransactionFees(),
  );
  const fees = {
    feeValue: estimate.feeValue,
    distribution: estimate.distribution,
  };

  const deployHash = await withRetry("deployContract", () =>
    client.deployContract({
      code: new Uint8Array(sourceBytes),
      args: [
        customer,
        sla,
        exclusions,
        weiPerMinute,
        maxCredit,
        reportWindow,
        claimWindow,
        resolutionWindow,
        sunset,
      ],
      fees,
    }),
  );
  console.log("DEPLOY_HASH", deployHash);

  const deployTx = await withRetry("waitForTransactionReceipt", () =>
    client.waitForTransactionReceipt({
      hash: deployHash,
      waitUntil: "finalized",
      retries: 120,
      interval: 4000,
    }),
  );
  assertDeployReceipt(deployTx);

  const contractAddress =
    deployTx.txDataDecoded?.contractAddress ?? deployTx.recipient;
  if (!contractAddress) {
    throw new Error("Finalized deployment has no contract address");
  }

  const deployedText = normalizeSource(
    await withRetry("getContractCode", () => client.getContractCode(contractAddress)),
  );
  const deployedSha256 = sha256Text(deployedText);
  const sourceMatch =
    deployedText === sourceText || deployedText.trimEnd() === sourceText.trimEnd();
  if (!sourceMatch) {
    throw new Error(
      `deployed source mismatch local=${sourceSha256} onchain=${deployedSha256}`,
    );
  }

  const record = {
    rpc: RPC,
    chainId,
    contractAddress,
    deployHash,
    customer,
    provider: client.account?.address,
    weiPerMinute: weiPerMinute.toString(),
    maxCredit: maxCredit.toString(),
    reportWindow,
    claimWindow,
    resolutionWindow,
    sunset,
    statusName: txStatus(deployTx),
    execution: txExecution(deployTx),
    sourceSha256,
    deployedSha256,
    sourceMatch,
    fees: jsonSafe(fees),
    explorer: `https://explorer-studio-dev.genlayer.com/address/${contractAddress}`,
  };
  writeFileSync(
    new URL("../deploy-result.json", import.meta.url),
    JSON.stringify(record, null, 2),
  );
  console.log("IncidentCredit deployed", record);
  return contractAddress;
}

const isDirect =
  Boolean(process.argv[1]) &&
  String(process.argv[1]).replaceAll("\\", "/").endsWith("deployScript.ts");
if (isDirect) {
  main().catch((err) => {
    console.error(err);
    process.exit(1);
  });
}
