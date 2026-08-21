/* Oracle #26 离线机械回归测试；不调用 provider 或网络。 */
const assert = require('node:assert/strict');
const test = require('node:test');
const { mkdtempSync, rmSync, writeFileSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { join } = require('node:path');
const { Usage, setTracingDisabled } = require('@openai/agents');
// 伪造编译会话绝不能导出遥测或访问真实端点。
setTracingDisabled(true);
const { createMemoryStorageAdapter, createSystemClockAdapter } = require('@openprose/reactor');
const { FileSystemWorldModelStore } = require('@openprose/reactor/adapters');
const { compileNode } = require('@openprose/reactor/internals');
const { runTriggerCommand } = require('@openprose/reactor-cli/dist/commands/trigger');
const { buildProjectTruthFor } = require('@openprose/reactor-cli/dist/run/run-core');

const NODE = 'test-gateway';
const KEY = 'PODSUM_ORACLE_26_ABSENT_KEY';
function fakeProvider(json) {
  const model = {
    async getResponse() {
      return { usage: new Usage({ inputTokens: 1, outputTokens: 1, totalTokens: 2 }), output: [{ type: 'message', role: 'assistant', status: 'completed', content: [{ type: 'output_text', text: json }] }] };
    },
    async *getStreamedResponse() { throw new Error('not used'); },
  };
  return { getModel: () => model };
}
function tempProject(yml) {
  const projectDir = mkdtempSync(join(tmpdir(), 'podsum-oracle-26-project-'));
  const stateDir = mkdtempSync(join(tmpdir(), 'podsum-oracle-26-state-'));
  writeFileSync(join(projectDir, 'tiny.prose.md'), `---\nname: ${NODE}\nkind: gateway\n---\n# Tiny\n### Goal\nTest.\n### Maintains\n#### incoming\nA value.\n`);
  writeFileSync(join(projectDir, 'reactor.yml'), yml);
  return { projectDir, stateDir };
}
function compileOptions() {
  return { testSkill: 'offline test skill', testProviders: {
    forme: fakeProvider(JSON.stringify({ nodes: [{ id: NODE, kind: 'gateway', wake_source: 'external', requires: [], maintains: ['incoming'] }], matches: [] })),
    canonicalizer: { [NODE]: fakeProvider(JSON.stringify({ fields: [{ path: 'event_id', material: true }, { path: 'message', material: true }], default_material: true, facets: [{ facet: 'incoming', paths: ['event_id', 'message'] }] })) },
    skipPostconditions: true,
  }};
}
function adapters(stateDir) { return { clock: createSystemClockAdapter(), storage: createMemoryStorageAdapter(), worldModel: new FileSystemWorldModelStore({ directory: join(stateDir, 'world-models') }) }; }
function captureImpl(calls) { return async input => { calls.push(input); return { reactor: { ingest: async () => [], ledger: { all: () => [] } }, bootResults: [] }; }; }

test('trigger threads configured render model/provider and rejects missing custom key before render', async () => {
  const { projectDir, stateDir } = tempProject(['model:', '  provider: custom', '  base_url: https://example.invalid/v1', '  api_key_env: PODSUM_ORACLE_26_KEY', '  render_model: local/deterministic', '  temperature: 0'].join('\n'));
  const calls = [];
  process.env.PODSUM_ORACLE_26_KEY = 'not-a-real-key';
  try {
    const code = await runTriggerCommand({ node: NODE, projectDir, stateDir, json: true, testAdapters: adapters(stateDir), testCompileOptions: compileOptions(), testRunProjectImpl: captureImpl(calls) }, () => {});
    assert.equal(code, 0);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].render.render.model, 'local/deterministic');
    assert.equal(calls[0].render.render.temperature, 0);
    assert.equal(calls[0].render.providerLabel, 'custom');
    assert.ok(calls[0].render.render.provider);
  } finally { delete process.env.PODSUM_ORACLE_26_KEY; rmSync(projectDir, { recursive: true, force: true }); rmSync(stateDir, { recursive: true, force: true }); }

  const cold = tempProject(['model:', '  provider: custom', '  base_url: https://example.invalid/v1', `  api_key_env: ${KEY}`].join('\n'));
  const coldCalls = []; const lines = [];
  delete process.env[KEY];
  try {
    // 离线编译会创建热缓存；trigger 仍须自行检查真实密钥。
    const { runCompileCommand } = require('@openprose/reactor-cli/dist/commands/compile');
    assert.equal(await runCompileCommand({ projectDir: cold.projectDir, stateDir: cold.stateDir, json: true, offline: true, ...compileOptions() }, () => {}), 0);
    delete process.env.REACTOR_OFFLINE;
    const code = await runTriggerCommand({ node: NODE, projectDir: cold.projectDir, stateDir: cold.stateDir, json: true, testAdapters: adapters(cold.stateDir), testRunProjectImpl: captureImpl(coldCalls) }, line => lines.push(line));
    assert.equal(code, 1);
    assert.match(JSON.parse(lines.join('\n')).message, new RegExp(KEY));
    assert.equal(coldCalls.length, 0);
  } finally { rmSync(cold.projectDir, { recursive: true, force: true }); rmSync(cold.stateDir, { recursive: true, force: true }); }
});

const gatewaySpec = {
  node: 'email-ingress-gateway', default_material: true,
  facets: [{ facet: 'incoming', paths: ['event_id', 'message'] }],
  fields: [
    { material: true, path: 'event_id', text: { case_insensitive: false, collapse_whitespace: false } },
    { material: true, path: 'message', text: { case_insensitive: false, collapse_whitespace: false } },
    ...['derived_markdown', 'summary', 'summaries', 'timestamp', 'fetched_at', 'request_id', 'connector_cursor', 'receipt_id', 'ingress_boundary_fingerprint'].map(path => ({ material: false, path })),
  ],
};
const atomicSpec = {
  node: 'email-bootstrap-responsibility', default_material: true, facets: [],
  fields: [
    { material: true, path: 'state/snapshot.json.snapshot_event_id', text: { case_insensitive: false, collapse_whitespace: false } },
    { material: true, path: 'state/snapshot.json.snapshot_message', text: { case_insensitive: false, collapse_whitespace: false } },
    { material: true, path: 'state/snapshot.json.event_updates', number: { quantum: null } },
  ],
};

// 必须使用 compileNode 的真实 canonicalizer，而不是手写 hash 断言。
test('gateway direct incoming backing changes the compiled fingerprint', () => {
  const project = buildProjectTruthFor({ perNode: { gateway: { spec: gatewaySpec, compiled: { canonicalizer: compileNode(gatewaySpec).canonicalizer } } } }, 'gateway');
  const files = { 'state/incoming.json': new TextEncoder().encode(JSON.stringify({ event_id: 'evt-1', message: 'first' })) };
  const first = compileNode(gatewaySpec).canonicalizer.apply(project(files));
  files['state/incoming.json'] = new TextEncoder().encode(JSON.stringify({ event_id: 'evt-2', message: 'second' }));
  const second = compileNode(gatewaySpec).canonicalizer.apply(project(files));
  assert.notEqual(first['@atomic'], null);
  assert.notEqual(first.incoming, null);
  assert.notEqual(first.incoming, second.incoming);
});

test('atomic backing selects the state/snapshot.json file-map candidate and changes fingerprint', () => {
  const project = buildProjectTruthFor({ perNode: { responsibility: { spec: atomicSpec, compiled: { canonicalizer: compileNode(atomicSpec).canonicalizer } } } }, 'responsibility');
  const files = {
    'state/atomic.json': new TextEncoder().encode(JSON.stringify({ unrelated: true })),
    'state/snapshot.json': new TextEncoder().encode(JSON.stringify({ snapshot_event_id: 'evt-1', snapshot_message: 'first', event_updates: 1 })),
  };
  const firstTruth = project(files);
  assert.deepEqual(firstTruth, { 'state/atomic.json': { unrelated: true }, 'state/snapshot.json': { snapshot_event_id: 'evt-1', snapshot_message: 'first', event_updates: 1 } });
  const first = compileNode(atomicSpec).canonicalizer.apply(firstTruth);
  files['state/snapshot.json'] = new TextEncoder().encode(JSON.stringify({ snapshot_event_id: 'evt-2', snapshot_message: 'second', event_updates: 2 }));
  const second = compileNode(atomicSpec).canonicalizer.apply(project(files));
  assert.notEqual(first['@atomic'], null);
  assert.notEqual(first['@atomic'], second['@atomic']);
});
