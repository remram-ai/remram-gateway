from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from .redaction import redact_text


def utc_now() -> str:
    return datetime.now(tz=UTC).isoformat()


class JobStore:
    def __init__(self, jobs_dir: Path, max_workers: int = 2) -> None:
        self.jobs_dir = jobs_dir
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="moltbox-debug")
        self._futures: dict[str, Future[dict]] = {}
        self._lock = threading.Lock()
        self.jobs_dir.mkdir(parents=True, exist_ok=True)

    def _job_path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.json"

    def _log_path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.log"

    def create(self, operation: str, runtime: str) -> dict:
        job_id = uuid.uuid4().hex
        payload = {
            "job_id": job_id,
            "operation": operation,
            "runtime": runtime,
            "status": "queued",
            "ok": None,
            "stdout": "",
            "stderr": "",
            "artifacts": [],
            "started_at": None,
            "finished_at": None,
            "exit_code": None,
            "created_at": utc_now(),
        }
        self._write(job_id, payload)
        self._log_path(job_id).touch()
        return payload

    def submit(self, operation: str, runtime: str, handler: Callable[[str], dict]) -> dict:
        job = self.create(operation=operation, runtime=runtime)
        job_id = job["job_id"]

        def runner() -> dict:
            self._update(job_id, status="running", started_at=utc_now())
            try:
                result = handler(job_id)
                payload = {
                    **self.get(job_id),
                    **result,
                    "status": "succeeded" if result.get("ok") else "failed",
                    "finished_at": utc_now(),
                }
            except Exception as exc:  # noqa: BLE001
                payload = {
                    **self.get(job_id),
                    "ok": False,
                    "status": "failed",
                    "stdout": "",
                    "stderr": redact_text(str(exc)),
                    "artifacts": [],
                    "exit_code": 1,
                    "finished_at": utc_now(),
                }
            self._write(job_id, payload)
            return payload

        future = self.executor.submit(runner)
        with self._lock:
            self._futures[job_id] = future
        return job

    def append_log(self, job_id: str, chunk: str) -> None:
        with self._log_path(job_id).open("a", encoding="utf-8") as handle:
            handle.write(redact_text(chunk))

    def _write(self, job_id: str, payload: dict) -> None:
        self._job_path(job_id).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def _update(self, job_id: str, **kwargs: object) -> None:
        payload = self.get(job_id)
        payload.update(kwargs)
        self._write(job_id, payload)

    def get(self, job_id: str) -> dict:
        path = self._job_path(job_id)
        if not path.is_file():
            raise FileNotFoundError(job_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def tail_output(self, job_id: str, tail_lines: int) -> dict:
        payload = self.get(job_id)
        log_path = self._log_path(job_id)
        text = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
        payload["output_tail"] = "\n".join(text.splitlines()[-tail_lines:])
        return payload
