import asyncio
import fcntl
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import pixie
from pydantic import BaseModel

from pixie_qa.instrumentation import capture_brief_result, capture_result, controlled_brief_pack, controlled_pack
from pixie_qa.json_types import JsonObject

ROOT = Path(__file__).resolve().parents[1]
PROSE = ROOT / ".agents" / "prose"
APP_PYTHON = Path(os.environ.get("PODSUM_PYTHON", Path.home() / "Library/Application Support/Podsum/.venv/bin/python"))
CONTRACT_FILES = (
    "email-evidence-gateway.prose.md",
    "workbench-action-gateway.prose.md",
    "email-evidence-responsibility.prose.md",
    "email-intel-brief-responsibility.prose.md",
    "email-review-responsibility.prose.md",
)
# Pixie 可并行排程；真实 provider、Reactor state 与 filesystem daemon 必须单飞。
_RUN_LOCK = asyncio.Lock()


class AppArgs(BaseModel):
    scenario: JsonObject


class _ReactorRunnable:
    """Shared strictly typed real-daemon transport for the two Pixie runnables."""

    def _run_review_scenario(self, controlled: JsonObject) -> JsonObject:
        scenario = self._json_object(controlled.get("scenario"), "review_input.scenario")
        initial_pack = self._json_object(scenario.get("initial_pack"), "review_input.scenario.initial_pack")
        if not APP_PYTHON.is_file():
            raise RuntimeError(f"Podsum app venv missing: {APP_PYTHON}")
        lock_path = Path(tempfile.gettempdir()) / "podsum-pixie-reactor.lock"
        lock = lock_path.open("w")
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        work = Path(tempfile.mkdtemp(prefix="podsum-review-pixie-"))
        stderr_path = work / "daemon.stderr.log"
        stderr = stderr_path.open("w", encoding="utf-8")
        port = self._free_port()
        ledger, state = work / "email-evidence-ledger.json", work / "reactor-state"
        contracts = work / "contracts"
        contracts.mkdir()
        for filename in CONTRACT_FILES:
            shutil.copy2(PROSE / "src" / filename, contracts / filename)
        env = os.environ.copy()
        env["PODSUM_PYTHON"] = str(APP_PYTHON)
        env["OPENAI_AGENTS_DISABLE_TRACING"] = "1"
        daemon = subprocess.Popen(
            ["node", "src/evidence-reactor-daemon.cjs", "--contracts", str(contracts), "--state", str(state), "--ledger", str(ledger), "--delivery-mode", "file", "--delivery-outbox", str(work / "outbox"), "--delivery-target", "pixie-file", "--port", str(port)],
            cwd=PROSE, env=env, stdout=subprocess.DEVNULL, stderr=stderr, text=True,
        )
        endpoint = f"http://127.0.0.1:{port}"
        try:
            self._wait_ready(endpoint, daemon, stderr_path)
            artifact = work / "public-review-pack.json"
            artifact.write_text(json.dumps(initial_pack, ensure_ascii=False), encoding="utf-8")
            # Ingress commits only Evidence. Brief and Review publication are asynchronous,
            # bounded VIA waits; do not restart or re-ingest on a publication race.
            evidence_via = self._ingress(artifact, ledger, endpoint)
            brief_via = self._wait_via(endpoint + "/via/email-intel-brief", daemon, stderr_path)
            agent_review = self._wait_via(endpoint + "/via/email-review", daemon, stderr_path)
            review_action = self._submit_opposite_review(endpoint, brief_via, agent_review, scenario)
            review_via = self._get(endpoint + "/via/email-review")
            review_binding = self._review_binding(review_via)
            authority_rejection = self._attempt_agent_confirm(endpoint, brief_via, scenario)
            current_brief = self._get(endpoint + "/via/email-intel-brief")
            status = self._get(endpoint + "/status")
            receipts = self._get(endpoint + "/receipts")
            return {
                "evidence_via": evidence_via,
                "brief_via": current_brief,
                "review_via": review_via,
                "review_binding": review_binding,
                "review_action": review_action,
                "authority_rejection": authority_rejection,
                "review_receipts": receipts,
                "receipt_cost": self._json_object(status, "status"),
            }
        finally:
            daemon.terminate()
            try:
                daemon.wait(timeout=15)
            except subprocess.TimeoutExpired:
                daemon.kill()
                daemon.wait(timeout=5)
            stderr.close()
            shutil.rmtree(work, ignore_errors=True)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()

    def _submit_opposite_review(self, endpoint: str, brief: JsonObject, collection: JsonObject, scenario: JsonObject) -> JsonObject:
        agent = self._current_agent_review(collection)
        if agent.get("verdict") not in {"approve", "request_revision", "abstain"}:
            raise ValueError("current Agent Review verdict missing")
        opposite = "request_revision" if agent["verdict"] != "request_revision" else "approve"
        target_id, fingerprint = brief.get("brief_id"), brief.get("via_fingerprint")
        if not isinstance(target_id, str) or not isinstance(fingerprint, str):
            raise ValueError("current Brief target binding missing")
        action: JsonObject = {
            "action_id": self._text(scenario.get("human_action_id"), "scenario.human_action_id"),
            "kind": "submit_review",
            "actor": "podsum.local-owner",
            "target_via_id": target_id,
            "target_fingerprint": fingerprint,
            "verdict": opposite,
            "findings": ["Human counter-review deliberately preserves disagreement with the current Agent verdict."],
        }
        return {"agent_verdict": agent["verdict"], "opposite_verdict": opposite, "response": self._post(endpoint + "/trigger/workbench-action", action)}

    def _attempt_agent_confirm(self, endpoint: str, brief: JsonObject, scenario: JsonObject) -> JsonObject:
        if scenario.get("exercise_authority_rejection") is not True:
            return {"performed": False}
        target_id, fingerprint = brief.get("brief_id"), brief.get("via_fingerprint")
        if not isinstance(target_id, str) or not isinstance(fingerprint, str):
            raise ValueError("current Brief target binding missing")
        body: JsonObject = {"action_id": self._text(scenario.get("rejection_action_id"), "scenario.rejection_action_id"), "kind": "confirm_brief", "actor": "agent:email-reviewer", "target_via_id": target_id, "target_fingerprint": fingerprint}
        try:
            self._post(endpoint + "/trigger/workbench-action", body)
        except urllib.error.HTTPError as error:
            payload = self._json_object(json.loads(error.read()), "authority rejection")
            return {"performed": True, "status": error.code, "response": payload}
        raise RuntimeError("Gateway accepted prohibited Agent confirmation")

    def _ingress(self, artifact: Path, ledger: Path, endpoint: str) -> JsonObject:
        completed = subprocess.run([str(APP_PYTHON), str(ROOT / "outputs/email/evidence_ingress.py"), str(artifact), str(ledger), endpoint], cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "outputs")}, capture_output=True, text=True, timeout=330, check=False)
        if completed.returncode:
            raise RuntimeError(f"real evidence ingress failed: {completed.stderr.strip()}")
        response = self._json_object(json.loads(completed.stdout), "ingress response")
        return self._json_object(self._json_object(response.get("reactor"), "ingress response.reactor").get("via"), "ingress response.reactor.via")

    @staticmethod
    def _current_agent_review(collection: JsonObject) -> JsonObject:
        """Return exactly one Agent review bound to the collection's current Brief."""
        reviews = collection.get("reviews")
        brief = collection.get("brief")
        if not isinstance(reviews, list) or not isinstance(brief, dict):
            raise ValueError("Review collection binding missing")
        brief_id, fingerprint = brief.get("brief_id"), brief.get("brief_fingerprint")
        if not isinstance(brief_id, str) or not isinstance(fingerprint, str):
            raise ValueError("Review collection current Brief ID/fingerprint missing")
        current = [
            review for review in reviews
            if isinstance(review, dict)
            and review.get("reviewer_id") == "agent:email-reviewer"
            and review.get("brief_fingerprint") == fingerprint
        ]
        if len(current) != 1:
            diagnostics = [
                {"review_id": review.get("review_id"), "brief_fingerprint": review.get("brief_fingerprint"), "status": review.get("status")}
                for review in reviews
                if isinstance(review, dict) and review.get("reviewer_id") == "agent:email-reviewer"
            ]
            raise ValueError(
                "expected exactly one current Agent Review; "
                f"brief_id={brief_id!r} brief_fingerprint={fingerprint!r} agent_review_ids/status={json.dumps(diagnostics, ensure_ascii=False)}"
            )
        return current[0]

    @classmethod
    def _review_binding(cls, collection: JsonObject) -> JsonObject:
        """从最终严格 collection 提取可审计的 proposal 到 kernel 绑定。"""
        brief = collection.get("brief")
        if not isinstance(brief, dict):
            raise ValueError("final Review collection binding missing")
        agent = cls._current_agent_review(collection)
        findings = agent.get("findings")
        if not isinstance(findings, list) or not all(isinstance(finding, str) for finding in findings):
            raise ValueError("final Agent Review findings missing")
        fields = ("reviewer_id", "review_id", "action_ref", "brief_id", "brief_fingerprint")
        if any(not isinstance(agent.get(field), str) for field in fields):
            raise ValueError("final Agent Review kernel fields missing")
        if agent["brief_id"] != brief.get("brief_id") or agent["brief_fingerprint"] != brief.get("brief_fingerprint"):
            raise ValueError("final Agent Review is not bound to collection Brief")
        return {
            "proposal": {"verdict": agent.get("verdict"), "findings": findings},
            "kernel_binding": {
                "reviewer_id": agent["reviewer_id"],
                "review_id": agent["review_id"],
                "action_ref": agent["action_ref"],
                "brief_id": agent["brief_id"],
                "current_reviewable_fingerprint": agent["brief_fingerprint"],
            },
        }

    @staticmethod
    def _text(value: object, name: str) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a non-empty string")
        return value

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    @staticmethod
    def _json_object(value: object, name: str) -> JsonObject:
        if not isinstance(value, dict):
            raise ValueError(f"{name} must be a JSON object")
        return value

    @classmethod
    def _get(cls, url: str) -> JsonObject:
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                return cls._json_object(json.loads(response.read()), url)
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"HTTP {error.code} {url}: {error.read().decode(errors='replace')}") from error

    @classmethod
    def _post(cls, url: str, payload: JsonObject) -> JsonObject:
        request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"content-type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=330) as response:
            return cls._json_object(json.loads(response.read()), url)

    @staticmethod
    def _daemon_stderr(stderr_path: Path) -> str:
        return stderr_path.read_text(encoding="utf-8", errors="replace")[-4000:]

    def _wait_via(self, url: str, daemon: subprocess.Popen[str], stderr_path: Path) -> JsonObject:
        # A 404 is the bounded publication race. Other HTTP failures, including a real
        # failed receipt surfaced by the gateway, are terminal evidence and must not loop.
        last_error = ""
        for _ in range(330):
            if daemon.poll() is not None:
                raise RuntimeError(f"real Reactor daemon exited: {self._daemon_stderr(stderr_path).strip()}")
            try:
                return self._get(url)
            except RuntimeError as error:
                last_error = str(error)
                if not last_error.startswith("HTTP 404 "):
                    raise
                failed = self._failed_via_receipt(url)
                if failed is not None:
                    raise RuntimeError(f"real VIA receipt failed: {json.dumps(failed, ensure_ascii=False)}")
                time.sleep(1)
        raise RuntimeError(f"real VIA did not publish: {last_error}; {self._daemon_stderr(stderr_path)}")

    def _failed_via_receipt(self, via_url: str) -> JsonObject | None:
        node = "7R6QX8GZ3EW3S7PVJ9KQ6E2D4M" if via_url.endswith("/email-intel-brief") else "email-review-responsibility"
        try:
            receipts = self._get(via_url.rsplit("/via/", 1)[0] + "/receipts").get("receipts")
        except RuntimeError:
            return None
        if not isinstance(receipts, list):
            return None
        failed = next((receipt for receipt in reversed(receipts) if isinstance(receipt, dict) and receipt.get("node") == node and receipt.get("status") == "failed"), None)
        return failed if isinstance(failed, dict) else None

    def _wait_ready(self, endpoint: str, daemon: subprocess.Popen[str], stderr_path: Path) -> None:
        for _ in range(300):
            if daemon.poll() is not None:
                raise RuntimeError(f"real Reactor daemon exited: {self._daemon_stderr(stderr_path).strip()}")
            try:
                if self._get(endpoint + "/health").get("ok") is True:
                    return
            except Exception:
                time.sleep(1)
        raise RuntimeError(f"real Reactor daemon did not become healthy: {self._daemon_stderr(stderr_path)}")


class AppRunnable(_ReactorRunnable, pixie.Runnable[AppArgs]):
    """Review-only Pixie runnable; Brief evaluation uses BriefRunnable."""

    @classmethod
    def create(cls) -> "AppRunnable":
        return cls()

    async def run(self, args: AppArgs) -> None:
        async with _RUN_LOCK:
            controlled = controlled_pack(lambda: {"scenario": args.scenario})
            capture_result(await asyncio.to_thread(self._run_review_scenario, controlled))


class BriefRunnable(_ReactorRunnable, pixie.Runnable[AppArgs]):
    """Brief-only runnable that drives ingress and optional real Evidence actions."""

    @classmethod
    def create(cls) -> "BriefRunnable":
        return cls()

    async def run(self, args: AppArgs) -> None:
        async with _RUN_LOCK:
            controlled = controlled_brief_pack(lambda: {"scenario": args.scenario})
            capture_brief_result(await asyncio.to_thread(self._run_brief_scenario, controlled))

    def _run_brief_scenario(self, controlled: JsonObject) -> JsonObject:
        scenario = self._json_object(controlled.get("scenario"), "brief_input.scenario")
        initial_pack = self._json_object(scenario.get("initial_pack"), "brief_input.scenario.initial_pack")
        if not APP_PYTHON.is_file():
            raise RuntimeError(f"Podsum app venv missing: {APP_PYTHON}")
        lock_path = Path(tempfile.gettempdir()) / "podsum-pixie-reactor.lock"
        lock = lock_path.open("w")
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        work = Path(tempfile.mkdtemp(prefix="podsum-brief-pixie-"))
        stderr_path = work / "daemon.stderr.log"
        stderr = stderr_path.open("w", encoding="utf-8")
        port = self._free_port()
        ledger, state = work / "email-evidence-ledger.json", work / "reactor-state"
        contracts = work / "contracts"
        contracts.mkdir()
        for filename in CONTRACT_FILES:
            shutil.copy2(PROSE / "src" / filename, contracts / filename)
        env = os.environ.copy()
        env["PODSUM_PYTHON"] = str(APP_PYTHON)
        env["OPENAI_AGENTS_DISABLE_TRACING"] = "1"
        daemon = subprocess.Popen(
            ["node", "src/evidence-reactor-daemon.cjs", "--contracts", str(contracts), "--state", str(state), "--ledger", str(ledger), "--delivery-mode", "file", "--delivery-outbox", str(work / "outbox"), "--delivery-target", "pixie-file", "--port", str(port)],
            cwd=PROSE, env=env, stdout=subprocess.DEVNULL, stderr=stderr, text=True,
        )
        endpoint = f"http://127.0.0.1:{port}"
        completed = False
        try:
            self._wait_ready(endpoint, daemon, stderr_path)
            artifact = work / "public-brief-pack.json"
            artifact.write_text(json.dumps(initial_pack, ensure_ascii=False), encoding="utf-8")
            self._ingress(artifact, ledger, endpoint)
            evidence_via = self._get(endpoint + "/via/email-evidence-pack")
            brief_via = self._wait_via(endpoint + "/via/email-intel-brief", daemon, stderr_path)
            receipt_start = self._brief_receipt_count(endpoint)
            update_receipt = self._apply_brief_action(endpoint, evidence_via, scenario)
            if update_receipt["performed"]:
                evidence_via = self._get(endpoint + "/via/email-evidence-pack")
                brief_via = self._wait_current_brief(endpoint, daemon, stderr_path, evidence_via, receipt_start)
                update_receipt["brief_receipt"] = self._brief_receipt_for(endpoint, brief_via)
            result = {
                "evidence_via": evidence_via,
                "brief_via": brief_via,
                "update_receipt": update_receipt,
                "receipt_cost": self._json_object(self._get(endpoint + "/status"), "status"),
            }
            completed = True
            return result
        finally:
            daemon.terminate()
            try:
                daemon.wait(timeout=15)
            except subprocess.TimeoutExpired:
                daemon.kill()
                daemon.wait(timeout=5)
            stderr.close()
            if not completed and os.environ.get("PODSUM_KEEP_PIXIE_FAILURE") == "1":
                # Debug-only: fixtures are public controlled inputs, but never print their contents.
                print(f"[DEBUG-BRIEF-FIXTURE] retained failed fixture: {work}", file=sys.stderr)
            else:
                shutil.rmtree(work, ignore_errors=True)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()

    def _apply_brief_action(self, endpoint: str, evidence: JsonObject, scenario: JsonObject) -> JsonObject:
        action_value = scenario.get("action")
        if action_value is None:
            return {"performed": False, "kind": None, "action_id": None, "response": None, "brief_receipt": None}
        action = self._json_object(action_value, "brief_input.scenario.action")
        kind = self._text(action.get("kind"), "scenario.action.kind")
        ledger = self._json_object(evidence.get("evidence_ledger"), "Evidence VIA ledger")
        fingerprint = self._text(ledger.get("material_fingerprint"), "Evidence ledger.material_fingerprint")
        action_id = self._text(action.get("action_id"), "scenario.action.action_id")
        items = evidence.get("items")
        if not isinstance(items, list):
            raise ValueError("Evidence VIA items must be a list")
        if kind == "refutes":
            from_id = self._email_entry_id(items, self._text(action.get("from_subject"), "scenario.action.from_subject"))
            to_id = self._email_entry_id(items, self._text(action.get("to_subject"), "scenario.action.to_subject"))
            payload: JsonObject = {"action_id": action_id, "kind": "relation", "from_id": from_id, "to_id": to_id, "relation_type": "refutes", "registry_version": "1", "actor": "podsum.local-owner", "reason": self._text(action.get("reason"), "scenario.action.reason"), "target_fingerprint": fingerprint}
        elif kind == "redaction":
            target_id = self._email_entry_id(items, self._text(action.get("target_subject"), "scenario.action.target_subject"))
            payload = {"action_id": action_id, "kind": "redaction", "target_id": target_id, "actor": "podsum.local-owner", "reason": self._text(action.get("reason"), "scenario.action.reason"), "target_fingerprint": fingerprint}
        else:
            raise ValueError("scenario.action.kind must be refutes or redaction")
        response = self._post(endpoint + "/trigger/workbench-action", payload)
        return {"performed": True, "kind": kind, "action_id": action_id, "response": response, "brief_receipt": None}

    def _brief_receipt_for(self, endpoint: str, brief: JsonObject) -> JsonObject:
        fingerprint = self._text(brief.get("via_fingerprint"), "Brief VIA fingerprint")
        receipts = self._get(endpoint + "/receipts").get("receipts")
        if not isinstance(receipts, list):
            raise ValueError("Reactor receipts must be a list")
        receipt = next((
            value for value in reversed(receipts)
            if isinstance(value, dict)
            and value.get("node") == "7R6QX8GZ3EW3S7PVJ9KQ6E2D4M"
            and isinstance(value.get("fingerprints"), dict)
            and value["fingerprints"].get("email_intel_brief_via") == fingerprint
            and value.get("status") != "failed"
        ), None)
        if not isinstance(receipt, dict):
            raise RuntimeError("real current Brief receipt missing after Evidence action")
        return receipt

    @staticmethod
    def _email_entry_id(items: list[object], subject: str) -> str:
        item = next((value for value in items if isinstance(value, dict) and value.get("subject") == subject), None)
        if not isinstance(item, dict):
            raise ValueError(f"Evidence VIA item subject not found: {subject}")
        entry_id = item.get("email_entry_id")
        if not isinstance(entry_id, str) or not entry_id:
            raise ValueError(f"Evidence VIA item email_entry_id missing: {subject}")
        return entry_id

    def _brief_receipt_count(self, endpoint: str) -> int:
        receipts = self._get(endpoint + "/receipts").get("receipts")
        if not isinstance(receipts, list):
            raise ValueError("Reactor receipts must be a list")
        return len(receipts)

    @staticmethod
    def _safe_failed_brief_receipt(receipt: JsonObject) -> JsonObject:
        """Only receipt metadata safe for Pixie errors; never include agent payloads."""
        fingerprints = receipt.get("fingerprints")
        return {
            "status": receipt.get("status"),
            "input_fingerprints": receipt.get("input_fingerprints") if isinstance(receipt.get("input_fingerprints"), list) else [],
            "fingerprints": fingerprints if isinstance(fingerprints, dict) else {},
            "cost": receipt.get("cost") if isinstance(receipt.get("cost"), dict) else {},
        }

    def _wait_current_brief(self, endpoint: str, daemon: subprocess.Popen[str], stderr_path: Path, evidence: JsonObject, receipt_start: int) -> JsonObject:
        ledger = self._json_object(evidence.get("evidence_ledger"), "Evidence VIA ledger")
        revision, fingerprint = ledger.get("revision"), ledger.get("material_fingerprint")
        if not isinstance(revision, int) or not isinstance(fingerprint, str):
            raise ValueError("Evidence VIA ledger revision/fingerprint missing")
        last_error = ""
        for _ in range(330):
            receipts = self._get(endpoint + "/receipts").get("receipts")
            if not isinstance(receipts, list):
                raise ValueError("Reactor receipts must be a list")
            failed = next((
                receipt for receipt in reversed(receipts[receipt_start:])
                if isinstance(receipt, dict)
                and receipt.get("node") == "7R6QX8GZ3EW3S7PVJ9KQ6E2D4M"
                and receipt.get("status") == "failed"
            ), None)
            if isinstance(failed, dict):
                raise RuntimeError(f"real current Brief receipt failed: {json.dumps(self._safe_failed_brief_receipt(failed), ensure_ascii=False)}")
            if daemon.poll() is not None:
                raise RuntimeError(f"real Reactor daemon exited: {self._daemon_stderr(stderr_path).strip()}")
            try:
                brief = self._get(endpoint + "/via/email-intel-brief")
                source = brief.get("source")
                if isinstance(source, dict) and brief.get("status") == "candidate" and source.get("revision") == revision and source.get("material_fingerprint") == fingerprint:
                    return brief
                last_error = "Brief source has not reached current Evidence ledger"
            except RuntimeError as error:
                if not str(error).startswith("HTTP 404 "):
                    raise
                last_error = str(error)
            time.sleep(1)
        raise RuntimeError(f"real Brief did not reach current Evidence: {last_error}; {self._daemon_stderr(stderr_path)}")
