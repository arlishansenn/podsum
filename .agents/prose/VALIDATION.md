### #26 stock CLI determinism repair (2026-07-14)

Oracle root cause: installed `@openprose/reactor-cli` 0.2.2 `trigger` did not thread `reactor.yml` custom provider/render model into the nested render options; a warm cache also skipped an exact custom-key guard. Separately, `buildProjectTruthFor` chose the lexically first `state/*.json`, so `state/atomic.json` could hide `state/incoming.json` even when the compiled canonicalizer requires `event_id` and `message`, producing a constant null/empty projection hash.

The local single `patch-package` patch backports the trigger provider/model portion of [openprose/prose#138](https://github.com/openprose/prose/pull/138), commit `82fcc15f`: clean provider-plan failure, exact-key fail-fast, nested model/provider/decoding threading, injected `testRunProjectImpl`, and nonzero failed disposition. The generic projection now parses every stable-path JSON candidate and deterministically selects the highest compiled-field-presence score (candidate order breaks ties): combined `{ "state/snapshot.json": parsed }`, each direct backing, then a necessary single-facet wrapper. Presence follows the canonical serializer recursion (`prefix + '.' + objectKey`) rather than splitting dots, so file-name keys remain exact; malformed/no matching candidates return `{}`.

RED was run first and failed as intended:

```text
node --test tests/bootstrap-compat.test.cjs
✖ trigger threads configured render model/provider ...
  TypeError: Cannot read properties of undefined (reading 'model')
✖ project truth chooses JSON satisfying compiled canonicalizer fields ...
  actual: { unrelated: true }
  expected: { event_id: 'one', message: 'first' }
```

首次修复后的 live gate 记录：Gateway 第 209 行已 green，但 Responsibility `@atomic` 的第 210 行仍 RED。根因不是 Gateway facet，而是 `buildProjectTruthFor` 把 `facets=[]` 误当成无需 projection，直接返回 `{}`；因此 atomic canonicalizer 没有收到含 `snapshot_event_id`、`snapshot_message`、`event_updates` 的 structured candidate，material hash 不会移动。第二次离线 RED 将 atomic spec 改为实际完整 spec（`node`、`default_material`、空 `facets` 以及 `state/snapshot.json.snapshot_event_id`、`.snapshot_message`、`.event_updates`），暴露旧 root-field test 虽 green 却没有 replicate：direct `state/snapshot.json` 不含该 exact canonical path。针对未打 patch 的临时 `npm ci --ignore-scripts`，测试按预期 RED：Gateway 通过，atomic 的 actual 是 `{ unrelated: true }`，而 expected 是含 `state/atomic.json` 与 `state/snapshot.json` 的 combined map。修复后真实 `compileNode(spec).canonicalizer.apply(project(files))` 断言非 null `@atomic` hash 且 evt-1 → evt-2 改变；Gateway 也使用完整 compiled root spec/fields，证明 incoming fingerprint 改变。该轮离线 patch 验证未执行 live gate。

GREEN mechanical validation (offline fake compile providers; no live render/provider) passed:

```sh
npm run test:bootstrap-compat
node -c tests/bootstrap-compat.test.cjs
node -c node_modules/@openprose/reactor-cli/dist/commands/trigger.js
node -c node_modules/@openprose/reactor-cli/dist/run/run-core.js
node -c node_modules/@openprose/reactor-cli/dist/run/load-run-project.js
git diff --check
# fresh temporary copy: npm ci (postinstall applies patches/@openprose+reactor-cli+0.2.2.patch)
```

第三次 #26 stock live 记录：第 209 行 Gateway fingerprint change 为 green，第 210 行 atomic previous-fingerprint fix 为 green；但当前第 207 行 Responsibility status 为 `failed`，故 stock live evidence 为 **INVALID/RED**。本次真实 receipt cost 为 fresh `535725`、reused `1595264`。`0.2.2` trigger/provider 与 generic `projectTruthFor` 本地 patch 的离线机械验证均为 green；stock Agent liveness 与 failure-reason 仍为 red。

[PR #138](https://github.com/openprose/prose/pull/138) 所涉 reason persistence 需跨 `@openprose/reactor` 的上游改动，尚未回补；当前不扩大本地 patch，以免影响 production SDK。production custom daemon 显式提供 provider 与 `projectTruthFor`，不走 stock `trigger`/`serve` render/projectTruth，且已有 bounded terminal diagnostics，因此该 stock-CLI 修复不改变 production path。production cutover 继续暂停，等待 owner 决策：接受此结果为 framework finding，或要求完整 upstream backport。除该第三次 live 外，未执行 CoModeling、commit、install、launchctl、IMAP 或 SMTP。

### #32 ingress durability and redaction recovery (2026-07-14)

邮件入口的默认账本已对齐到 `runtime_root/store/email-evidence-ledger.json`，每日 artifact 位于 `runtime_root/store/ingress/`；显式 `--ledger` 仍可覆盖。Ingress plist 使用同一 production 路径。账本写入采用临时文件 flush/file fsync、replace、父目录 fsync，并以 0600 创建临时文件。

Redaction 使用无 payload/secret 的 durable intent：intent 落盘后才删除 payload；删除可重入；账本 tombstone/relation/event 持久化后才删除 intent 并 fsync 目录。正常 `apply_action`、ingest、latest/projection 都会恢复中断 intent。测试覆盖 intent 写入后、部分删除后、全部删除后及账本提交后的进程内 crash seam；恢复后 payload 仍消失且 intent 清理。

本次机械验证：

```sh
PYTHONPATH=outputs "$HOME/Library/Application Support/Podsum/.venv/bin/python" -m unittest \
  tests.test_evidence_relations tests.test_email_ingress tests.test_cutover_state \
  tests.test_reactor_production_layout -v
plutil -lint outputs/com.local.podsum-email-ingress.plist
git diff --check
```

Podcast Feishu target 属于继承的未提交、非 email 改动；本 #32 未改动其 plist。以上 focused tests 均通过；未执行 provider、CoModeling、commit、install、launchctl、IMAP 或 SMTP。

### Oracle #27 Evidence VIA / Workbench blockers（2026-07-14，pending modified live gate）

#27 的 live test 已改为 isolated two-contract evidence-module gate：不再写临时 `reactor.yml`，不再依赖 `reactor` CLI；receipt verification 改为 daemon `GET /status` 的 `chain.ok` 与直接针对 runtime 的 DevTools。每次 ingress POST 后都要求 daemon Evidence VIA body 的 ledger material fingerprint 等于 commit，且 `via_fingerprint` 等于最新 Evidence named receipt facet。Workbench 启动前会临时移走 ledger，要求 `/api/evidence-pack.scan` 与 daemon GET body deep-equal、ledger 为 null、path 为 endpoint，且 `/api/evidence-ledger` 等于 daemon body 的 `evidence_ledger`，finally 恢复文件。

Workbench 配置 Reactor 时只经既有 `reactor_json('/via/email-evidence-pack')` 读取并严格验证 `object_type`、object `evidence_ledger` 和非空 `via_fingerprint`；GET 失败或无效响应 fail/error，绝不回退本地 ledger。另有 fake loopback Reactor focused HTTP test，在 root 放置损坏 ledger 以证明两个 API 不读取 local file。daemon 的 Evidence VIA helper 从当前 EVIDENCE receipt 取 `evidence_pack_via` fingerprint，backing 或 receipt 缺失 fail closed，且不会写回 world model。

本轮仅修改测试和机械验证边界，**未运行**该 modified #27 live gate；不能将历史 #27 结果或 isolated test 称为 production full host evidence。新增 non-live composition check 只验证 production wrapper 默认 daemon/contracts 路径和五份 required checked-in contracts；#30/#31 full-set tests 仍是 composition 的其余证据。未执行 provider、CoModeling、commit、install、launchctl、IMAP 或 SMTP。
