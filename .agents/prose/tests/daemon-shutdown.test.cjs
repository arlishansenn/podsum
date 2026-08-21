"use strict";
// 不启动 provider：真实 Node HTTP server 验证 serialized drain 和 shutdown 的机械边界。
const assert = require("node:assert/strict");
const http = require("node:http");
const { gracefulClose } = require("../src/evidence-reactor-daemon.cjs");

(async () => {
  let accepting = true;
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  let pending = Promise.resolve();
  const server = http.createServer((request, response) => {
    if (!accepting) { response.writeHead(503); response.end("stopping"); return; }
    pending = pending.then(async () => { await gate; response.end("drained"); });
    pending = pending.catch(() => undefined);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = server.address().port;
  const responseDone = new Promise((resolve, reject) => {
    http.get(`http://127.0.0.1:${port}/work`, (response) => { response.resume(); response.on("end", resolve); }).on("error", reject);
  });
  await new Promise((resolve) => setTimeout(resolve, 20));
  const close = gracefulClose(server, () => pending, () => { accepting = false; });
  const closing = close();
  let closed = false;
  closing.then(() => { closed = true; });
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(closed, false, "must wait for queued work");
  release();
  await responseDone;
  await closing;
  assert.equal(closed, true);
  await assert.rejects(fetch(`http://127.0.0.1:${port}/health`));
  console.log("daemon graceful shutdown passed");
})().catch((error) => { console.error(error); process.exitCode = 1; });
