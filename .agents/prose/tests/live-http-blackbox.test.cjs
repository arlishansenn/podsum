"use strict";

// #26 验收测试：刻意通过 HTTP 驱动 `reactor serve` 和一次性 state 目录；
// 不挂载 SDK DAG，也不伪造 render。
const assert = require("node:assert/strict");
const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const net = require("node:net");
const { createFileSystemWorldModelStore } = require("@openprose/reactor");

const projectRoot = path.resolve(__dirname, "..");
const sourceDir = path.join(projectRoot, "src");
const reactor = path.join(projectRoot, "node_modules", ".bin", "reactor");
const devtools = path.join(projectRoot, "node_modules", ".bin", "reactor-devtools");
const gateway = "email-ingress-gateway";
const responsibility = "77CTSX38RYPEE7AM92CMCGE5W3";
const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "podsum-reactor-http-"));
const caseDir = path.join(stateDir, "project");
const childEnvironment = { ...process.env, OPENAI_AGENTS_DISABLE_TRACING: "1" };
let serve;
let servePort;

function run(bin, args, { json = false } = {}) {
  const result = spawnSync(bin, args, {
    cwd: caseDir,
    encoding: "utf8",
    env: childEnvironment,
    timeout: 300_000,
  });
  assert.equal(result.status, 0, `${path.basename(bin)} ${args.join(" ")} failed:\n${result.stderr}\n${result.stdout}`);
  return json ? JSON.parse(result.stdout) : result.stdout;
}

function writeProject() {
  fs.mkdirSync(path.join(caseDir, "src"), { recursive: true });
  for (const name of fs.readdirSync(sourceDir).filter((file) => file.startsWith("bootstrap-") && file.endsWith(".prose.md"))) {
    fs.copyFileSync(path.join(sourceDir, name), path.join(caseDir, "src", name));
  }
  fs.writeFileSync(path.join(caseDir, "reactor.yml"), `state:
  dir: ${JSON.stringify(path.join(stateDir, "runtime"))}
model:
  provider: openai
  api_key_env: OPENAI_API_KEY
  base_url: http://100.64.0.5:4000/v1
  render_model: newapi/gpt-5.5
  compile_model: newapi/gpt-5.5
  max_turns: 8
sandbox:
  mode: none
  shell_timeout_ms: 300000
gateways:
  - node: ${gateway}
    source_id: email-bootstrap
    connector:
      type: static
      id_field: event_id
      items: []
reactors: []
`);
}

function unusedPort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close((error) => (error ? reject(error) : resolve(port)));
    });
  });
}

function request(port, method, pathname, body) {
  return new Promise((resolve, reject) => {
    const data = body === undefined ? undefined : JSON.stringify(body);
    const req = http.request({
      host: "127.0.0.1",
      port,
      method,
      path: pathname,
      headers: data === undefined ? undefined : {
        "content-type": "application/json",
        "content-length": Buffer.byteLength(data),
      },
    }, (res) => {
      let chunks = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => { chunks += chunk; });
      res.on("end", () => {
        try {
          resolve({ status: res.statusCode, body: JSON.parse(chunks) });
        } catch (error) {
          reject(new Error(`invalid JSON from ${pathname}: ${chunks}\n${error.message}`));
        }
      });
    });
    req.once("error", reject);
    if (data !== undefined) req.write(data);
    req.end();
  });
}

async function waitForHealth(port) {
  let lastError;
  for (let attempt = 0; attempt < 120; attempt += 1) {
    try {
      const health = await request(port, "GET", "/health");
      if (health.status === 200 && health.body.ok === true) return;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`reactor serve did not become healthy: ${lastError}`);
}

function receipts(port) {
  return request(port, "GET", "/receipts").then((response) => {
    assert.equal(response.status, 200);
    return response.body.receipts;
  });
}

function latest(receiptList, node) {
  const found = receiptList.filter((receipt) => receipt.node === node).at(-1);
  assert.ok(found, `expected a receipt for ${node}`);
  return found;
}

function publishedJsonSummary(node) {
  try {
    const store = createFileSystemWorldModelStore({ directory: path.join(stateDir, "runtime", "world-models") });
    return Object.entries(store.read(node).files).flatMap(([file, value]) => {
      try {
        const parsed = JSON.parse(Buffer.from(value).toString("utf8"));
        return [{ file, keys: parsed && typeof parsed === "object" && !Array.isArray(parsed) ? Object.keys(parsed).sort() : [] }];
      } catch {
        return [];
      }
    });
  } catch {
    return [];
  }
}

function artifactJson(node, expected) {
  const store = createFileSystemWorldModelStore({ directory: path.join(stateDir, "runtime", "world-models") });
  const files = store.read(node).files;
  for (const value of Object.values(files)) {
    try {
      const parsed = JSON.parse(Buffer.from(value).toString("utf8"));
      if (Object.entries(expected).every(([key, expectedValue]) => parsed[key] === expectedValue)) return parsed;
    } catch {
      // world-model 也可能包含派生的 Markdown projection。
    }
  }
  assert.fail(`${node} published no structured JSON matching expected fields; published JSON filenames/keys: ${JSON.stringify(publishedJsonSummary(node))}`);
}

function safeReceiptSummary(receipt) {
  const tokens = receipt?.cost?.tokens;
  return {
    node: receipt.node,
    status: typeof receipt?.status === "string" ? receipt.status : null,
    cost: {
      fresh: typeof tokens?.fresh === "number" ? tokens.fresh : null,
      reused: typeof tokens?.reused === "number" ? tokens.reused : null,
    },
    fingerprints: Object.fromEntries(["incoming", "@atomic"].flatMap((key) =>
      typeof receipt?.fingerprints?.[key] === "string" ? [[key, receipt.fingerprints[key]]] : [])),
  };
}

async function safeFailureDiagnostics(port) {
  let receiptList = [];
  try { receiptList = await receipts(port); } catch {}
  return {
    receipts: receiptList.filter((receipt) => receipt?.node === gateway || receipt?.node === responsibility).map(safeReceiptSummary),
    published_json: [gateway, responsibility].map((node) => ({ node, files: publishedJsonSummary(node) })),
  };
}

async function stopServe() {
  if (!serve || serve.exitCode !== null) return;
  serve.kill("SIGTERM");
  await new Promise((resolve) => serve.once("exit", resolve));
}

(async () => {
  assert.ok(process.env.OPENAI_API_KEY, "OPENAI_API_KEY is required for the real live Reactor acceptance test");
  writeProject();

  const doctor = run(reactor, ["--project", caseDir, "doctor", "--live", "--json"], { json: true });
  assert.equal(doctor.healthyForLive, true, "doctor must validate the configured live provider");
  run(reactor, ["--project", caseDir, "compile", "--force", "--json"], { json: true });
  const topology = run(reactor, ["--project", caseDir, "topology", "--json"], { json: true });
  assert.deepEqual(topology.edges, [{ subscriber: responsibility, producer: gateway, facet: "incoming" }]);

  const port = await unusedPort();
  servePort = port;
  serve = spawn(reactor, ["--project", caseDir, "serve", "--http", String(port), "--poll-interval", "60000"], {
    cwd: caseDir,
    env: childEnvironment,
    stdio: "ignore",
  });
  await waitForHealth(port);

  const firstPayload = { event_id: "evt-http-1", message: "first material payload" };
  const first = await request(port, "POST", `/trigger/${gateway}`, firstPayload);
  assert.equal(first.status, 200);
  assert.equal(first.body.dataDelivered, true);
  const afterFirst = await receipts(port);
  const firstGateway = latest(afterFirst, gateway);
  const firstResponsibility = latest(afterFirst, responsibility);
  assert.equal(firstGateway.status, "rendered");
  assert.equal(firstResponsibility.status, "rendered");
  assert.ok(firstGateway.fingerprints.incoming, "Gateway must publish the subscribed incoming facet");
  artifactJson(gateway, firstPayload);
  artifactJson(responsibility, { snapshot_event_id: firstPayload.event_id, snapshot_message: firstPayload.message });

  const downstreamFingerprint = firstResponsibility.fingerprints["@atomic"];
  const responsibilityReceiptsBeforeRepeat = afterFirst.filter((receipt) => receipt.node === responsibility).length;
  const repeat = await request(port, "POST", `/trigger/${gateway}`, firstPayload);
  assert.equal(repeat.status, 200);
  const afterRepeat = await receipts(port);
  const repeatedGateway = latest(afterRepeat, gateway);
  assert.ok(["rendered", "skipped"].includes(repeatedGateway.status));
  const responsibilityReceiptsAfterRepeat = afterRepeat.filter((receipt) => receipt.node === responsibility);
  assert.equal(responsibilityReceiptsAfterRepeat.length, responsibilityReceiptsBeforeRepeat,
    "identical material input must not wake the downstream Responsibility");
  assert.equal(latest(afterRepeat, responsibility).fingerprints["@atomic"], downstreamFingerprint);

  const changedPayload = { event_id: "evt-http-2", message: "changed material payload" };
  const changed = await request(port, "POST", `/trigger/${gateway}`, changedPayload);
  assert.equal(changed.status, 200);
  const afterChanged = await receipts(port);
  const changedGateway = latest(afterChanged, gateway);
  const changedResponsibility = latest(afterChanged, responsibility);
  assert.equal(changedGateway.status, "rendered");
  assert.equal(changedResponsibility.status, "rendered");
  assert.notEqual(changedGateway.fingerprints.incoming, firstGateway.fingerprints.incoming);
  assert.notEqual(changedResponsibility.fingerprints["@atomic"], downstreamFingerprint);
  artifactJson(gateway, changedPayload);
  artifactJson(responsibility, { snapshot_event_id: changedPayload.event_id, snapshot_message: changedPayload.message });

  const verify = run(reactor, ["--project", caseDir, "receipts", "verify", "--json"], { json: true });
  assert.equal(verify.ok, true, "receipt chains must verify");
  const replay = run(devtools, [path.join(stateDir, "runtime"), "--describe", "--json"], { json: true });
  assert.equal(replay.chainVerify.ok, true, "DevTools must replay the live receipt ledger");

  await stopServe();
  console.log("live HTTP black box: first=rendered repeat=downstream-memo-skip changed=rendered chains=valid devtools=replayed");
})().catch(async () => {
  console.error("#26 live HTTP black box failed; safe diagnostics follow:");
  console.error(JSON.stringify(await safeFailureDiagnostics(servePort)));
  await stopServe();
  process.exitCode = 1;
}).finally(() => {
  fs.rmSync(stateDir, { recursive: true, force: true });
});
