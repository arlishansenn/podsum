"use strict";

// #29 独立 live blackbox：真实 compile、确定性 evidence、真实 Agent Brief 与 Workbench。
const assert = require("node:assert/strict");
const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");

const prose = path.resolve(__dirname, "..");
const root = path.resolve(prose, "..", "..");
const python = process.env.PODSUM_PYTHON || path.join(os.homedir(), "Library/Application Support/Podsum/.venv/bin/python");
const temp = fs.mkdtempSync(path.join(os.tmpdir(), "podsum-brief-"));
const project = path.join(temp, "project");
const state = path.join(temp, "state");
const ledger = path.join(temp, "downloads/EmailReports/email-evidence-ledger.json");
const env = { ...process.env, OPENAI_AGENTS_DISABLE_TRACING: "1", PYTHONPATH: path.join(root, "outputs") };
const SEMANTIC_RENDER_TIMEOUT_MS = 300_000;
const INGRESS_SUBPROCESS_TIMEOUT_MS = 360_000;
let daemon;
let workbench;
let daemonOutput = "";
let workbenchOutput = "";
let daemonExit;

function appendOutput(current, chunk) {
  // 保留末尾，避免 provider 持续输出时诊断本身占满测试进程内存。
  return (current + chunk.toString("utf8")).slice(-100_000);
}

function captureOutput(child, setOutput) {
  child.stdout.on("data", (chunk) => setOutput(chunk));
  child.stderr.on("data", (chunk) => setOutput(chunk));
}

function port() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const value = server.address().port;
      server.close(() => resolve(value));
    });
  });
}

function request(portValue, method, pathname, body, timeoutMs = 30_000) {
  return new Promise((resolve, reject) => {
    const text = body === undefined ? undefined : JSON.stringify(body);
    const req = http.request({
      host: "127.0.0.1",
      port: portValue,
      method,
      path: pathname,
      timeout: timeoutMs,
      // daemon 的长 POST 完成后不能复用 readiness probe 的已关闭 socket；每个 blackbox request 独立连接。
      agent: false,
      headers: text === undefined ? { connection: "close" } : { "content-type": "application/json", "content-length": Buffer.byteLength(text), connection: "close" },
    }, (res) => {
      let result = "";
      res.on("data", (chunk) => { result += chunk; });
      res.on("end", () => {
        try {
          resolve({ status: res.statusCode, body: JSON.parse(result) });
        } catch {
          reject(new Error(`HTTP ${method} ${pathname} returned non-JSON: ${result}`));
        }
      });
    });
    req.on("timeout", () => req.destroy(new Error(`HTTP ${method} ${pathname} exceeded ${timeoutMs}ms`)));
    req.on("error", (error) => reject(new Error(`HTTP ${method} ${pathname} failed: ${error.message}`)));
    if (text) req.write(text);
    req.end();
  });
}

function assertDaemonAlive() {
  if (daemonExit) throw new Error(`daemon exited early: ${daemonExit}`);
}

async function ready(portValue) {
  const until = Date.now() + SEMANTIC_RENDER_TIMEOUT_MS;
  while (Date.now() < until) {
    assertDaemonAlive();
    try {
      if ((await request(portValue, "GET", "/health")).status === 200) return;
    } catch (error) {
      assertDaemonAlive();
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("daemon readiness exceeded 300 seconds");
}

async function stop(child) {
  if (!child || child.exitCode !== null) return;
  child.kill("SIGTERM");
  await Promise.race([
    new Promise((resolve) => child.once("exit", resolve)),
    new Promise((resolve) => setTimeout(resolve, 5000)),
  ]);
  if (child.exitCode === null) child.kill("SIGKILL");
}

function pack(extra) {
  return {
    object_type: "email_evidence_pack",
    object_version: "1",
    status: "ready_for_summary",
    date: "2026-07-14",
    account: "sanitized@test",
    window: "daily",
    scan_limit: 2,
    raw_count: extra ? 2 : 1,
    possibly_truncated: false,
    items: [
      { uid: "A", date: "2026-07-14", from: "sender@test", subject: "Launch", snippet: "Sanitized launch fact", evidence: [] },
      ...(extra ? [{ uid: "B", date: "2026-07-14", from: "sender@test", subject: "Update", snippet: "New material fact", evidence: [] }] : []),
    ],
  };
}

function ingress(file, endpoint) {
  // ingress 的 300 秒是一次真实 Agent semantic render 的上界；测试必须留出清理与诊断余量。
  const result = spawnSync(python, [path.join(root, "outputs/email/evidence_ingress.py"), file, ledger, endpoint], {
    cwd: root,
    env,
    encoding: "utf8",
    timeout: INGRESS_SUBPROCESS_TIMEOUT_MS,
  });
  assertDaemonAlive();
  assert.equal(result.error, undefined, `ingress child failed: ${result.error?.message}\n${result.stderr}`);
  assert.equal(result.status, 0, `ingress exited ${result.status}\nstdout:\n${result.stdout}\nstderr:\n${result.stderr}`);
  return JSON.parse(result.stdout);
}

async function diagnosticRequest(portValue, pathname) {
  try {
    return await request(portValue, "GET", pathname, undefined, 5_000);
  } catch (error) {
    return { error: error instanceof Error ? error.message : String(error) };
  }
}

async function printDiagnostics(portValue) {
  // HTTP handler 串行化 Reactor DAG；失败时仍尽量读取每一个可见 world-model 与 receipt 状态。
  const diagnostics = {
    receipts: await diagnosticRequest(portValue, "/receipts"),
    status: await diagnosticRequest(portValue, "/status"),
    current_evidence_world_model: await diagnosticRequest(portValue, "/via/email-evidence-pack"),
    current_brief_world_model: await diagnosticRequest(portValue, "/via/email-intel-brief"),
    daemon_exit: daemonExit ?? null,
    daemon_stdout_stderr: daemonOutput || "(no daemon output)",
    workbench_stdout_stderr: workbenchOutput || "(no workbench output)",
  };
  console.error("#29 live brief diagnostics:\n" + JSON.stringify(diagnostics, null, 2));
}

(async () => {
  assert.ok(process.env.OPENAI_API_KEY, "OPENAI_API_KEY is required");
  fs.mkdirSync(path.join(project, "src"), { recursive: true });
  for (const file of ["email-evidence-gateway.prose.md", "workbench-action-gateway.prose.md", "email-evidence-responsibility.prose.md", "email-intel-brief-responsibility.prose.md"]) {
    fs.copyFileSync(path.join(prose, "src", file), path.join(project, "src", file));
  }

  const daemonPort = await port();
  daemon = spawn(process.execPath, [path.join(prose, "src/evidence-reactor-daemon.cjs"), "--contracts", path.join(project, "src"), "--state", state, "--ledger", ledger, "--delivery-mode", "file", "--delivery-outbox", path.join(temp, "outbox"), "--delivery-target", "fixture-file", "--port", String(daemonPort)], { cwd: project, env, stdio: ["ignore", "pipe", "pipe"] });
  captureOutput(daemon, (chunk) => { daemonOutput = appendOutput(daemonOutput, chunk); });
  daemon.once("exit", (code, signal) => { daemonExit = `code=${code}, signal=${signal}`; });
  await ready(daemonPort);

  const fixture = path.join(temp, "pack.json");
  fs.writeFileSync(fixture, JSON.stringify(pack(false)));
  ingress(fixture, `http://127.0.0.1:${daemonPort}`);
  const first = await request(daemonPort, "GET", "/via/email-intel-brief");
  assert.equal(first.status, 200);
  assert.equal(first.body.status, "candidate");
  for (const claim of first.body.claims) assert.ok(claim.evidence_entry_ids.length);

  const briefReceipts = async () => (await request(daemonPort, "GET", "/receipts")).body.receipts.filter((receipt) => receipt.node === "7R6QX8GZ3EW3S7PVJ9KQ6E2D4M");
  const receiptCount = (await briefReceipts()).length;
  ingress(fixture, `http://127.0.0.1:${daemonPort}`);
  assert.equal((await briefReceipts()).length, receiptCount, "identical evidence must memo-skip Brief");

  fs.writeFileSync(fixture, JSON.stringify(pack(true)));
  ingress(fixture, `http://127.0.0.1:${daemonPort}`);
  const second = await request(daemonPort, "GET", "/via/email-intel-brief");
  assert.equal(second.status, 200);
  assert.notEqual(second.body.source.material_fingerprint, first.body.source.material_fingerprint);
  const secondReceiptCount = (await briefReceipts()).length;
  assert.ok(secondReceiptCount > receiptCount, "new evidence material must naturally wake Brief");

  const entry = (await request(daemonPort, "GET", "/via/email-evidence-pack")).body.evidence_ledger.current_entries.find((value) => value.kind === "email_item");
  const redact = await request(daemonPort, "POST", "/trigger/workbench-action", { action_id: "redact-A", kind: "redaction", target_id: entry.entry_id, actor: "podsum.local-owner", reason: "fixture", target_fingerprint: second.body.source.material_fingerprint }, SEMANTIC_RENDER_TIMEOUT_MS);
  assert.equal(redact.status, 200);
  const after = await request(daemonPort, "GET", "/via/email-intel-brief");
  const currentEvidence = await request(daemonPort, "GET", "/via/email-evidence-pack");
  assert.equal(after.status, 200);
  assert.ok((await briefReceipts()).length > secondReceiptCount, "redaction must naturally wake Brief");
  assert.equal(after.body.source.material_fingerprint, currentEvidence.body.evidence_ledger.material_fingerprint);
  assert.equal(after.body.source.revision, currentEvidence.body.evidence_ledger.revision);
  assert.ok(!JSON.stringify(after.body).includes("Sanitized launch fact"));

  const wbPort = await port();
  workbench = spawn(python, [path.join(root, "outputs/podsum_email_workbench.py"), "--root", path.join(temp, "downloads"), "--date", "2026-07-14", "--port", String(wbPort), "--reactor-endpoint", `http://127.0.0.1:${daemonPort}`], { cwd: root, env, stdio: ["ignore", "pipe", "pipe"] });
  captureOutput(workbench, (chunk) => { workbenchOutput = appendOutput(workbenchOutput, chunk); });
  for (let index = 0; index < 100; index += 1) {
    try {
      if ((await request(wbPort, "GET", "/api/context")).status === 200) break;
    } catch {
      // Workbench 正在绑定端口；下一轮继续，但 daemon 提前退出必须立即报错。
      assertDaemonAlive();
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  const workbenchBrief = await request(wbPort, "GET", "/api/intel-brief");
  assert.equal(workbenchBrief.status, 200);
  assert.deepEqual(workbenchBrief.body.object_type, after.body.object_type);
  assert.ok(workbenchBrief.body.effective_markdown);
  assert.ok(workbenchBrief.body.effective_markdown.includes(after.body.summary));
  const entryToUid = new Map();
  for (const item of currentEvidence.body.items) {
    if (item.email_entry_id) entryToUid.set(item.email_entry_id, item.uid);
    for (const evidence of item.evidence || []) if (evidence.link_entry_id) entryToUid.set(evidence.link_entry_id, item.uid);
  }
  for (const claim of after.body.claims) {
    assert.ok(workbenchBrief.body.effective_markdown.includes(claim.text));
    for (const entryId of claim.evidence_entry_ids) {
      const uid = entryToUid.get(entryId);
      assert.ok(uid, `claim evidence must map to current EmailEvidencePack item: ${entryId}`);
      assert.ok(workbenchBrief.body.effective_markdown.includes(`[UID ${uid}](email://2026-07-14/${uid})`));
    }
  }
  assert.ok(workbenchBrief.body.source_coverage.item_count > 0);
  assert.ok(workbenchBrief.body.source_coverage.covered_count > 0);
  const workbenchChecklist = await request(wbPort, "GET", "/api/checklist");
  assert.equal(workbenchChecklist.status, 200);
  assert.equal(workbenchChecklist.body.checklist.has_key_takeaway, true);
  assert.equal(workbenchChecklist.body.checklist.has_source_index, true);
  assert.equal(workbenchChecklist.body.checklist.has_uid_trace, true);

  const devtools = spawnSync(path.join(prose, "node_modules/.bin/reactor-devtools"), [state, "--describe", "--json"], { cwd: project, env, encoding: "utf8", timeout: 30_000 });
  assert.equal(devtools.status, 0, devtools.stderr);
  assert.equal(JSON.parse(devtools.stdout).chainVerify.ok, true);

  const status = await request(daemonPort, "GET", "/status");
  const agentReceipts = status.body.receipts.filter((receipt) => receipt.node === "7R6QX8GZ3EW3S7PVJ9KQ6E2D4M");
  const latestAgentReceipt = agentReceipts.at(-1);
  assert.ok(agentReceipts.length >= 3);
  assert.ok(agentReceipts.every((receipt) => receipt.wake?.source === "input"), "Brief only wakes from its Evidence Responsibility input");
  assert.ok(!status.body.receipts.some((receipt) => receipt.node === "7R6QX8GZ3EW3S7PVJ9KQ6E2D4M::ingress"), "Brief must not have phantom ingress receipts");
  console.log("#29 live brief black box: grounded candidate, memo skip, natural update/redaction, Workbench, receipts and DevTools verified");
  console.log(`#29 Agent receipt outcome: ${JSON.stringify({ status: latestAgentReceipt?.status, wake_source: latestAgentReceipt?.wake?.source, input_fingerprints: latestAgentReceipt?.input_fingerprints, cost: latestAgentReceipt?.cost, agent_receipt_count: agentReceipts.length, source: after.body.source })}`);
})().catch(async (error) => {
  console.error(error);
  await printDiagnostics(daemon ? Number(daemon.spawnargs.at(-1)) : 0);
  process.exitCode = 1;
}).finally(async () => {
  await stop(workbench);
  await stop(daemon);
  fs.rmSync(temp, { recursive: true, force: true });
});
