from __future__ import annotations

import argparse
import copy
import ctypes
import dataclasses
import hashlib
import http.client
import json
import os
import sys
import tempfile
import threading
import time
import uuid
from ctypes import wintypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse


def _stable_runtime_root() -> Path:
    """Resolve the backend root independently of the caller's working dir.

    Explorer shortcuts commonly start a process with the Desktop (or
    ``System32``) as its current directory.  The frozen backend must instead
    use the directory containing its bundled Python files.
    """
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass))
    try:
        candidates.append(Path(__file__).resolve().parent)
    except OSError:
        pass
    for candidate in candidates:
        if (candidate / "dragon_nest_builder").is_dir():
            return candidate
        internal = candidate / "_internal"
        if (internal / "dragon_nest_builder").is_dir():
            return internal
    return candidates[0] if candidates else Path.cwd()


# Normalize the backend process before importing modules whose resource paths
# are initialized at import time. This makes shortcut launches equivalent to
# launches from the installation directory.
_RUNTIME_ROOT = _stable_runtime_root()
try:
    os.chdir(_RUNTIME_ROOT)
except OSError:
    pass

from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.data_loader import DataStore, as_number
from dragon_nest_builder.timeline_parallel import warm_timeline_process_pool
from dragon_nest_builder.v410_native_core import NativeSimulatorCore


VERSION = "4.10"
HOST = "127.0.0.1"
DEFAULT_PORT = 48710
BACKEND_PROBE_TIMEOUT_SECONDS = 0.35
BACKEND_TAKEOVER_POLL_SECONDS = 0.5


class ExclusiveThreadingHTTPServer(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR can allow an old and a new development backend
    # to share the port. Requests then land on different code versions.
    allow_reuse_address = False


def _compatible_backend_is_running(host: str, port: int) -> bool:
    """Return whether the occupied endpoint is this V4.10 local backend."""

    connection = http.client.HTTPConnection(
        host,
        port,
        timeout=BACKEND_PROBE_TIMEOUT_SECONDS,
    )
    try:
        connection.request("GET", "/api/health")
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        return (
            response.status == HTTPStatus.OK
            and payload.get("ok") is True
            and str(payload.get("version") or "") == VERSION
        )
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    finally:
        connection.close()


def _address_is_in_use(error: OSError) -> bool:
    return error.errno in {48, 98, 10048} or getattr(error, "winerror", None) == 10048


def _bind_server_or_wait_for_takeover(
    host: str,
    port: int,
) -> ExclusiveThreadingHTTPServer:
    reused_existing = False
    while True:
        try:
            server = ExclusiveThreadingHTTPServer((host, port), ApiHandler)
            if reused_existing:
                print(
                    f"The previous V4.10 backend stopped; this process now owns "
                    f"http://{host}:{port}",
                    flush=True,
                )
            return server
        except OSError as error:
            if not _address_is_in_use(error):
                raise
            if not _compatible_backend_is_running(host, port):
                raise RuntimeError(
                    f"V4.10 local API port {port} is occupied by another program."
                ) from error
            if not reused_existing:
                print(
                    f"A compatible V4.10 backend is already listening on "
                    f"http://{host}:{port}; reusing it and waiting for takeover.",
                    flush=True,
                )
                reused_existing = True
            time.sleep(BACKEND_TAKEOVER_POLL_SECONDS)


def _job_key(kind: str, payload: dict[str, Any]) -> str:
    frozen = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{kind}:{hashlib.sha256(frozen.encode('utf-8')).hexdigest()}"


def _capture_history_directory() -> Path:
    path = Path.home() / "Documents" / "DragonNestDpsCapture" / "live_dps_diagnostics"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _pick_capture_file() -> str:
    """Open the same native history picker for browser and Tauri frontends."""

    if os.name != "nt":
        return ""

    class OpenFileNameW(ctypes.Structure):
        _fields_ = [
            ("lStructSize", wintypes.DWORD),
            ("hwndOwner", wintypes.HWND),
            ("hInstance", wintypes.HINSTANCE),
            ("lpstrFilter", wintypes.LPCWSTR),
            ("lpstrCustomFilter", wintypes.LPWSTR),
            ("nMaxCustFilter", wintypes.DWORD),
            ("nFilterIndex", wintypes.DWORD),
            ("lpstrFile", wintypes.LPWSTR),
            ("nMaxFile", wintypes.DWORD),
            ("lpstrFileTitle", wintypes.LPWSTR),
            ("nMaxFileTitle", wintypes.DWORD),
            ("lpstrInitialDir", wintypes.LPCWSTR),
            ("lpstrTitle", wintypes.LPCWSTR),
            ("Flags", wintypes.DWORD),
            ("nFileOffset", wintypes.WORD),
            ("nFileExtension", wintypes.WORD),
            ("lpstrDefExt", wintypes.LPCWSTR),
            ("lCustData", wintypes.LPARAM),
            ("lpfnHook", ctypes.c_void_p),
            ("lpTemplateName", wintypes.LPCWSTR),
            ("pvReserved", ctypes.c_void_p),
            ("dwReserved", wintypes.DWORD),
            ("FlagsEx", wintypes.DWORD),
        ]

    filename = ctypes.create_unicode_buffer(32768)
    filter_text = (
        "实战统计记录 (*.dpscap)\0*.dpscap\0"
        "Packet Capture (*.pcapng)\0*.pcapng\0"
        "JSON Lines (*.jsonl)\0*.jsonl\0"
        "所有文件 (*.*)\0*.*\0\0"
    )
    dialog = OpenFileNameW()
    dialog.lStructSize = ctypes.sizeof(dialog)
    dialog.lpstrFilter = filter_text
    dialog.nFilterIndex = 1
    dialog.lpstrFile = ctypes.cast(filename, wintypes.LPWSTR)
    dialog.nMaxFile = len(filename)
    dialog.lpstrInitialDir = str(_capture_history_directory())
    dialog.lpstrTitle = "载入实战统计记录"
    # Explorer UI, existing file/path only, and do not mutate backend cwd.
    dialog.Flags = 0x00080000 | 0x00001000 | 0x00000800 | 0x00000008
    get_open_file = ctypes.windll.comdlg32.GetOpenFileNameW
    get_open_file.argtypes = [ctypes.POINTER(OpenFileNameW)]
    get_open_file.restype = wintypes.BOOL
    return filename.value if get_open_file(ctypes.byref(dialog)) else ""

MODULES = [
    {"id": "build1", "label": "配装1", "group": "build", "status": "native"},
    {"id": "build2", "label": "配装2", "group": "build", "status": "native"},
    {"id": "compare", "label": "配装对比", "group": "build", "status": "native"},
    {"id": "skill-build", "label": "模拟加点", "group": "skills", "status": "native"},
    {"id": "skill-sim", "label": "技能模拟", "group": "skills", "status": "native"},
    {"id": "team", "label": "配队模拟", "group": "team", "status": "native"},
    {"id": "live", "label": "实战统计", "group": "live", "status": "native"},
]


class Engine:
    """Window-free application service used by the V4.10 frontend."""

    def __init__(self) -> None:
        started = time.perf_counter()
        self.store = DataStore.load()
        self.calculator = BuildCalculator(self.store)
        self.native = NativeSimulatorCore(self.store, self.calculator)
        # Windows process workers are expensive to spawn and initialize. Start
        # them while the user is choosing/loading a replay so the first team
        # score does not pay that cold-start cost on the critical path.
        warm_timeline_process_pool(16)
        self.loaded_ms = round((time.perf_counter() - started) * 1000, 1)
        self._lock = threading.RLock()

    def bootstrap(self) -> dict[str, Any]:
        first_jobs = self.store.first_jobs()
        return {
            "version": VERSION,
            "title": f"龙之谷ACT（高级战斗追踪器）工具及模拟工具 V{VERSION}",
            "engine": {
                "status": "ready",
                "loadedMs": self.loaded_ms,
                "source": "Python V4.10 window-free core",
            },
            "modules": MODULES,
            "jobs": {
                "first": first_jobs,
                "second": {job: self.store.second_jobs(job) for job in first_jobs},
            },
        }

    def calculate(self, build: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        with self._lock:
            rows, summary = self.calculator.calculate(copy.deepcopy(build))
        return {
            "rows": [dataclasses.asdict(row) for row in rows],
            "summary": summary,
            "elapsedMs": round((time.perf_counter() - started) * 1000, 2),
        }


ENGINE: Engine | None = None


def get_engine() -> Engine:
    global ENGINE
    if ENGINE is None:
        ENGINE = Engine()
    return ENGINE


def _score_job_dedupe_key(
    core: NativeSimulatorCore,
    payload: dict[str, Any],
) -> str:
    canonical = getattr(core, "score_job_dedupe_key", None)
    if callable(canonical):
        return str(canonical(payload))
    return _job_key(
        "replay-score",
        {
            "request": payload,
            # Team-score theory uses the current build as its equipment/card
            # template. Include it so a background prefetch cannot be reused
            # after the user changes build1.
            "build": core.get_build("build1"),
        },
    )


def _start_score_job(
    core: NativeSimulatorCore,
    payload: dict[str, Any],
    *,
    label: str = "正在生成全队评分",
) -> str:
    canonical = getattr(core, "start_score_job", None)
    if callable(canonical):
        return str(canonical(payload, label=label))
    requested_source_hash = str(payload.get("sourceHash") or "").strip()
    resolver = getattr(core, "resolve_score_source_hash", None)
    resolved_source_hash = (
        resolver(requested_source_hash) if callable(resolver)
        else requested_source_hash
    )
    if resolved_source_hash != requested_source_hash:
        payload = {**payload, "sourceHash": resolved_source_hash}
    return core.jobs.start(
        label,
        lambda progress: core.score_replay(payload, progress),
        kind="replay-score",
        dedupe_key=_score_job_dedupe_key(core, payload),
    )


def _start_default_score_prefetch(
    core: NativeSimulatorCore,
    replay: dict[str, Any],
) -> None:
    if str(replay.get("scorePrefetchJobId") or "").strip():
        return
    source_hash = str(replay.get("sourceHash") or "").strip()
    players = replay.get("players")
    if not source_hash or not isinstance(players, list) or not players:
        return
    payload = {
        "sourceHash": source_hash,
        "bossTargetIds": [],
        "glyphOverrides": {},
    }
    replay["scorePrefetchJobId"] = _start_score_job(
        core,
        payload,
        label="正在后台预计算全队评分",
    )


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "DragonNestV410/2.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[v4.10-api] {self.address_string()} {fmt % args}")

    def _cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Capture-Name")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _write_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _write_file(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            self._write_json({"error": "asset_not_found"}, HTTPStatus.NOT_FOUND)
            return
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self._cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "public, max-age=86400, immutable")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            # Windows PowerShell 5 sends string request bodies with the
            # active ANSI code page unless a byte array is supplied.
            text = raw.decode("gb18030")
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {}

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        try:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            engine = get_engine()
            core = engine.native
            if parsed.path == "/api/health":
                self._write_json({"ok": True, "version": VERSION, "windowFree": True})
                return
            if parsed.path == "/api/bootstrap":
                self._write_json(engine.bootstrap())
                return
            if parsed.path in {"/api/build", "/api/build/default"}:
                slot = query.get("slot", ["build1"])[0]
                self._write_json(core.get_build(slot))
                return
            if parsed.path == "/api/build/catalog":
                first_job = query.get("firstJob", [""])[0]
                second_job = query.get("secondJob", [""])[0]
                self._write_json(core.build_catalog(first_job, second_job))
                return
            if parsed.path == "/api/skill-build":
                job = query.get("job", [""])[0]
                self._write_json(core.skill_build(job))
                return
            if parsed.path == "/api/skill-build/schemes":
                job = query.get("job", [""])[0]
                self._write_json(core.skill_build_schemes(job))
                return
            if parsed.path == "/api/assets/skill-icon-sheet":
                page = max(1, min(99, int(query.get("page", ["1"])[0])))
                path = (
                    _RUNTIME_ROOT
                    / "dragon_nest_builder"
                    / "assets"
                    / "skill_icons"
                    / f"skillicon{page:02d}.png"
                )
                self._write_file(path, "image/png")
                return
            if parsed.path == "/api/skill-rows":
                slot = query.get("slot", ["build1"])[0]
                include_unlearned = query.get("includeUnlearned", ["0"])[0] == "1"
                self._write_json(core.skill_rows(core.get_build(slot), include_unlearned))
                return
            if parsed.path == "/api/job":
                job_id = query.get("id", [""])[0]
                self._write_json(core.jobs.status(job_id))
                return
            if parsed.path == "/api/jobs":
                self._write_json({"jobs": core.jobs.list()})
                return
            if parsed.path == "/api/replay/score/detail":
                score_key = query.get("scoreKey", [""])[0]
                source_id = query.get("sourceId", [""])[0]
                source_hash = query.get("sourceHash", [""])[0]
                self._write_json(core.score_detail(score_key, source_id, source_hash))
                return
            if parsed.path == "/api/replay/pick":
                self._write_json({"path": _pick_capture_file()})
                return
            if parsed.path == "/api/capture/status":
                include_snapshot = query.get("includeSnapshot", ["1"])[0] != "0"
                self._write_json(core.capture_status(include_snapshot))
                return
            if parsed.path == "/api/capture/timeline":
                try:
                    after_sequence = int(query.get("afterSequence", ["0"])[0] or 0)
                except (TypeError, ValueError):
                    after_sequence = 0
                self._write_json(core.capture_timeline(after_sequence))
                return
            if parsed.path == "/api/capture/buff-overlay/wait":
                try:
                    after_revision = int(
                        query.get("afterRevision", ["0"])[0] or 0
                    )
                except (TypeError, ValueError):
                    after_revision = 0
                try:
                    timeout_ms = int(query.get("timeoutMs", ["15000"])[0] or 15000)
                except (TypeError, ValueError):
                    timeout_ms = 15000
                self._write_json(
                    core.wait_buff_overlay_change(after_revision, timeout_ms)
                )
                return
            if parsed.path == "/api/capture/buff-overlay":
                include_catalog = query.get("includeCatalog", ["1"])[0] in {
                    "1", "true", "yes"
                }
                payload = core.buff_overlay_status()
                if not include_catalog:
                    payload.pop("buffOptions", None)
                    payload.pop("summonOptions", None)
                self._write_json(payload)
                return
            if parsed.path == "/api/capture/teammate-info":
                self._write_json(core.teammate_info_status())
                return
            if parsed.path == "/api/score-sync/settings":
                self._write_json(core.score_sync_settings())
                return
            if parsed.path == "/api/team/mechanism-templates":
                self._write_json(core.mechanism_templates())
                return
            self._write_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._write_json(
                {"error": type(exc).__name__, "message": str(exc)},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def do_POST(self) -> None:  # noqa: N802
        try:
            if self.path == "/api/replay/upload":
                length = int(self.headers.get("Content-Length", "0") or 0)
                if length <= 0:
                    self._write_json({"error": "empty_capture"}, HTTPStatus.BAD_REQUEST)
                    return
                encoded_name = self.headers.get("X-Capture-Name", "capture.dpscap")
                filename = Path(unquote(encoded_name)).name
                suffix = Path(filename).suffix.casefold()
                if suffix not in {".dpscap", ".pcapng", ".jsonl"}:
                    self._write_json(
                        {"error": "unsupported_capture", "message": "仅支持 .dpscap、.pcapng 和 .jsonl 记录"},
                        HTTPStatus.BAD_REQUEST,
                    )
                    return
                upload_dir = Path(tempfile.gettempdir()) / "DragonNestSimulatorV410" / "replay_uploads"
                upload_dir.mkdir(parents=True, exist_ok=True)
                upload_path = upload_dir / f"{uuid.uuid4().hex}{suffix}"
                remaining = length
                with upload_path.open("wb") as stream:
                    while remaining > 0:
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise OSError("实战记录上传不完整")
                        stream.write(chunk)
                        remaining -= len(chunk)
                engine = get_engine()
                core = engine.native

                def load_uploaded(progress):
                    try:
                        result = core.load_replay(str(upload_path), progress)
                        result["sourceFile"] = filename
                        _start_default_score_prefetch(core, result)
                        return result
                    except Exception:
                        upload_path.unlink(missing_ok=True)
                        raise

                job_id = core.jobs.start("正在载入实战记录", load_uploaded, kind="replay-load")
                self._write_json({"jobId": job_id})
                return
            payload = self._read_json()
            engine = get_engine()
            core = engine.native
            if self.path == "/api/calculate":
                build = payload.get("build")
                if not isinstance(build, dict):
                    self._write_json({"error": "build_required"}, HTTPStatus.BAD_REQUEST)
                    return
                self._write_json(engine.calculate(build))
                return
            if self.path == "/api/build/save":
                build = payload.get("build")
                if not isinstance(build, dict):
                    self._write_json({"error": "build_required"}, HTTPStatus.BAD_REQUEST)
                    return
                self._write_json(core.set_build(str(payload.get("slot", "build1")), build))
                return
            if self.path == "/api/skill-build/schemes/save":
                job = str(payload.get("job", ""))
                name = str(payload.get("name", ""))
                scheme = payload.get("scheme")
                if not isinstance(scheme, dict):
                    self._write_json({"error": "scheme_required"}, HTTPStatus.BAD_REQUEST)
                    return
                self._write_json(core.save_skill_build_scheme(job, name, scheme))
                return
            if self.path == "/api/skill-build/schemes/delete":
                self._write_json(core.delete_skill_build_scheme(
                    str(payload.get("job", "")),
                    str(payload.get("name", "")),
                ))
                return
            if self.path == "/api/build/reset":
                self._write_json(core.reset_build(str(payload.get("slot", "build1"))))
                return
            if self.path == "/api/build/sync":
                self._write_json(core.sync_builds())
                return
            if self.path == "/api/build/options":
                self._write_json(core.item_options(str(payload.get("kind", "")), payload))
                return
            if self.path == "/api/build/details":
                build = payload.get("build")
                if not isinstance(build, dict):
                    self._write_json({"error": "build_required"}, HTTPStatus.BAD_REQUEST)
                    return
                self._write_json(core.build_details(build))
                return
            if self.path == "/api/build/preset":
                build = payload.get("build")
                if not isinstance(build, dict):
                    self._write_json({"error": "build_required"}, HTTPStatus.BAD_REQUEST)
                    return
                options = payload.get("options")
                self._write_json(core.apply_build_preset(
                    build,
                    str(payload.get("action", "")),
                    options if isinstance(options, dict) else {},
                ))
                return
            if self.path == "/api/build/profile-sync/start":
                self._write_json(core.begin_profile_sync())
                return
            if self.path == "/api/build/profile-sync/preview":
                self._write_json(core.profile_sync_preview())
                return
            if self.path == "/api/build/profile-sync/apply":
                self._write_json(core.apply_profile_sync())
                return
            if self.path == "/api/build/profile-sync/cancel":
                self._write_json(core.cancel_profile_sync())
                return
            if self.path == "/api/skill-rows":
                build = payload.get("build")
                if not isinstance(build, dict):
                    self._write_json({"error": "build_required"}, HTTPStatus.BAD_REQUEST)
                    return
                self._write_json(core.skill_rows(build, bool(payload.get("includeUnlearned"))))
                return
            if self.path == "/api/skill-sim/rows":
                build = payload.get("build")
                rows = payload.get("rows")
                if not isinstance(build, dict) or not isinstance(rows, list):
                    self._write_json({"error": "build_and_rows_required"}, HTTPStatus.BAD_REQUEST)
                    return
                self._write_json(core.skill_sim_rows(build, rows))
                return
            if self.path == "/api/timeline/quick":
                build = payload.get("build")
                if not isinstance(build, dict):
                    self._write_json({"error": "build_required"}, HTTPStatus.BAD_REQUEST)
                    return
                settings = payload.get("settings")
                self._write_json(core.quick_timeline(build, settings if isinstance(settings, dict) else {}))
                return
            if self.path == "/api/skill-glyphs/optimize":
                build = payload.get("build")
                if not isinstance(build, dict):
                    self._write_json({"error": "build_required"}, HTTPStatus.BAD_REQUEST)
                    return
                job_id = core.jobs.start(
                    "正在一键选择技能纹章",
                    lambda progress: core.optimize_skill_glyphs(payload, progress),
                )
                self._write_json({"jobId": job_id})
                return
            if self.path == "/api/team/preview":
                job_id = core.jobs.start(
                    "正在后台计算配队模拟",
                    lambda progress: core.team_preview(payload, progress),
                    kind="team-preview",
                    dedupe_key=_job_key("team-preview", payload),
                )
                self._write_json({"jobId": job_id})
                return
            if self.path == "/api/team/custom-preview":
                job_id = core.jobs.start(
                    "正在生成自选机制八人时间轴",
                    lambda progress: core.custom_team_preview(payload, progress),
                    kind="team-custom-preview",
                    dedupe_key=_job_key("team-custom-preview", payload),
                )
                self._write_json({"jobId": job_id})
                return
            if self.path == "/api/team/setup":
                self._write_json(core.team_setup(payload))
                return
            if self.path == "/api/team/optimize":
                job_id = core.jobs.start(
                    "正在进行一键极限优化配队",
                    lambda progress: core.optimize_team(payload, progress),
                    kind="team-optimize",
                    dedupe_key=_job_key("team-optimize", payload),
                )
                self._write_json({"jobId": job_id})
                return
            if self.path == "/api/replay/load":
                path = str(payload.get("path", "")).strip()
                if not path:
                    self._write_json({"error": "path_required"}, HTTPStatus.BAD_REQUEST)
                    return

                def load_and_prefetch(progress):
                    result = core.load_replay(path, progress)
                    _start_default_score_prefetch(core, result)
                    return result

                job_id = core.jobs.start(
                    "正在载入实战记录",
                    load_and_prefetch,
                    kind="replay-load",
                    dedupe_key=_job_key("replay-load", {"path": path}),
                )
                self._write_json({"jobId": job_id})
                return
            if self.path == "/api/replay/score":
                if not str(payload.get("sourceHash", "")).strip():
                    self._write_json({"error": "source_hash_required"}, HTTPStatus.BAD_REQUEST)
                    return
                job_id = _start_score_job(core, payload)
                self._write_json({"jobId": job_id})
                return
            if self.path == "/api/replay/score-equipment":
                self._write_json(core.score_equipment(payload))
                return
            if self.path == "/api/replay/score-equipment/batch":
                self._write_json(core.score_equipment_batch(payload))
                return
            if self.path == "/api/capture/start":
                self._write_json(core.start_capture(as_number(payload.get("duration"))))
                return
            if self.path == "/api/capture/auto":
                self._write_json(core.set_capture_auto(bool(payload.get("enabled"))))
                return
            if self.path == "/api/capture/stop":
                self._write_json(core.stop_capture())
                return
            if self.path == "/api/capture/clear":
                self._write_json(core.clear_capture())
                return
            if self.path == "/api/score-sync/settings":
                self._write_json(core.save_score_sync_settings(payload))
                return
            if self.path == "/api/score-sync/settings/unlock":
                self._write_json(core.unlock_score_sync_settings(payload.get("password")))
                return
            if self.path == "/api/score-sync/test":
                self._write_json(core.test_score_sync(payload.get("adminPassword")))
                return
            if self.path == "/api/live/history/open":
                self._write_json(core.open_live_history())
                return
            self._write_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            status = HTTPStatus.FORBIDDEN if isinstance(exc, PermissionError) else HTTPStatus.INTERNAL_SERVER_ERROR
            self._write_json(
                {"error": type(exc).__name__, "message": str(exc)},
                status,
            )


def serve(host: str = HOST, port: int = DEFAULT_PORT) -> None:
    server = _bind_server_or_wait_for_takeover(host, port)
    get_engine()
    pid_path = Path(__file__).resolve().parent / ".v410_backend_pid"
    pid_path.write_text(str(os.getpid()), encoding="ascii")
    print(f"Dragon Nest V4.10 API listening on http://{host}:{port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        try:
            if pid_path.read_text(encoding="ascii").strip() == str(os.getpid()):
                pid_path.unlink(missing_ok=True)
        except OSError:
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dragon Nest simulator V4.10 local API")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", default=DEFAULT_PORT, type=int)
    args = parser.parse_args()
    serve(args.host, args.port)
