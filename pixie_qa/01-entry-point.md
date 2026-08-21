# Entry Point & Execution Flow

## How to run

The evaluated production entry point is the checked-in OpenProse Reactor daemon:

```sh
cd .agents/prose
PODSUM_PYTHON="$HOME/Library/Application Support/Podsum/.venv/bin/python" \
node src/evidence-reactor-daemon.cjs --contracts src --state /tmp/podsum-eval-state \
  --ledger /tmp/podsum-eval-ledger.json --port 8787
```

The Runnable launches this real five-contract daemon with a temporary state directory and ledger, then drives its HTTP interface. This uses `compileProject`, `runProject`, authored `email-intel-brief-responsibility.prose.md` and `email-review-responsibility.prose.md`, real Brief/Review `createAgentRender` calls, the configured provider, and both runtime validators; it does not call old agent helpers or a unit-test helper.

## Entry point

- **File**: `.agents/prose/src/evidence-reactor-daemon.cjs`
- **Type**: local HTTP Reactor daemon
- **Framework**: Node `http` plus `@openprose/reactor/run`; Python kernel subprocess only for ledger/action handling

## User-facing endpoints / interface

- **Endpoint**: `POST /trigger/email-evidence-gateway`
  - **Input format**: kernel-validated JSON commit envelope with `commit`, `projection`, and `current_pack`.
  - **Output format**: `{via: <current sanitized EvidencePack VIA>}` after Reactor reconciliation and Agent Brief render.
- **Endpoint**: `POST /trigger/workbench-action`
  - **Input format**: a validated evidence or Review action. Review actions include current `target_via_id` and Brief receipt fingerprint; daemon invokes the real Python kernel/store.
  - **Output format**: current Brief/Review collection and safe action receipt.
- **Endpoint**: `GET /via/email-intel-brief`, `GET /via/email-review`
  - **Input format**: none.
  - **Output format**: strict current Brief or fingerprint-bound Review collection, each with read-only VIA fingerprint; 404 before publication.
- **Endpoint**: `GET /receipts`, `GET /status`, `GET /health`
  - **Input format**: none.
  - **Output format**: Reactor receipts/cost/chain status or health response.

## Execution flow

A real caller commits an artifact through `outputs/email/evidence_ingress.py`, which atomically updates the temporary ledger and POSTs its envelope. The daemon stages ingress, deterministically publishes Evidence Gateway truth, then projects `evidence_pack_via`. The input-driven Brief responsibility calls the configured provider and validates its VIA; the input-driven Review responsibility then calls the real provider and validates/merges its advisory review. The Runnable reads the current Brief ID/receipt fingerprint, dynamically submits the opposite human verdict through the HTTP Gateway, captures the visible conflict, and in the authority case submits prohibited Agent confirmation to observe Gateway rejection without mutation.

## Environment requirements

| Variable | Purpose | Required? | Default |
| -------- | ------- | --------- | ------- |
| `OPENAI_API_KEY` | credentials for compile and real Agent Brief render | Yes | none |
| `REACTOR_BASE_URL` | OpenAI-compatible provider endpoint | No | `http://100.64.0.5:4000/v1` |
| `REACTOR_RENDER_MODEL` | real brief render model | No | `newapi/gpt-5.5` |
| `REACTOR_COMPILE_MODEL` | real contract compile model | No | `newapi/gpt-5.5` |
| `PODSUM_PYTHON` | absolute Podsum application venv for the Python ledger kernel | Yes for eval Runnable | app venv path in daemon |
| `PYTHONPATH` | allows the kernel to import `outputs/email` modules | Runnable supplies `outputs` | daemon preserves/appends it |
