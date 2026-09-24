"""Atomic workflow storage with optimistic revisions and a hash-chained event log."""

import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .models import ApprovalGate, ArtifactRef, Event, Proposal, PromotionReceipt, Workflow, WorkflowPolicy


class WorkflowConflict(RuntimeError):
    pass


class WorkflowNotFound(FileNotFoundError):
    pass


def canonical_json(value) -> bytes:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", exclude_none=True)
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def atomic_write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def event_hash(event: Event) -> str:
    value = event.model_dump(mode="json", exclude={"sha256"}, exclude_none=True)
    return sha256_bytes(canonical_json(value))


def workflow_hash(workflow: Workflow) -> str:
    value = workflow.model_dump(mode="json", exclude={"last_event_sha256"}, exclude_none=True)
    return sha256_bytes(canonical_json(value))


class InterProcessLock:
    """A small standard-library lock used by CLI and console processes."""

    _registry_guard = threading.Lock()
    _local_locks: dict[str, threading.RLock] = {}
    _held: dict[tuple[str, int], dict] = {}

    def __init__(self, path: Path, timeout: float = 10.0):
        self.path, self.timeout, self.stream = Path(path), timeout, None
        self._key = ""
        self._thread = 0
        self._local_lock = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._key = str(self.path.resolve())
        self._thread = threading.get_ident()
        with self._registry_guard:
            self._local_lock = self._local_locks.setdefault(self._key, threading.RLock())
        if not self._local_lock.acquire(timeout=self.timeout):
            raise WorkflowConflict(f"workflow is locked: {self.path}")
        with self._registry_guard:
            held = self._held.get((self._key, self._thread))
            if held is not None:
                held["count"] += 1
                self.stream = held["stream"]
                return self
        self.stream = self.path.open("a+b")
        if self.stream.tell() == 0:
            self.stream.write(b"0")
            self.stream.flush()
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    self.stream.seek(0)
                    msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self._registry_guard:
                    self._held[(self._key, self._thread)] = {"count": 1, "stream": self.stream}
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    self.stream.close()
                    self.stream = None
                    self._local_lock.release()
                    raise WorkflowConflict(f"workflow is locked: {self.path}")
                time.sleep(0.05)

    def __exit__(self, exc_type, exc, traceback):
        if self.stream is None:
            return
        with self._registry_guard:
            held = self._held[(self._key, self._thread)]
            held["count"] -= 1
            if held["count"]:
                self.stream = None
                self._local_lock.release()
                return
            del self._held[(self._key, self._thread)]
        if os.name == "nt":
            import msvcrt
            self.stream.seek(0)
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        self.stream.close()
        self.stream = None
        self._local_lock.release()


class PackWriteLock:
    """Serialize pack writers and recover an interrupted autopilot promotion."""

    def __init__(self, root: Path, pack: str, timeout: float = 10.0):
        self.root, self.pack = Path(root).resolve(), pack
        self.local = self.root / ".local" / pack
        self.lock = InterProcessLock(self.local / ".pack-write.lock", timeout)
        self.workflow_lock = InterProcessLock(self.local / ".autopilot.lock", timeout)

    def __enter__(self):
        self.lock.__enter__()
        try:
            with self.workflow_lock:
                self.recover()
        except Exception:
            self.lock.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.lock.__exit__(exc_type, exc, traceback)

    def recover(self) -> None:
        marker = self.local / ".pack-transaction.json"
        if not marker.is_file():
            return
        try:
            transaction = json.loads(marker.read_text(encoding="utf-8"))
            if transaction.get("schema_version") != "swisstip.pack-transaction/v2":
                raise WorkflowConflict("unsupported pack transaction marker")
            autopilot = self.local / "autopilot"
            workflow = Workflow.model_validate_json((autopilot / "workflow.json").read_text(encoding="utf-8"))
            event = Event.model_validate(transaction.get("event"))
            if (event.workflow_id != workflow.workflow_id or event.kind != "proposal.promoted"
                    or event.gate is None or event.sha256 != event_hash(event)
                    or event.sha256 != transaction.get("committed_event_sha256")):
                raise WorkflowConflict("pack transaction promotion event is invalid")
            events_path = self.local / "autopilot" / "events.jsonl"
            events = [Event.model_validate_json(line) for line in events_path.read_text(encoding="utf-8").splitlines()
                      if line.strip()]
            previous = None
            for sequence, recorded in enumerate(events, 1):
                if (recorded.workflow_id != workflow.workflow_id or recorded.sequence != sequence
                        or recorded.previous_sha256 != previous or recorded.sha256 != event_hash(recorded)):
                    raise WorkflowConflict("pack transaction event log fails hash-chain verification")
                previous = recorded.sha256
            if len(events) < workflow.event_sequence:
                raise WorkflowConflict("pack transaction event log is shorter than the workflow")
            workflow_tail = events[workflow.event_sequence - 1]
            if (workflow_tail.sha256 != workflow.last_event_sha256
                    or workflow_tail.workflow_sha256 != workflow_hash(workflow)
                    or workflow_tail.workflow_revision != workflow.revision
                    or workflow_tail.state_after != workflow.state):
                raise WorkflowConflict("pack transaction workflow snapshot is not verified by its event log")
            committed = (workflow.event_sequence >= event.sequence and len(events) >= event.sequence
                         and events[event.sequence - 1].sha256 == event.sha256)
            event_bytes_before = transaction.get("event_bytes_before")
            previous_event_sha256 = transaction.get("previous_event_sha256")
            if type(event_bytes_before) is not int or event_bytes_before < 0 or event.previous_sha256 != previous_event_sha256:
                raise WorkflowConflict("pack transaction has invalid event-log recovery metadata")
            event_data = events_path.read_bytes()
            if len(event_data) < event_bytes_before:
                raise WorkflowConflict("pack transaction event log is shorter than its recovery point")
            prefix_lines = [line for line in event_data[:event_bytes_before].splitlines() if line.strip()]
            prefix_sha256 = Event.model_validate_json(prefix_lines[-1]).sha256 if prefix_lines else None
            if prefix_sha256 != previous_event_sha256:
                raise WorkflowConflict("pack transaction recovery point does not match the prior event")
            if not committed and workflow.last_event_sha256 != previous_event_sha256:
                raise WorkflowConflict("uncommitted pack transaction does not match the workflow tail")
            proposal_ref = ArtifactRef.model_validate(transaction.get("proposal"))
            progress = workflow.gates[event.gate]
            if (progress.proposal is None or progress.proposal.sha256 != proposal_ref.sha256
                    or event.metrics.get("proposal_sha256") != proposal_ref.sha256):
                raise WorkflowConflict("pack transaction does not name the approved gate proposal")
            proposal_path = (autopilot / proposal_ref.path).resolve()
            if not proposal_path.is_relative_to(autopilot) or not proposal_path.is_file():
                raise WorkflowConflict("pack transaction proposal artifact is missing")
            proposal_data = proposal_path.read_bytes()
            if len(proposal_data) != proposal_ref.bytes or sha256_bytes(proposal_data) != proposal_ref.sha256:
                raise WorkflowConflict("pack transaction proposal artifact changed")
            proposal = Proposal.model_validate_json(proposal_data)
            if proposal.gate != event.gate:
                raise WorkflowConflict("pack transaction proposal belongs to another gate")
            receipt_ref = next((reference for reference in event.artifacts
                                if reference.schema_version == "swisstip.autopilot-promotion/v1"), None)
            if receipt_ref is None:
                raise WorkflowConflict("pack transaction promotion event has no receipt reference")
            if transaction.get("prepared_artifacts") != [receipt_ref.path]:
                raise WorkflowConflict("pack transaction prepared artifacts differ from the promotion receipt")
            allowed_destinations = {item.destination for item in proposal.promotions}
            readiness_destination = f"releases/{self.pack}/readiness.json"
            if event.gate == ApprovalGate.ACCEPTANCE:
                allowed_destinations.add(readiness_destination)
            destinations = [item.get("destination") for item in transaction.get("writes", [])]
            if (len(destinations) != len(set(destinations))
                    or any(destination not in allowed_destinations for destination in destinations)):
                raise WorkflowConflict("pack transaction contains an unauthorized destination")
            prepared_paths = []
            for relative in transaction.get("prepared_artifacts", []):
                prepared = (autopilot / relative).resolve()
                if not prepared.is_relative_to(autopilot):
                    raise WorkflowConflict("pack transaction prepared artifact leaves the workflow")
                prepared_paths.append(prepared)
            for item in transaction.get("writes", []):
                unresolved_destination = self.root
                for part in Path(item["destination"]).parts:
                    unresolved_destination /= part
                    if unresolved_destination.is_symlink():
                        raise WorkflowConflict("pack transaction destination uses a symlink")
                destination = unresolved_destination.resolve()
                if not destination.is_relative_to(self.root):
                    raise WorkflowConflict("pack transaction destination leaves the workspace")
                if item["destination"] == readiness_destination and item.get("backup") is None:
                    raise WorkflowConflict("pack transaction cannot delete an unbacked readiness record")
                if committed:
                    continue
                backup = item.get("backup")
                if backup is None:
                    if item.get("backup_sha256") is not None:
                        raise WorkflowConflict("pack transaction has a hash for a missing backup")
                    destination.unlink(missing_ok=True)
                else:
                    unresolved_backup = self.local
                    for part in Path(backup).parts:
                        unresolved_backup /= part
                        if unresolved_backup.is_symlink():
                            raise WorkflowConflict("pack transaction backup uses a symlink")
                    backup_path = unresolved_backup.resolve()
                    if not backup_path.is_relative_to(self.local) or not backup_path.is_file():
                        raise WorkflowConflict("pack transaction backup is missing")
                    backup_data = backup_path.read_bytes()
                    if sha256_bytes(backup_data) != item.get("backup_sha256"):
                        raise WorkflowConflict("pack transaction backup hash differs")
                    atomic_write(destination, backup_data)
            if not committed:
                tail_lines = [line for line in event_data[event_bytes_before:].splitlines() if line.strip()]
                if len(tail_lines) > 1 or tail_lines and Event.model_validate_json(tail_lines[0]).sha256 != event.sha256:
                    raise WorkflowConflict("pack transaction has an unexpected event-log tail")
                with events_path.open("r+b") as stream:
                    stream.truncate(event_bytes_before)
                for prepared in prepared_paths:
                    prepared.unlink(missing_ok=True)
            else:
                receipt_data = (autopilot / receipt_ref.path).read_bytes()
                if len(receipt_data) != receipt_ref.bytes or sha256_bytes(receipt_data) != receipt_ref.sha256:
                    raise WorkflowConflict("committed promotion receipt changed")
                receipt = PromotionReceipt.model_validate_json(receipt_data)
                if receipt.gate != event.gate or receipt.proposal_sha256 != proposal_ref.sha256:
                    raise WorkflowConflict("committed promotion receipt differs from its event")
            transaction_dir = (self.local / transaction["transaction_dir"]).resolve()
            if not transaction_dir.is_relative_to(self.local):
                raise WorkflowConflict("pack transaction directory leaves the pack")
            shutil.rmtree(transaction_dir, ignore_errors=True)
            marker.unlink(missing_ok=True)
        except WorkflowConflict:
            raise
        except Exception as exc:
            raise WorkflowConflict(f"cannot recover interrupted pack transaction: {exc}") from exc


class WorkflowStore:
    def __init__(self, root: Path, pack: str):
        self.root = Path(root).resolve()
        self.pack = pack
        self.directory = self.root / ".local" / pack / "autopilot"
        self.workflow_path = self.directory / "workflow.json"
        self.policy_path = self.directory / "policy.json"
        self.events_path = self.directory / "events.jsonl"
        self.lock_path = self.directory.parent / ".autopilot.lock"
        self.transaction_path = self.directory / ".workflow-transaction.json"

    @contextmanager
    def locked(self):
        with PackWriteLock(self.root, self.pack), InterProcessLock(self.lock_path):
            self._recover_workflow_transaction_unlocked()
            yield

    @contextmanager
    def workflow_locked_under_pack(self):
        """Workflow lock for callers that already hold the pack lock."""
        with InterProcessLock(self.lock_path):
            self._recover_workflow_transaction_unlocked()
            yield

    @contextmanager
    def pack_locked(self):
        with PackWriteLock(self.root, self.pack):
            yield

    def artifact(self, path: Path, *, schema_version: str | None = None,
                 media_type: str = "application/json") -> ArtifactRef:
        data = path.read_bytes()
        return ArtifactRef(path=path.relative_to(self.directory).as_posix(), sha256=sha256_bytes(data),
                           bytes=len(data), media_type=media_type, schema_version=schema_version)

    def reference(self, relative: Path, value, *, schema_version: str | None = None,
                  media_type: str = "application/json") -> tuple[ArtifactRef, bytes]:
        data = value if isinstance(value, bytes) else canonical_json(value)
        normalized = ArtifactRef(path=relative.as_posix(), sha256=sha256_bytes(data), bytes=len(data),
                                 media_type=media_type, schema_version=schema_version)
        return normalized, data

    def initialize(self, workflow: Workflow, policy: WorkflowPolicy, event: Event,
                   brief: dict, policy_data: bytes) -> Workflow:
        with self.locked():
            if self.workflow_path.exists():
                raise WorkflowConflict(f"workflow already exists for {self.pack}")
            if workflow.policy.sha256 != sha256_bytes(policy_data) or workflow.policy.bytes != len(policy_data):
                raise ValueError("workflow policy reference does not match the policy bytes")
            if (event.sha256 != event_hash(event) or workflow.last_event_sha256 != event.sha256
                    or event.workflow_sha256 != workflow_hash(workflow)):
                raise ValueError("initial event hash does not match the workflow")
            parent = self.directory.parent
            parent.mkdir(parents=True, exist_ok=True)
            temporary = parent / f".{self.directory.name}.initialize-{uuid.uuid4().hex}"
            try:
                temporary.mkdir()
                atomic_write(temporary / "policy.json", policy_data)
                atomic_write(temporary / "brief.json", canonical_json(brief))
                atomic_write(temporary / "events.jsonl", canonical_json(event))
                atomic_write(temporary / "workflow.json", canonical_json(workflow))
                if self.directory.exists():
                    if any(self.directory.iterdir()):
                        raise WorkflowConflict(f"incomplete workflow directory already exists for {self.pack}")
                    self.directory.rmdir()
                os.replace(temporary, self.directory)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        return self.load_verified()

    def load(self) -> Workflow:
        return self.load_verified()

    def _load_unverified(self) -> Workflow:
        if not self.workflow_path.is_file():
            raise WorkflowNotFound(f"no autopilot workflow for {self.pack}")
        return Workflow.model_validate_json(self.workflow_path.read_text(encoding="utf-8"))

    def load_policy(self) -> WorkflowPolicy:
        workflow = self.load_verified()
        data = self._read_artifact(workflow.policy)
        return WorkflowPolicy.model_validate_json(data)

    def commit(self, expected_revision: int, workflow: Workflow, event: Event,
               artifacts: dict[Path, tuple[ArtifactRef, bytes]] | None = None) -> Workflow:
        with self.locked():
            self.commit_unlocked(expected_revision, workflow, event, artifacts)
        return self.load_verified()

    def commit_unlocked(self, expected_revision: int, workflow: Workflow, event: Event,
                        artifacts: dict[Path, tuple[ArtifactRef, bytes]] | None = None) -> None:
        """Commit while the caller holds `locked()`; used to bind pack writes and workflow receipt."""
        current = self._load_verified_unlocked()
        if current.revision != expected_revision:
            raise WorkflowConflict(f"workflow revision is {current.revision}, expected {expected_revision}")
        if workflow.revision != expected_revision + 1:
            raise ValueError("a committed workflow must advance the revision exactly once")
        if event.sequence != current.event_sequence + 1 or event.workflow_revision != workflow.revision:
            raise ValueError("event sequence and workflow revision do not match the transition")
        if event.previous_sha256 != current.last_event_sha256 or event.sha256 != workflow.last_event_sha256:
            raise ValueError("event hash chain does not match the workflow")
        if event.sha256 != event_hash(event) or event.workflow_sha256 != workflow_hash(workflow):
            raise ValueError("event SHA-256 does not match its content")
        workflow = Workflow.model_validate(workflow.model_dump(mode="python"))
        prepared = artifacts or {}
        for relative, (reference, data) in prepared.items():
            if reference.path != relative.as_posix() or reference.sha256 != sha256_bytes(data) or reference.bytes != len(data):
                raise ValueError(f"artifact reference does not match bytes: {relative.as_posix()}")
            if (self.directory / relative).exists():
                raise WorkflowConflict(f"immutable artifact already exists: {relative.as_posix()}")
        old_event_bytes = self.events_path.stat().st_size if self.events_path.is_file() else 0
        if self.transaction_path.exists():
            raise WorkflowConflict("an earlier workflow transaction still needs recovery")
        atomic_write(self.transaction_path, canonical_json({
            "schema_version": "swisstip.workflow-transaction/v1",
            "workflow_id": current.workflow_id,
            "previous_event_sha256": current.last_event_sha256,
            "event_bytes_before": old_event_bytes,
            "event": event.model_dump(mode="json", exclude_none=True),
            "prepared_artifacts": [relative.as_posix() for relative in prepared],
        }))
        written = []
        committed = False
        try:
            for relative, (_, data) in prepared.items():
                path = self.directory / relative
                atomic_write(path, data)
                written.append(path)
            self._append_event(event)
            atomic_write(self.workflow_path, canonical_json(workflow))
            committed = True
        except Exception:
            if not committed and self.events_path.is_file():
                with self.events_path.open("r+b") as stream:
                    stream.truncate(old_event_bytes)
            if not committed:
                for path in written:
                    path.unlink(missing_ok=True)
                self.transaction_path.unlink(missing_ok=True)
            raise
        try:
            self.transaction_path.unlink(missing_ok=True)
        except OSError:
            # The workflow snapshot is the commit point. Authenticated recovery removes a stale marker later.
            pass

    def _recover_workflow_transaction_unlocked(self) -> None:
        if not self.transaction_path.is_file():
            return
        try:
            transaction = json.loads(self.transaction_path.read_text(encoding="utf-8"))
            if transaction.get("schema_version") != "swisstip.workflow-transaction/v1":
                raise WorkflowConflict("unsupported workflow transaction marker")
            workflow = self._load_unverified()
            event = Event.model_validate(transaction.get("event"))
            if (transaction.get("workflow_id") != workflow.workflow_id
                    or event.workflow_id != workflow.workflow_id
                    or event.sha256 != event_hash(event)
                    or event.previous_sha256 != transaction.get("previous_event_sha256")):
                raise WorkflowConflict("workflow transaction identity or event hash is invalid")
            event_bytes_before = transaction.get("event_bytes_before")
            if type(event_bytes_before) is not int or event_bytes_before < 0 or not self.events_path.is_file():
                raise WorkflowConflict("workflow transaction recovery point is invalid")
            event_data = self.events_path.read_bytes()
            if len(event_data) < event_bytes_before:
                raise WorkflowConflict("workflow event log is shorter than its recovery point")
            prefix_lines = [line for line in event_data[:event_bytes_before].splitlines() if line.strip()]
            previous = Event.model_validate_json(prefix_lines[-1]).sha256 if prefix_lines else None
            if previous != event.previous_sha256:
                raise WorkflowConflict("workflow transaction recovery point differs from the prior event")
            events = self._events_unverified()
            committed = (workflow.event_sequence >= event.sequence
                         and len(events) >= event.sequence
                         and events[event.sequence - 1].sha256 == event.sha256)
            if workflow.last_event_sha256 == event.previous_sha256:
                committed = False
            elif not committed:
                raise WorkflowConflict("workflow transaction event is neither pending nor committed")
            event_paths = {reference.path for reference in event.artifacts}
            prepared_paths = []
            for relative in transaction.get("prepared_artifacts", []):
                if relative not in event_paths:
                    raise WorkflowConflict("workflow transaction prepared artifact is absent from its event")
                path = (self.directory / relative).resolve()
                if not path.is_relative_to(self.directory):
                    raise WorkflowConflict("workflow transaction artifact leaves the workflow directory")
                prepared_paths.append(path)
            if committed:
                for reference in event.artifacts:
                    if reference.path in transaction.get("prepared_artifacts", []):
                        self._read_artifact(reference)
            else:
                tail_lines = [line for line in event_data[event_bytes_before:].splitlines() if line.strip()]
                if len(tail_lines) > 1 or tail_lines and Event.model_validate_json(tail_lines[0]).sha256 != event.sha256:
                    raise WorkflowConflict("workflow transaction has an unexpected event tail")
                with self.events_path.open("r+b") as stream:
                    stream.truncate(event_bytes_before)
                for path in prepared_paths:
                    path.unlink(missing_ok=True)
            self.transaction_path.unlink(missing_ok=True)
        except WorkflowConflict:
            raise
        except Exception as exc:
            raise WorkflowConflict(f"cannot recover interrupted workflow transaction: {exc}") from exc

    def events(self, limit: int = 100) -> list[Event]:
        workflow = self.load_verified()
        events = self._events_unverified()
        return events[-limit:] if limit else events

    def load_verified(self) -> Workflow:
        with self.locked():
            return self._load_verified_unlocked()

    def _load_verified_unlocked(self) -> Workflow:
        workflow = self._load_unverified()
        self._read_artifact(workflow.policy)
        events = self._events_unverified()
        if len(events) != workflow.event_sequence:
            raise WorkflowConflict(f"event log has {len(events)} entries, workflow expects {workflow.event_sequence}")
        previous = None
        for sequence, event in enumerate(events, 1):
            if event.workflow_id != workflow.workflow_id or event.sequence != sequence:
                raise WorkflowConflict(f"event {sequence} does not belong to this workflow sequence")
            if event.previous_sha256 != previous or event.sha256 != event_hash(event):
                raise WorkflowConflict(f"event {sequence} fails hash-chain verification")
            for artifact in event.artifacts:
                self._read_artifact(artifact)
            previous = event.sha256
        if previous != workflow.last_event_sha256:
            raise WorkflowConflict("workflow does not name the verified event-log tail")
        if not events or events[-1].workflow_revision != workflow.revision or events[-1].state_after != workflow.state:
            raise WorkflowConflict("workflow revision or state disagrees with the event-log tail")
        if events[-1].workflow_sha256 != workflow_hash(workflow):
            raise WorkflowConflict("workflow snapshot changed after its final event")
        for progress in workflow.gates.values():
            if progress.proposal is not None:
                self._read_artifact(progress.proposal)
            if progress.decision is not None:
                self._read_artifact(progress.decision)
        return workflow

    def _events_unverified(self) -> list[Event]:
        if not self.events_path.is_file():
            return []
        lines = [line for line in self.events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return [Event.model_validate_json(line) for line in lines]

    def _read_artifact(self, reference: ArtifactRef) -> bytes:
        path = (self.directory / reference.path).resolve()
        if not path.is_relative_to(self.directory) or not path.is_file():
            raise WorkflowConflict(f"workflow artifact is missing or outside its directory: {reference.path}")
        data = path.read_bytes()
        if len(data) != reference.bytes or sha256_bytes(data) != reference.sha256:
            raise WorkflowConflict(f"workflow artifact changed: {reference.path}")
        return data

    def read_artifact(self, reference: ArtifactRef) -> bytes:
        self.load_verified()
        return self._read_artifact(reference)

    def _append_event(self, event: Event) -> None:
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("ab") as stream:
            stream.write(canonical_json(event))
            stream.flush()
            os.fsync(stream.fileno())
