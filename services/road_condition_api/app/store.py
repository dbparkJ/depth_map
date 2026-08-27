from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


_JOB_ID = re.compile(r"^[a-f0-9]{32}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.jobs_root = self.root / "jobs"
        self.jobs_root.mkdir(parents=True, exist_ok=True)

    def _validate_id(self, job_id: str) -> str:
        if not _JOB_ID.fullmatch(job_id):
            raise ValueError("invalid job id")
        return job_id

    def job_dir(self, job_id: str) -> Path:
        return self.jobs_root / self._validate_id(job_id)

    def result_dir(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "result"

    def _atomic_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)

    def create(self, request: dict[str, Any]) -> dict[str, Any]:
        job_id = uuid4().hex
        directory = self.job_dir(job_id)
        directory.mkdir(parents=True, exist_ok=False)
        now = utc_now()
        status = {
            "job_id": job_id,
            "state": "queued",
            "created_at": now,
            "updated_at": now,
            "progress": 0.0,
            "message": "queued",
            "request": request,
            "artifacts": {},
            "error": None,
        }
        self._atomic_json(directory / "request.json", request)
        self._atomic_json(directory / "status.json", status)
        return status

    def read_status(self, job_id: str) -> dict[str, Any]:
        path = self.job_dir(job_id) / "status.json"
        if not path.is_file():
            raise FileNotFoundError(job_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def update_status(self, job_id: str, **changes: Any) -> dict[str, Any]:
        status = self.read_status(job_id)
        status.update(changes)
        status["updated_at"] = utc_now()
        self._atomic_json(self.job_dir(job_id) / "status.json", status)
        return status

    def list_statuses(self, limit: int = 50) -> list[dict[str, Any]]:
        statuses: list[dict[str, Any]] = []
        for path in self.jobs_root.glob("*/status.json"):
            try:
                statuses.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        statuses.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        return statuses[: max(1, min(int(limit), 200))]

    def read_result_json(self, job_id: str, filename: str) -> Any:
        allowed = {
            "summary.json",
            "defects.json",
            "segments.json",
            "surface_preview.json",
            "defects.local.geojson",
            "defects.enu.geojson",
        }
        if filename not in allowed:
            raise ValueError("unsupported result filename")
        path = self.result_dir(job_id) / filename
        if not path.is_file():
            raise FileNotFoundError(path)
        return json.loads(path.read_text(encoding="utf-8"))

    def delete(self, job_id: str) -> None:
        directory = self.job_dir(job_id)
        if not directory.is_dir():
            raise FileNotFoundError(job_id)
        shutil.rmtree(directory)
