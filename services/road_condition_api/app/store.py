from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import threading
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
        self._review_lock = threading.RLock()

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

    @staticmethod
    def _canonical_hash(payload: Any) -> str:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _review_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "review_bundle.json"

    def _initialize_reviews(self, job_id: str) -> dict[str, Any]:
        defects = self.read_result_json(job_id, "defects.json")
        summary = self.read_result_json(job_id, "summary.json")
        if not isinstance(defects, list):
            raise ValueError("defects result must be a list")
        records: dict[str, Any] = {}
        for defect in defects:
            if not isinstance(defect, dict) or not isinstance(defect.get("defect_id"), str):
                raise ValueError("each raw defect must have a string defect_id")
            defect_id = defect["defect_id"]
            if defect_id in records:
                raise ValueError("raw defect IDs must be unique")
            records[defect_id] = {
                "state": "pending",
                "version": 0,
                "raw_prediction": copy.deepcopy(defect),
                "current_annotation": copy.deepcopy(defect),
                "last_event_id": None,
            }
        now = utc_now()
        bundle = {
            "format_version": 1,
            "job_id": job_id,
            "created_at": now,
            "updated_at": now,
            "raw_prediction_source": "result/defects.json",
            "raw_prediction_sha256": self._canonical_hash(defects),
            "scoring_profile": (summary.get("scoring_profile") or {}),
            "automatic_approval_enabled": False,
            "defects": records,
            "events": [],
        }
        self._atomic_json(self._review_path(job_id), bundle)
        return bundle

    def read_reviews(self, job_id: str) -> dict[str, Any]:
        with self._review_lock:
            path = self._review_path(job_id)
            bundle = (
                json.loads(path.read_text(encoding="utf-8"))
                if path.is_file()
                else self._initialize_reviews(job_id)
            )
            raw = self.read_result_json(job_id, "defects.json")
            if self._canonical_hash(raw) != bundle.get("raw_prediction_sha256"):
                raise RuntimeError("raw prediction changed after review initialization")
            return bundle

    def apply_review(
        self,
        job_id: str,
        defect_id: str,
        *,
        actor: str,
        action: str,
        reason: str,
        expected_version: int,
        after: dict[str, Any] | None,
    ) -> dict[str, Any]:
        with self._review_lock:
            bundle = self.read_reviews(job_id)
            record = bundle["defects"].get(defect_id)
            if record is None:
                raise FileNotFoundError(defect_id)
            if int(record["version"]) != expected_version:
                raise RuntimeError("review version conflict")
            before = copy.deepcopy(record["current_annotation"])
            if action == "modified":
                if not isinstance(after, dict):
                    raise ValueError("modified review requires a complete after object")
                if after.get("defect_id") != defect_id:
                    raise ValueError("modified defect_id must remain unchanged")
                current = copy.deepcopy(after)
            else:
                if after is not None:
                    raise ValueError("after is only allowed for modified reviews")
                current = copy.deepcopy(before)
            event_id = uuid4().hex
            event = {
                "event_id": event_id,
                "defect_id": defect_id,
                "actor": actor,
                "action": action,
                "before": before,
                "after": copy.deepcopy(current),
                "created_at": utc_now(),
                "reason": reason,
                "previous_version": expected_version,
                "new_version": expected_version + 1,
                "scoring_profile": copy.deepcopy(bundle.get("scoring_profile") or {}),
            }
            record.update(
                {
                    "state": action,
                    "version": expected_version + 1,
                    "current_annotation": current,
                    "last_event_id": event_id,
                }
            )
            bundle["events"].append(event)
            bundle["updated_at"] = event["created_at"]
            self._atomic_json(self._review_path(job_id), bundle)
            return {"record": copy.deepcopy(record), "event": event}

    def delete(self, job_id: str) -> None:
        directory = self.job_dir(job_id)
        if not directory.is_dir():
            raise FileNotFoundError(job_id)
        shutil.rmtree(directory)
