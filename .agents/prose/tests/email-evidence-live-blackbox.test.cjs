"use strict";

// #27 真实黑盒：只复制 evidence contracts，以 HTTP 驱动 Reactor 与 Workbench。
const assert = require("node:assert/strict");
const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");

const proseRoot = path.resolve(__dirname, "..");
const repositoryRoot = path.resolve(proseRoot, "..", "..");
const daemon = path.join(proseRoot, "src/evidence-reactor-daemon.cjs");
const devtools = path.join(proseRoot, "node_modules/.bin/reactor-devtools");
const projectPython = process.env.PODSUM_PYTHON || path.join(os.homedir(), "Library/Application Support/Podsum/.venv/bin/python");
const gateway = "email-evidence-gateway";
const responsibility = "5ZPQV9NQVE4W4FR40F9XSCJ7TW";
const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), "podsum-evidence-reactor-"));
const project = path.join(stateDir, "project");
const runtime = path.join(stateDir, "runtime");
const ledger = path.join(stateDir, "downloads/EmailReports/email-evidence-ledger.json");
// daemon 首次启动会真实 compileProject；它不是 ingress 运行期请求，最多允许 300 秒就绪。
const DAEMON_STARTUP_READINESS_TIMEOUT_MS = 300_000;
const INGRESS_SUBPROCESS_TIMEOUT_MS = 60_000;
const childEnvironment = {
  ...process.env,
  OPENAI_AGENTS_DISABLE_TRACING: "1",
  PYTHONPATH: path.join(repositoryRoot, "outputs"),
};
let reactorServe;
let workbench;
let activeReactorPort;
let output = "";

function command(binary, argumentsList, workingDirectory) {
  const result = spawnSync(binary, argumentsList, {
    cwd: workingDirectory,
    encoding: "utf8",
    env: childEnvironment,
    timeout: 300000,
  });
  assert.equal(result.status, 0, `${binary} ${argumentsList.join(" ")}\n${result.stderr}\n${result.stdout}`);
  return result.stdout;
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function port() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const value = server.address().port;
      server.close((error) => (error ? reject(error) : resolve(value)));
    });
  });
}

function request(portNumber, method, pathname, body, timeoutMs) {
  return new Promise((resolve, reject) => {
    const data = body === undefined ? undefined : JSON.stringify(body);
    const requestOptions = {
      host: "127.0.0.1",
      port: portNumber,
      method,
      path: pathname,
      agent: false,
      headers: data === undefined ? undefined : {
        "content-type": "application/json",
        "content-length": Buffer.byteLength(data),
      },
    };
    const requestHandle = http.request(requestOptions, (response) => {
      let text = "";
      response.setEncoding("utf8");
      response.on("data", (chunk) => { text += chunk; });
      response.on("end", () => {
        try {
          resolve({ status: response.statusCode, body: JSON.parse(text) });
        } catch (error) {
          reject(new Error(`${pathname}: ${text}\n${error.message}`));
        }
      });
    });
    requestHandle.once("error", reject);
    if (timeoutMs !== undefined) requestHandle.setTimeout(timeoutMs, () => requestHandle.destroy(new Error(`${pathname}: HTTP timeout`)));
    if (data !== undefined) requestHandle.write(data);
    requestHandle.end();
  });
}

async function health(portNumber, pathname) {
  let lastError;
  for (let attempt = 0; attempt < 120; attempt += 1) {
    try {
      const response = await request(portNumber, "GET", pathname, undefined);
      if (response.status === 200) return;
    } catch (error) {
      lastError = error;
    }
    await delay(250);
  }
  throw lastError || new Error("health timeout");
}

function daemonExitError(child) {
  return new Error(`daemon 在 startup readiness 前退出（exit code: ${child.exitCode ?? "null"}，signal: ${child.signalCode ?? "none"}）\nCaptured stdout:\n${child.capturedStdout}\nCaptured stderr:\n${child.capturedStderr}`);
}

async function daemonStartupReadiness(portNumber, child) {
  const startedAt = Date.now();
  const deadline = startedAt + DAEMON_STARTUP_READINESS_TIMEOUT_MS;
  let lastError;
  while (Date.now() < deadline) {
    if (child.exitCode !== null || child.signalCode !== null) throw daemonExitError(child);
    try {
      const response = await request(portNumber, "GET", "/health", undefined, deadline - Date.now());
      if (response.status === 200) return Date.now() - startedAt;
      lastError = new Error(`/health 返回 ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    if (child.exitCode !== null || child.signalCode !== null) throw daemonExitError(child);
    await delay(Math.min(250, deadline - Date.now()));
  }
  if (child.exitCode !== null || child.signalCode !== null) throw daemonExitError(child);
  throw new Error(`daemon startup readiness 超过 ${DAEMON_STARTUP_READINESS_TIMEOUT_MS / 1000} 秒：${lastError?.message ?? "health timeout"}`);
}

async function stop(child) {
  if (!child || child.exitCode !== null) return;
  child.kill("SIGTERM");
  const terminated = await Promise.race([
    new Promise((resolve) => child.once("exit", resolve)),
    delay(5000).then(() => false),
  ]);
  if (terminated === false && child.exitCode === null) {
    child.kill("SIGKILL");
    await new Promise((resolve) => child.once("exit", resolve));
  }
}

function artifact(uid, subject) {
  return {
    object_type: "email_evidence_pack",
    object_version: "1",
    status: "ready_for_summary",
    date: "2026-07-14",
    account: "sanitized@example.test",
    window: "daily",
    scan_limit: 10,
    raw_count: 1,
    possibly_truncated: false,
    items: [{
      uid,
      date: "2026-07-14",
      from: "sender@example.test",
      subject,
      snippet: "Sanitized evidence only.",
      evidence: [{
        type: "public_link",
        uid,
        url: "https://example.test/evidence",
        status: "fetched",
        excerpt: "Sanitized excerpt.",
      }],
    }],
  };
}

function writeProject() {
  fs.mkdirSync(path.join(project, "src"), { recursive: true });
  // #27 仅隔离验证两个 checked-in evidence modules，不声称覆盖 production full host。
  for (const name of ["email-evidence-gateway.prose.md", "email-evidence-responsibility.prose.md"]) {
    fs.copyFileSync(path.join(proseRoot, "src", name), path.join(project, "src", name));
  }
}

async function receipts(portNumber) {
  const response = await request(portNumber, "GET", "/receipts", undefined);
  assert.equal(response.status, 200);
  return response.body.receipts;
}

function latestEvidenceReceipt(allReceipts) {
  return allReceipts.filter((receipt) => receipt.node === responsibility && receipt.fingerprints?.evidence_pack_via).at(-1);
}

async function waitForVia(portNumber, fingerprint) {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const response = await request(portNumber, "GET", "/via/email-evidence-pack");
    const allReceipts = await receipts(portNumber);
    const receipt = latestEvidenceReceipt(allReceipts);
    if (response.status === 200 && receipt && response.body.evidence_ledger?.material_fingerprint === fingerprint) {
      assert.equal(response.body.evidence_ledger.material_fingerprint, fingerprint);
      assert.equal(response.body.via_fingerprint, receipt.fingerprints.evidence_pack_via, "GET VIA fingerprint must be the latest named Evidence receipt facet");
      return { via: response.body, allReceipts, receipt };
    }
    await delay(500);
  }
  throw new Error("Responsibility 未发布期望的 evidence_pack_via");
}

function invokeIngress(file, endpoint) {
  const argumentsList = [path.join(repositoryRoot, "outputs/email/evidence_ingress.py"), file, ledger, endpoint];
  const result = spawnSync(projectPython, argumentsList, {
    cwd: repositoryRoot,
    encoding: "utf8",
    env: childEnvironment,
    // 60 秒测试边界必须大于 evidence_ingress.py 的 30 秒 HTTP 等待边界，避免客户端先于 ingress 超时。
    timeout: INGRESS_SUBPROCESS_TIMEOUT_MS,
  });
  assert.equal(result.status, 0, `${projectPython} ${argumentsList.join(" ")}\n${result.stderr}\n${result.stdout}`);
  return JSON.parse(result.stdout);
}

function startReactor(portNumber) {
  const child = spawn(process.execPath, [daemon, "--contracts", path.join(project, "src"), "--state", runtime, "--ledger", ledger, "--delivery-mode", "file", "--delivery-outbox", path.join(stateDir, "outbox"), "--delivery-target", "fixture-file", "--port", String(portNumber)], {
    cwd: project,
    env: childEnvironment,
    stdio: ["ignore", "pipe", "pipe"],
  });
  child.capturedStdout = "";
  child.capturedStderr = "";
  child.stdout.on("data", (chunk) => {
    const text = chunk.toString();
    child.capturedStdout += text;
    output += text;
  });
  child.stderr.on("data", (chunk) => {
    const text = chunk.toString();
    child.capturedStderr += text;
    output += text;
  });
  return child;
}

function startWorkbench(portNumber) {
  return spawn(projectPython, [path.join(repositoryRoot, "outputs/podsum_email_workbench.py"), "--root", path.join(stateDir, "downloads"), "--date", "2026-07-14", "--port", String(portNumber), "--reactor-endpoint", `http://127.0.0.1:${activeReactorPort}`], {
    cwd: repositoryRoot,
    env: childEnvironment,
    stdio: ["ignore", "pipe", "pipe"],
  });
}

async function diagnosticState() {
  const snapshot = { receipts: [], status: undefined, worldModels: {} };
  const receiptsPath = path.join(runtime, "receipts.json");
  if (fs.existsSync(receiptsPath)) snapshot.receipts = JSON.parse(fs.readFileSync(receiptsPath, "utf8"));
  if (activeReactorPort !== undefined) {
    try {
      snapshot.status = (await request(activeReactorPort, "GET", "/status", undefined)).body;
    } catch (error) {
      snapshot.status = { error: error.message };
    }
  }
  for (const node of [gateway, responsibility]) {
    try {
      snapshot.worldModels[node] = JSON.parse(fs.readFileSync(path.join(runtime, "world-models", node, "published.json"), "utf8"));
    } catch (error) {
      snapshot.worldModels[node] = { error: error.message };
    }
  }
  return JSON.stringify(snapshot, null, 2);
}

(async () => {
  assert.ok(process.env.OPENAI_API_KEY, "OPENAI_API_KEY 是 #27 live integration 所必需的");
  assert.ok(fs.existsSync(projectPython), `Podsum Python 不存在：${projectPython}`);
  writeProject();
  const reactorPort = await port();
  activeReactorPort = reactorPort;
  reactorServe = startReactor(reactorPort);
  const firstDaemonStartupElapsedMs = await daemonStartupReadiness(reactorPort, reactorServe);

  // 先验证首个事件的最小 Gateway → Responsibility loop，失败时保留完整诊断。
  const firstFile = path.join(stateDir, "first.json");
  fs.writeFileSync(firstFile, JSON.stringify(artifact("mail-1", "First evidence")));
  const first = invokeIngress(firstFile, `http://127.0.0.1:${reactorPort}`);
  const firstFingerprint = first.envelope.commit.material_fingerprint;
  const firstProjection = await waitForVia(reactorPort, firstFingerprint);
  const firstGatewayReceipt = firstProjection.allReceipts.filter((receipt) => receipt.node === gateway).at(-1);
  assert.equal(firstGatewayReceipt.status, "rendered");
  assert.equal(firstProjection.receipt.status, "rendered");
  assert.equal(firstProjection.via.evidence_ledger.source_count, 1);
  assert.equal(firstProjection.via.items[0].uid, "mail-1");
  const ledgerBytes = fs.readFileSync(ledger);
  const prefix = JSON.parse(ledgerBytes.toString("utf8"));
  const responsibilityCount = firstProjection.allReceipts.filter((receipt) => receipt.node === responsibility).length;

  const repeated = invokeIngress(firstFile, `http://127.0.0.1:${reactorPort}`);
  assert.equal(repeated.envelope.commit.material_fingerprint, firstFingerprint);
  assert.equal(repeated.envelope.commit.added_packs, 0);
  await delay(1000);
  const repeatReceipts = await receipts(reactorPort);
  assert.equal(repeatReceipts.filter((receipt) => receipt.node === responsibility).length, responsibilityCount, "重复 pack 不得 rerender Responsibility");
  assert.deepEqual(fs.readFileSync(ledger), ledgerBytes, "重复 pack 不得改变 ledger 文件字节");
  assert.equal((await waitForVia(reactorPort, firstFingerprint)).via.evidence_ledger.material_fingerprint, firstFingerprint, "重复 pack 不得移动 current Evidence fingerprint");

  const changedFile = path.join(stateDir, "changed.json");
  fs.writeFileSync(changedFile, JSON.stringify(artifact("mail-2", "New material evidence")));
  const changed = invokeIngress(changedFile, `http://127.0.0.1:${reactorPort}`);
  assert.notEqual(changed.envelope.commit.material_fingerprint, firstFingerprint);
  const changedVia = await waitForVia(reactorPort, changed.envelope.commit.material_fingerprint);
  assert.equal(changedVia.via.evidence_ledger.source_count, 2);
  assert.equal(changedVia.via.items[0].uid, "mail-2");
  const changedLedger = JSON.parse(fs.readFileSync(ledger, "utf8"));
  assert.deepEqual(changedLedger.sources.slice(0, prefix.sources.length), prefix.sources, "新 material 必须保留 source prefix");
  assert.deepEqual(changedLedger.entries.slice(0, prefix.entries.length), prefix.entries, "新 material 必须保留 entry prefix");

  await stop(reactorServe);
  reactorServe = undefined;
  const restartPort = await port();
  activeReactorPort = restartPort;
  reactorServe = startReactor(restartPort);
  const restartDaemonStartupElapsedMs = await daemonStartupReadiness(restartPort, reactorServe);
  assert.equal(JSON.parse(fs.readFileSync(ledger, "utf8")).sources.length, 2);
  const restartedVia = await waitForVia(restartPort, changed.envelope.commit.material_fingerprint);
  assert.equal(restartedVia.via.evidence_ledger.material_fingerprint, changed.envelope.commit.material_fingerprint);

  // 临时移走 ledger：Workbench 只能消费 daemon GET，不能读取或修复 local ledger。
  const hiddenLedger = `${ledger}.hidden`;
  fs.renameSync(ledger, hiddenLedger);
  try {
    const daemonVia = (await request(restartPort, "GET", "/via/email-evidence-pack", undefined));
    assert.equal(daemonVia.status, 200);
    const workbenchPort = await port();
    workbench = startWorkbench(workbenchPort);
    await health(workbenchPort, "/api/context");
    const workbenchVia = await request(workbenchPort, "GET", "/api/evidence-pack", undefined);
    assert.equal(workbenchVia.status, 200);
    assert.deepEqual(workbenchVia.body.scan, daemonVia.body, "Workbench scan must be the daemon GET body exactly");
    assert.equal(workbenchVia.body.ledger, null);
    assert.equal(workbenchVia.body.path, `http://127.0.0.1:${restartPort}/via/email-evidence-pack`);
    const workbenchLedger = await request(workbenchPort, "GET", "/api/evidence-ledger", undefined);
    assert.equal(workbenchLedger.status, 200);
    const { ok: ledgerOk, ...ledgerProjection } = workbenchLedger.body;
    assert.equal(ledgerOk, true);
    assert.deepEqual(ledgerProjection, daemonVia.body.evidence_ledger);
  } finally {
    if (fs.existsSync(hiddenLedger)) fs.renameSync(hiddenLedger, ledger);
  }

  const status = await request(restartPort, "GET", "/status", undefined);
  assert.equal(status.status, 200);
  assert.equal(status.body.chain.ok, true);
  const replay = JSON.parse(command(devtools, [runtime, "--describe", "--json"], project));
  assert.equal(replay.chainVerify.ok, true);
  console.log(`#27 daemon 编译启动耗时：首次 ${firstDaemonStartupElapsedMs}ms，重启 ${restartDaemonStartupElapsedMs}ms`);
  console.log("#27 isolated evidence-module live gate: ingress, dedup, append, restart, Workbench, receipts and DevTools verified");
})().catch(async (error) => {
  console.error(error.stack);
  console.error("#27 diagnostics (receipts/status/world-model):");
  console.error(await diagnosticState());
  console.error("reactor serve output:\n" + output);
  process.exitCode = 1;
}).finally(async () => {
  await stop(workbench);
  await stop(reactorServe);
  fs.rmSync(stateDir, { recursive: true, force: true });
});
