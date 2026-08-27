from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_EXTERNAL_JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class RimmsContractStore:
    """Persistent, contract-only RIMMS ingress with no external network access."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve() / "integrations" / "rimms"
        self.jobs_root = self.root / "jobs"
        self.index_path = self.root / "idempotency_index.json"
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    @staticmethod
    def _validate_external_job_id(external_job_id: str) -> str:
        if not _EXTERNAL_JOB_ID.fullmatch(external_job_id):
            raise ValueError("invalid external_job_id")
        return external_job_id

    @staticmethod
    def _key_hash(idempotency_key: str) -> str:
        if not _IDEMPOTENCY_KEY.fullmatch(idempotency_key):
            raise ValueError(
                "Idempotency-Key must be 8-128 safe ASCII characters"
            )
        return hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()

    def _job_dir(self, external_job_id: str) -> Path:
        return self.jobs_root / self._validate_external_job_id(external_job_id)

    @staticmethod
    def _atomic_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)

    def _read_index(self) -> dict[str, dict[str, str]]:
        if not self.index_path.is_file():
            return {}
        payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("RIMMS idempotency index is invalid")
        if any(
            not isinstance(key, str) or not isinstance(value, dict)
            for key, value in payload.items()
        ):
            raise RuntimeError("RIMMS idempotency index entries are invalid")
        return payload

    def create(self, request: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        external_job_id = self._validate_external_job_id(str(request["external_job_id"]))
        key_hash = self._key_hash(idempotency_key)
        request_hash = _canonical_hash(request)
        with self._lock:
            index = self._read_index()
            indexed = index.get(key_hash)
            if indexed is not None:
                if (
                    indexed.get("external_job_id") == external_job_id
                    and indexed.get("request_sha256") == request_hash
                ):
                    replay = self.read(external_job_id)
                    replay["idempotency_replayed"] = True
                    return replay
                raise RuntimeError("idempotency key was already used for another request")

            directory = self._job_dir(external_job_id)
            if directory.exists():
                raise RuntimeError("external_job_id already exists with another idempotency key")
            directory.mkdir(parents=True, exist_ok=False)
            now = _utc_now()
            status = {
                "format_version": 1,
                "request_contract_version": request["contract_version"],
                "result_contract_version": request["expected_result_contract_version"],
                "external_job_id": external_job_id,
                "state": "awaiting_connector_configuration",
                "created_at": now,
                "updated_at": now,
                "request_sha256": request_hash,
                "idempotency_key_sha256": key_hash,
                "idempotency_replayed": False,
                "request": copy.deepcopy(request),
                "execution": {
                    "started": False,
                    "object_uri_access": "disabled_not_configured",
                    "authentication": "N/A_not_configured",
                    "blockers": [
                        "RoadInventory-MMS authentication is not configured.",
                        "Object storage connector and credentials are not configured.",
                    ],
                },
                "polling": {
                    "enabled": True,
                    "status_path": (
                        f"/api/v1/integrations/rimms/jobs/{external_job_id}"
                    ),
                },
                "callback_delivery": {
                    "enabled": False,
                    "attempt_count": 0,
                    "status": "N/A_polling_only",
                },
                "result": {
                    "contract_version": request["expected_result_contract_version"],
                    "result_manifest_uri": None,
                    "summary_uri": None,
                    "report_uri": None,
                },
                "source_of_truth": {
                    "survey_route_identifiers": "RoadInventory-MMS",
                    "raw_analysis_predictions": "road-condition analysis service",
                    "reviewed_defect_sync": "N/A_direction_not_agreed",
                },
            }
            self._atomic_json(directory / "request.json", request)
            self._atomic_json(directory / "status.json", status)
            index[key_hash] = {
                "external_job_id": external_job_id,
                "request_sha256": request_hash,
            }
            self._atomic_json(self.index_path, index)
            return copy.deepcopy(status)

    def read(self, external_job_id: str) -> dict[str, Any]:
        path = self._job_dir(external_job_id) / "status.json"
        if not path.is_file():
            raise FileNotFoundError(external_job_id)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("RIMMS status is invalid")
        return payload

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for path in self.jobs_root.glob("*/status.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                records.append(payload)
        records.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        return records[: max(1, min(int(limit), 200))]
