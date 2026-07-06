# AGENTS.md

## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues (via the `gh` CLI). External PRs are not a triage surface. See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical triage roles using default label names (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Runtime and tests

Podsum uses its application virtual environment. Run Python tests and Podsum CLI
verification with:

```sh
"$HOME/Library/Application Support/Podsum/.venv/bin/python"
```

Do not use the system `python3` for project verification; it can miss runtime
dependencies such as LangGraph that are installed in the Podsum venv.
