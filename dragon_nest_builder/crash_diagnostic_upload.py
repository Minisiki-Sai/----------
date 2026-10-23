from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .live_dps.supabase_score_sync import TeamScoreSupabaseSyncService


STATE_SCHEMA = "dragon-nest-crash-upload-state-v1"
PACKAGE_SCHEMA = "dragon-nest-crash-package-v1"
MAX_PACKAGE_BYTES = 64 * 1024 * 1024
UPLOADABLE_TRIGGERS = frozenset({
    "previous-unclean-exit",
    "backend-unexpected-exit",
})


def default_crash_diagnostic_root() -> Path:
    return (
        Path.home()
        / "Documents"
        / "DragonNestDpsCapture"
        / "crash_diagnostics"
    )


@dataclass(frozen=True, slots=True)
class CrashPackageMetadata:
    package_hash: str
    file_size: int
    modified_ns: int
    app_version: str
    trigger: str
    created_epoch_ms: int


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _read_package_report(path: Path) -> tuple[str, str, int] | None:
    try:
        with zipfile.ZipFile(path, "r") as archive:
            info = archive.getinfo("report.json")
            if info.file_size <= 0 or info.file_size > 256 * 1024:
                return None
            report = json.loads(archive.read(info).decode("utf-8-sig"))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return None
    if not isinstance(report, dict) or report.get("schema") != PACKAGE_SCHEMA:
        return None
    trigger = str(report.get("trigger") or "").strip()
    if trigger not in UPLOADABLE_TRIGGERS:
        return None
    session = report.get("session")
    if not isinstance(session, dict):
        session = {}
    app_version = str(session.get("version") or "unknown").strip()[:32]
    try:
        created_epoch_ms = int(report.get("created_epoch_ms") or 0)
    except (TypeError, ValueError):
        return None
    if created_epoch_ms <= 0:
        return None
    return app_version or "unknown", trigger, created_epoch_ms


class CrashDiagnosticUploader:
    """Low-frequency, retrying uploader for completed crash ZIP packages."""

    def __init__(
        self,
        connection_provider: Callable[[], Any],
        *,
        diagnostic_root: Path | None = None,
        service_factory: Callable[..., TeamScoreSupabaseSyncService] = (
            TeamScoreSupabaseSyncService
        ),
        status_callback: Callable[[str, str], None] | None = None,
        initial_delay_seconds: float = 3.0,
        scan_interval_seconds: float = 10.0,
    ) -> None:
        self.connection_provider = connection_provider
        self.diagnostic_root = Path(
            diagnostic_root or default_crash_diagnostic_root()
        )
        self.packages_dir = self.diagnostic_root / "packages"
        self.state_path = self.diagnostic_root / "crash-upload-state.json"
        self.service_factory = service_factory
        self.status_callback = status_callback
        self.initial_delay_seconds = max(0.0, float(initial_delay_seconds))
        self.scan_interval_seconds = max(5.0, float(scan_interval_seconds))
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._state = self._load_state()
        self._thread = threading.Thread(
            target=self._run,
            name="crash-diagnostic-cloud-upload",
            daemon=True,
        )

    def _load_state(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            payload = {}
        if not isinstance(payload, dict) or payload.get("schema") != STATE_SCHEMA:
            payload = {"schema": STATE_SCHEMA, "packages": {}}
        if not isinstance(payload.get("packages"), dict):
            payload["packages"] = {}
        return payload

    def _save_state(self) -> None:
        with self._lock:
            packages = self._state.get("packages", {})
            if isinstance(packages, dict) and len(packages) > 128:
                ordered = sorted(
                    packages.items(),
                    key=lambda item: float(
                        item[1].get("updated_epoch", 0)
                        if isinstance(item[1], dict)
                        else 0
                    ),
                    reverse=True,
                )[:128]
                self._state["packages"] = dict(ordered)
            _atomic_write_json(self.state_path, self._state)

    def _notify(self, event: str, message: str) -> None:
        callback = self.status_callback
        if callback is None:
            return
        try:
            callback(str(event), str(message))
        except Exception:
            pass

    def _metadata_for(
        self,
        path: Path,
        previous: dict[str, Any] | None,
    ) -> CrashPackageMetadata | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        size = int(stat.st_size)
        modified_ns = int(stat.st_mtime_ns)
        if size <= 0 or size > MAX_PACKAGE_BYTES:
            return None
        if (
            isinstance(previous, dict)
            and int(previous.get("file_size") or 0) == size
            and int(previous.get("modified_ns") or 0) == modified_ns
            and len(str(previous.get("package_hash") or "")) == 64
            and str(previous.get("trigger") or "") in UPLOADABLE_TRIGGERS
        ):
            return CrashPackageMetadata(
                package_hash=str(previous["package_hash"]),
                file_size=size,
                modified_ns=modified_ns,
                app_version=str(previous.get("app_version") or "unknown"),
                trigger=str(previous["trigger"]),
                created_epoch_ms=int(previous.get("created_epoch_ms") or 0),
            )
        report = _read_package_report(path)
        if report is None:
            return None
        app_version, trigger, created_epoch_ms = report
        return CrashPackageMetadata(
            package_hash=_sha256_file(path),
            file_size=size,
            modified_ns=modified_ns,
            app_version=app_version,
            trigger=trigger,
            created_epoch_ms=created_epoch_ms,
        )

    @staticmethod
    def _connection_fields(connection: Any) -> tuple[str, str, str]:
        if isinstance(connection, dict):
            return (
                str(connection.get("endpoint") or "").strip(),
                str(connection.get("upload_token") or "").strip(),
                str(connection.get("installation_id") or "").strip(),
            )
        return (
            str(getattr(connection, "endpoint", "") or "").strip(),
            str(getattr(connection, "upload_token", "") or "").strip(),
            str(getattr(connection, "installation_id", "") or "").strip(),
        )

    def upload_pending_once(self, *, now: float | None = None) -> int:
        """Upload at most two pending packages and return the success count."""

        current = float(time.time() if now is None else now)
        try:
            candidates = sorted(
                self.packages_dir.glob("dragon-nest-crash-*.zip"),
                key=lambda value: value.stat().st_mtime_ns,
            )
        except OSError:
            return 0
        uploaded = 0
        attempted = 0
        for path in candidates:
            key = path.name
            with self._lock:
                raw_previous = self._state["packages"].get(key)
                previous = raw_previous if isinstance(raw_previous, dict) else {}
            metadata = self._metadata_for(path, previous)
            if metadata is None:
                continue
            unchanged = (
                int(previous.get("file_size") or 0) == metadata.file_size
                and int(previous.get("modified_ns") or 0) == metadata.modified_ns
                and str(previous.get("package_hash") or "")
                == metadata.package_hash
            )
            if (
                unchanged
                and bool(previous.get("uploaded"))
                and bool(previous.get("metadata_registered", True))
            ):
                continue
            if unchanged and float(previous.get("next_retry_epoch") or 0) > current:
                continue
            if attempted >= 2:
                break
            attempted += 1
            try:
                connection = self.connection_provider()
                endpoint, token, installation_id = self._connection_fields(connection)
                if not endpoint or not token or not installation_id:
                    raise RuntimeError("CloudBase 闪退诊断上传配置尚未就绪")
                service = self.service_factory(endpoint, token, installation_id)
                result = service.upload_crash_diagnostic(
                    path,
                    package_hash=metadata.package_hash,
                    file_size=metadata.file_size,
                    app_version=metadata.app_version,
                    trigger=metadata.trigger,
                    created_epoch_ms=metadata.created_epoch_ms,
                )
                metadata_registered = bool(
                    result.get("metadata_registered", True)
                )
                record = {
                    "file_size": metadata.file_size,
                    "modified_ns": metadata.modified_ns,
                    "package_hash": metadata.package_hash,
                    "app_version": metadata.app_version,
                    "trigger": metadata.trigger,
                    "created_epoch_ms": metadata.created_epoch_ms,
                    "uploaded": True,
                    "diagnostic_id": str(result.get("diagnostic_id") or ""),
                    "object_id": str(result.get("object_id") or ""),
                    "cloud_object_id": str(result.get("cloud_object_id") or ""),
                    "metadata_registered": metadata_registered,
                    "attempts": int(previous.get("attempts") or 0) + 1,
                    "last_error": "",
                    # If migration 018 has not been applied yet, the bytes are
                    # already safe in private storage. Retry only hourly so the
                    # searchable metadata is filled in after deployment.
                    "next_retry_epoch": 0 if metadata_registered else current + 3600,
                    "updated_epoch": current,
                }
                with self._lock:
                    self._state["packages"][key] = record
                self._save_state()
                uploaded += 1
                self._notify(
                    "uploaded" if metadata_registered else "metadata-pending",
                    f"{record['diagnostic_id']} -> {record['cloud_object_id']}",
                )
            except Exception as exc:
                attempts = int(previous.get("attempts") or 0) + 1
                delay = min(3600.0, 15.0 * (2 ** min(attempts, 8)))
                record = {
                    "file_size": metadata.file_size,
                    "modified_ns": metadata.modified_ns,
                    "package_hash": metadata.package_hash,
                    "app_version": metadata.app_version,
                    "trigger": metadata.trigger,
                    "created_epoch_ms": metadata.created_epoch_ms,
                    "uploaded": False,
                    "diagnostic_id": str(previous.get("diagnostic_id") or ""),
                    "object_id": str(previous.get("object_id") or ""),
                    "cloud_object_id": str(previous.get("cloud_object_id") or ""),
                    "metadata_registered": False,
                    "attempts": attempts,
                    "last_error": str(exc)[:500],
                    "next_retry_epoch": current + delay,
                    "updated_epoch": current,
                }
                with self._lock:
                    self._state["packages"][key] = record
                self._save_state()
                self._notify("retry", f"{key}: {exc}")
        return uploaded

    def status(self) -> dict[str, Any]:
        with self._lock:
            rows = [
                value
                for value in self._state.get("packages", {}).values()
                if isinstance(value, dict)
            ]
        rows.sort(key=lambda row: float(row.get("updated_epoch") or 0), reverse=True)
        latest = rows[0] if rows else {}
        return {
            "schema": STATE_SCHEMA,
            "running": self._thread.is_alive() and not self._stop.is_set(),
            "uploadedCount": sum(bool(row.get("uploaded")) for row in rows),
            "pendingCount": sum(
                not bool(row.get("uploaded"))
                or not bool(row.get("metadata_registered", True))
                for row in rows
            ),
            "latestDiagnosticId": str(latest.get("diagnostic_id") or ""),
            "latestCloudObjectId": str(latest.get("cloud_object_id") or ""),
            "latestError": str(latest.get("last_error") or ""),
        }

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        if self._stop.wait(self.initial_delay_seconds):
            return
        while not self._stop.is_set():
            try:
                self.upload_pending_once()
            except Exception as exc:
                self._notify("scan-error", str(exc))
            if self._stop.wait(self.scan_interval_seconds):
                return


_UPLOADER_LOCK = threading.Lock()
_UPLOADER: CrashDiagnosticUploader | None = None


def start_crash_diagnostic_uploader(
    connection_provider: Callable[[], Any],
    *,
    status_callback: Callable[[str, str], None] | None = None,
) -> CrashDiagnosticUploader:
    global _UPLOADER
    with _UPLOADER_LOCK:
        if _UPLOADER is None:
            _UPLOADER = CrashDiagnosticUploader(
                connection_provider,
                status_callback=status_callback,
            )
            _UPLOADER.start()
        return _UPLOADER


def stop_crash_diagnostic_uploader() -> None:
    global _UPLOADER
    with _UPLOADER_LOCK:
        uploader = _UPLOADER
        _UPLOADER = None
    if uploader is not None:
        uploader.stop()


def crash_diagnostic_upload_status() -> dict[str, Any]:
    with _UPLOADER_LOCK:
        uploader = _UPLOADER
    if uploader is None:
        return {
            "schema": STATE_SCHEMA,
            "running": False,
            "uploadedCount": 0,
            "pendingCount": 0,
            "latestDiagnosticId": "",
            "latestCloudObjectId": "",
            "latestError": "",
        }
    return uploader.status()
