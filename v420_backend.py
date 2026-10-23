from __future__ import annotations

import argparse
import copy
import ctypes
import dataclasses
import faulthandler
import hashlib
import http.client
import json
import multiprocessing
import os
import platform
import sys
import threading
import time
import traceback
from ctypes import wintypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse


_BACKEND_DIAGNOSTIC_HANDLE: Any | None = None
_BACKEND_DIAGNOSTIC_PATH: Path | None = None
_BACKEND_STATE_STOP = threading.Event()
_BACKEND_STATE_THREAD: threading.Thread | None = None
_BACKEND_STARTED_MONOTONIC = time.monotonic()
_WINDOWS_EXCEPTION_HANDLER: Any | None = None


def _crash_diagnostics_directory() -> Path:
    """Return a stable, user-shareable crash diagnostics directory."""

    path = (
        Path.home()
        / "Documents"
        / "DragonNestDpsCapture"
        / "crash_diagnostics"
    )
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_backend_diagnostic(
    context: str,
    error: BaseException | None = None,
) -> None:
    handle = _BACKEND_DIAGNOSTIC_HANDLE
    if handle is None:
        return
    try:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        handle.write(f"\n[{stamp}] {context}\n")
        if error is not None:
            traceback.print_exception(
                type(error), error, error.__traceback__, file=handle
            )
        handle.flush()
    except Exception:
        # Diagnostics must never turn a recoverable application error into a
        # second failure.
        pass


def _install_windows_backend_minidump() -> None:
    """Write a minimal native dump when the frozen backend terminates fatally."""

    global _WINDOWS_EXCEPTION_HANDLER
    if os.name != "nt":
        return
    try:
        dbghelp = ctypes.WinDLL("DbgHelp.dll")
        kernel32 = ctypes.WinDLL("kernel32.dll")
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        dbghelp.MiniDumpWriteDump.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.HANDLE, wintypes.DWORD,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ]
        dbghelp.MiniDumpWriteDump.restype = wintypes.BOOL

        class MiniDumpExceptionInformation(ctypes.Structure):
            _fields_ = [
                ("ThreadId", wintypes.DWORD),
                ("ExceptionPointers", ctypes.c_void_p),
                ("ClientPointers", wintypes.BOOL),
            ]

        callback_type = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)

        def exception_handler(exception_pointers: int) -> int:
            invalid_handle = ctypes.c_void_p(-1).value
            handle = invalid_handle
            try:
                stamp = int(time.time() * 1000)
                desktop_pid = os.getenv("DRAGON_NEST_DESKTOP_PID", "") or "unknown"
                path = _crash_diagnostics_directory() / (
                    f"backend-native-crash_{desktop_pid}_{stamp}_{os.getpid()}.dmp"
                )
                handle = kernel32.CreateFileW(
                    str(path), 0x40000000, 0, None, 2, 0x80, None
                )
                if handle == invalid_handle:
                    return 0
                info = MiniDumpExceptionInformation(
                    kernel32.GetCurrentThreadId(),
                    ctypes.c_void_p(exception_pointers),
                    False,
                )
                dbghelp.MiniDumpWriteDump(
                    kernel32.GetCurrentProcess(),
                    os.getpid(),
                    handle,
                    0x1000,  # MiniDumpWithThreadInfo | MiniDumpNormal
                    ctypes.byref(info) if exception_pointers else None,
                    None,
                    None,
                )
            except Exception:
                pass
            finally:
                try:
                    if handle not in {None, invalid_handle}:
                        kernel32.CloseHandle(handle)
                except Exception:
                    pass
            return 0

        _WINDOWS_EXCEPTION_HANDLER = callback_type(exception_handler)
        kernel32.SetUnhandledExceptionFilter(_WINDOWS_EXCEPTION_HANDLER)
    except Exception:
        _WINDOWS_EXCEPTION_HANDLER = None


def _initialize_crash_diagnostics() -> None:
    """Capture Python, worker-thread and native-extension failures."""

    global _BACKEND_DIAGNOSTIC_HANDLE, _BACKEND_DIAGNOSTIC_PATH
    try:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        path = _crash_diagnostics_directory() / (
            f"backend_{stamp}_{os.getpid()}.log"
        )
        handle = path.open("a", encoding="utf-8", buffering=1)
    except OSError:
        return
    _BACKEND_DIAGNOSTIC_HANDLE = handle
    _BACKEND_DIAGNOSTIC_PATH = path
    handle.write(
        "Dragon Nest Battle Platform backend diagnostics\n"
        f"started={time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"pid={os.getpid()}\n"
        f"parent_pid={os.getppid()}\n"
        f"desktop_pid={os.getenv('DRAGON_NEST_DESKTOP_PID', '')}\n"
        f"process_name={multiprocessing.current_process().name}\n"
        f"python={sys.version.replace(chr(10), ' ')}\n"
        f"platform={platform.platform()}\n"
        f"executable={sys.executable}\n"
    )
    handle.flush()
    try:
        faulthandler.enable(file=handle, all_threads=True)
    except (OSError, RuntimeError):
        pass
    _install_windows_backend_minidump()

    previous_sys_hook = sys.excepthook

    def sys_hook(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_traceback: Any,
    ) -> None:
        _write_backend_diagnostic("Uncaught main-thread exception")
        try:
            traceback.print_exception(
                exc_type, exc_value, exc_traceback, file=handle
            )
            handle.flush()
        finally:
            previous_sys_hook(exc_type, exc_value, exc_traceback)

    sys.excepthook = sys_hook

    previous_thread_hook = threading.excepthook

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        _write_backend_diagnostic(
            f"Uncaught worker-thread exception: {args.thread.name if args.thread else 'unknown'}"
        )
        try:
            traceback.print_exception(
                args.exc_type, args.exc_value, args.exc_traceback, file=handle
            )
            handle.flush()
        finally:
            previous_thread_hook(args)

    threading.excepthook = thread_hook

    previous_unraisable_hook = sys.unraisablehook

    def unraisable_hook(args: sys.UnraisableHookArgs) -> None:
        _write_backend_diagnostic(
            f"Unraisable exception: {getattr(args, 'err_msg', '') or 'unknown'}",
            args.exc_value,
        )
        previous_unraisable_hook(args)

    sys.unraisablehook = unraisable_hook


if multiprocessing.current_process().name == "MainProcess":
    _initialize_crash_diagnostics()


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

DEFAULT_APP_VERSION = "4.88"


def _runtime_app_version() -> str:
    """Use the desktop shell's package version as the update authority.

    Tauri injects ``DRAGON_NEST_APP_VERSION`` whenever it starts either the
    development backend or the frozen engine. Keeping a second hard-coded
    release number here previously made a newer desktop report itself as an
    older release, so the updater offered the already-installed version forever.
    The fallback only serves direct or standalone backend launches.
    """

    injected = str(os.environ.get("DRAGON_NEST_APP_VERSION") or "").strip()
    return injected or DEFAULT_APP_VERSION


VERSION = _runtime_app_version()
os.environ["DRAGON_NEST_APP_VERSION"] = VERSION

# The desktop and Python engine can be rebuilt at different times while the
# public app version remains unchanged.  Do not treat an older engine as
# compatible merely because both report the same public version; otherwise a
# newly built frontend can silently reuse a backend which lacks newer routes.
# Bump this value whenever the local HTTP contract changes.
BACKEND_PRODUCT = "dragon-nest-battle-platform"
BACKEND_API_REVISION = "20260923-database-character-percentile"
BACKEND_API_CAPABILITIES = frozenset(
    {
        "anonymous-usage-dashboard",
        "anonymous-usage-events-v2",
        "character-score-history",
        "crash-diagnostic-cloud-upload-v1",
        "database-profession-ranking-v1",
        "database-ranking-center-v2",
        "database-ranking-character-query-v1",
        "movement-ledger-v1",
        "profession-score-history",
        "profession-score-history-detail",
        "profile-score-overlay-privacy-v1",
        "profile-score-privacy-scopes-v2",
        "score-history-cursor-v2",
    }
)

from dragon_nest_builder.calculator import BuildCalculator
from dragon_nest_builder.crash_diagnostic_upload import (
    crash_diagnostic_upload_status,
    start_crash_diagnostic_uploader,
    stop_crash_diagnostic_uploader,
)
from dragon_nest_builder.data_loader import DataStore, as_number
from dragon_nest_builder.live_dps.movement_map_assets import (
    movement_map_asset_path,
)
from dragon_nest_builder.live_dps import npcap_config
from dragon_nest_builder.live_dps.v410_score_sync import load_v410_score_sync_config
from dragon_nest_builder.v410_native_core import NativeSimulatorCore


HOST = "127.0.0.1"
DEFAULT_PORT = 48710
BACKEND_PROBE_TIMEOUT_SECONDS = 0.35
BACKEND_TAKEOVER_POLL_SECONDS = 0.5


def _backend_state_path() -> Path:
    desktop_pid = os.getenv("DRAGON_NEST_DESKTOP_PID", "") or "standalone"
    return _crash_diagnostics_directory() / (
        f"backend-state-{desktop_pid}-{os.getpid()}.json"
    )


def _compact_capture_diagnostic(engine: "Engine") -> dict[str, Any]:
    try:
        raw = engine.native.capture_status(False)
    except Exception as exc:
        return {"state": "unavailable", "error": type(exc).__name__}
    allowed = (
        "state", "status", "progress", "elapsedSeconds", "scoreReady",
        "capturePackets", "decodedPackets", "droppedPackets", "queueDepth",
        "autoEnabled", "npcapInstalled", "overlayLatency", "stopDiagnostics",
    )
    result = {key: raw.get(key) for key in allowed if key in raw}
    source_file = str(raw.get("sourceFile") or raw.get("file") or "").strip()
    if source_file:
        result["sourceFileName"] = Path(source_file).name
    return result


def _write_backend_state(engine: "Engine", *, phase: str) -> None:
    try:
        payload = {
            "schema": "dragon-nest-backend-state-v1",
            "version": VERSION,
            "phase": phase,
            "pid": os.getpid(),
            "desktopPid": os.getenv("DRAGON_NEST_DESKTOP_PID", ""),
            "recordedEpochMs": int(time.time() * 1000),
            "uptimeSeconds": round(time.monotonic() - _BACKEND_STARTED_MONOTONIC, 3),
            "threads": sorted(
                thread.name for thread in threading.enumerate() if thread.is_alive()
            ),
            "capture": _compact_capture_diagnostic(engine),
        }
        destination = _backend_state_path()
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(destination)
    except Exception:
        pass


def _backend_state_loop(engine: "Engine") -> None:
    while not _BACKEND_STATE_STOP.wait(5.0):
        _write_backend_state(engine, phase="running")


def _start_backend_state_heartbeat(engine: "Engine") -> None:
    global _BACKEND_STATE_THREAD
    _BACKEND_STATE_STOP.clear()
    _write_backend_state(engine, phase="ready")
    _BACKEND_STATE_THREAD = threading.Thread(
        target=_backend_state_loop,
        args=(engine,),
        name="CrashDiagnosticHeartbeat",
        daemon=True,
    )
    _BACKEND_STATE_THREAD.start()


def _stop_backend_state_heartbeat(engine: "Engine" | None) -> None:
    _BACKEND_STATE_STOP.set()
    thread = _BACKEND_STATE_THREAD
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=0.75)
    if engine is not None:
        _write_backend_state(engine, phase="stopped-normally")


class ExclusiveThreadingHTTPServer(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR can allow an old and a new development backend
    # to share the port. Requests then land on different code versions.
    allow_reuse_address = False


def _probe_backend(host: str, port: int) -> dict[str, Any] | None:
    """Return the health payload exposed by a local backend, if available."""

    connection = http.client.HTTPConnection(
        host,
        port,
        timeout=BACKEND_PROBE_TIMEOUT_SECONDS,
    )
    try:
        connection.request("GET", "/api/health")
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        if response.status != HTTPStatus.OK or not isinstance(payload, dict):
            return None
        return payload
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    finally:
        connection.close()


def _compatible_backend_is_running(host: str, port: int) -> bool:
    """Return whether the occupied endpoint has the exact local API contract."""

    payload = _probe_backend(host, port)
    return bool(
        payload
        and payload.get("ok") is True
        and str(payload.get("product") or "") == BACKEND_PRODUCT
        and str(payload.get("version") or "") == VERSION
        and str(payload.get("apiRevision") or "") == BACKEND_API_REVISION
    )


def _replaceable_stale_backend(payload: dict[str, Any] | None) -> bool:
    """Recognize an older build of this backend without trusting unrelated apps."""

    if not payload or payload.get("ok") is not True:
        return False
    product = str(payload.get("product") or "")
    if product:
        return product == BACKEND_PRODUCT
    # V4.24-V4.434 health payloads predate the product/API revision fields but
    # already exposed this unique marker.  Restrict the fallback to the same
    # public version so an unrelated or separately installed release is not
    # stopped by accident.
    return (
        payload.get("windowFree") is True
        and str(payload.get("version") or "") == VERSION
    )


def _request_stale_backend_shutdown(host: str, port: int) -> bool:
    """Ask a recognized stale backend to release the shared local API port."""

    connection = http.client.HTTPConnection(
        host,
        port,
        timeout=max(0.5, BACKEND_PROBE_TIMEOUT_SECONDS),
    )
    try:
        connection.request("POST", "/api/shutdown", body=b"")
        response = connection.getresponse()
        response.read()
        return response.status == HTTPStatus.OK
    except OSError:
        # The process may close the listener before the response is read.  The
        # bind retry below is the final authority, so this is still progress.
        return True
    finally:
        connection.close()


def _address_is_in_use(error: OSError) -> bool:
    return error.errno in {48, 98, 10048} or getattr(error, "winerror", None) == 10048


def _bind_server_or_wait_for_takeover(
    host: str,
    port: int,
) -> ExclusiveThreadingHTTPServer:
    reused_existing = False
    stale_shutdown_requested = False
    while True:
        try:
            server = ExclusiveThreadingHTTPServer((host, port), ApiHandler)
            if reused_existing:
                print(
                    f"The previous V{VERSION} backend stopped; this process now owns "
                    f"http://{host}:{port}",
                    flush=True,
                )
            return server
        except OSError as error:
            if not _address_is_in_use(error):
                raise
            payload = _probe_backend(host, port)
            compatible = bool(
                payload
                and payload.get("ok") is True
                and str(payload.get("product") or "") == BACKEND_PRODUCT
                and str(payload.get("version") or "") == VERSION
                and str(payload.get("apiRevision") or "")
                == BACKEND_API_REVISION
            )
            if not compatible and _replaceable_stale_backend(payload):
                if not stale_shutdown_requested:
                    if not _request_stale_backend_shutdown(host, port):
                        raise RuntimeError(
                            f"V{VERSION} stale local API could not be stopped."
                        ) from error
                    print(
                        f"A stale V{VERSION} backend is using port {port}; "
                        "requesting a graceful API-contract takeover.",
                        flush=True,
                    )
                    stale_shutdown_requested = True
                time.sleep(BACKEND_TAKEOVER_POLL_SECONDS)
                continue
            if not compatible:
                raise RuntimeError(
                    f"V{VERSION} local API port {port} is occupied by another program."
                ) from error
            if not reused_existing:
                print(
                    f"A compatible V{VERSION} backend is already listening on "
                    f"http://{host}:{port}; reusing it and waiting for takeover.",
                    flush=True,
                )
                reused_existing = True
            time.sleep(BACKEND_TAKEOVER_POLL_SECONDS)


def _job_key(kind: str, payload: dict[str, Any]) -> str:
    frozen = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{kind}:{hashlib.sha256(frozen.encode('utf-8')).hexdigest()}"


MODULES = [
    {"id": "build1", "label": "配装1", "group": "build", "status": "native"},
    {"id": "build2", "label": "配装2", "group": "build", "status": "native"},
    {"id": "compare", "label": "配装对比", "group": "build", "status": "native"},
    {"id": "skill-build", "label": "加点模拟", "group": "skills", "status": "native"},
    {"id": "skill-sim", "label": "技能模拟", "group": "skills", "status": "native"},
    {"id": "team", "label": "配队模拟", "group": "team", "status": "native"},
    {"id": "live", "label": "实战统计", "group": "live", "status": "native"},
]


class Engine:
    """Window-free application service used by the current frontend."""

    def __init__(self) -> None:
        started = time.perf_counter()
        self.store = DataStore.load()
        self.calculator = BuildCalculator(self.store)
        self.native = NativeSimulatorCore(self.store, self.calculator)
        # The parallel score pool is created lazily by the first calculation.
        # Starting all workers here made an idle application occupy up to 16
        # extra processes before the user opened team scoring.
        self.loaded_ms = round((time.perf_counter() - started) * 1000, 1)
        self._lock = threading.RLock()

    def bootstrap(self) -> dict[str, Any]:
        first_jobs = self.store.first_jobs()
        second_jobs = {job: self.store.second_jobs(job) for job in first_jobs}
        first_by_base: dict[str, list[str]] = {}
        for first_job in first_jobs:
            children = second_jobs.get(first_job, [])
            base_job = self.store.job_family(children[0], first_job) if children else ""
            # Equipment data abbreviates the base Archer class as ``弓箭``;
            # the history UI should use the in-game profession name.
            if base_job == "弓箭":
                base_job = "弓箭手"
            if base_job:
                first_by_base.setdefault(base_job, []).append(first_job)
        return {
            "version": VERSION,
            "title": f"龙之谷ACT（高级战斗追踪器）工具及模拟工具 V{VERSION}",
            "engine": {
                "status": "ready",
                "loadedMs": self.loaded_ms,
                "source": f"Python V{VERSION} window-free core",
            },
            "modules": MODULES,
            "jobs": {
                "base": list(first_by_base),
                "first": first_jobs,
                "second": second_jobs,
                "firstByBase": first_by_base,
            },
        }

    def shutdown(self) -> None:
        self.native.shutdown()

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

    def run_score(progress):
        source_hash = str(payload.get("sourceHash") or "").strip()
        if source_hash.startswith("live:"):
            # Live scoring shares the machine with the game client. Two
            # below-normal workers preserve capture/UI responsiveness and
            # avoid the 8-16 process spikes used by offline replay scoring.
            from dragon_nest_builder.timeline_parallel import timeline_worker_limit

            with timeline_worker_limit(2):
                return core.score_replay(payload, progress)
        return core.score_replay(payload, progress)

    return core.jobs.start(
        label,
        run_score,
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
    server_version = "DragonNestV420/2.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[v{VERSION}-api] {self.address_string()} {fmt % args}")

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

    def _write_json_bytes(
        self, data: bytes, status: HTTPStatus = HTTPStatus.OK
    ) -> None:
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

    def _handle_error(self, error: Exception) -> None:
        try:
            self._write_json(
                {"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR
            )
        except Exception:  # noqa: BLE001
            pass

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        """Only the countdown overlays talk to this engine.

        The surface is deliberately tiny: health, capture state, the two
        overlay snapshots, voice events and the Npcap path setting.  Anything
        else the old combat tracker exposed (builds, teams, scoring, ranking,
        history, telemetry) has been removed from the product.
        """
        try:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            core = get_engine().native
            if parsed.path == "/api/health":
                self._write_json(
                    {
                        "ok": True,
                        "product": BACKEND_PRODUCT,
                        "version": VERSION,
                        "apiRevision": BACKEND_API_REVISION,
                        "apiCapabilities": sorted(BACKEND_API_CAPABILITIES),
                        "windowFree": True,
                    }
                )
                return
            if parsed.path == "/api/capture/status":
                include_snapshot = query.get("includeSnapshot", ["1"])[0] != "0"
                self._write_json(core.capture_status(include_snapshot))
                return
            if parsed.path == "/api/capture/voice-events":
                self._write_json(core.capture_voice_events())
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
                self._write_json_bytes(
                    core.buff_overlay_status_json(include_catalog)
                )
                return
            if parsed.path == "/api/capture/toggles":
                self._write_json(core.capture_toggles())
                return
            if parsed.path == "/api/npcap/config":
                self._write_json(npcap_config.status_payload())
                return
            self._write_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
        except Exception as error:  # noqa: BLE001
            self._handle_error(error)

    def do_POST(self) -> None:  # noqa: N802
        """Capture control and the Npcap path setting only."""
        try:
            parsed_path = urlparse(self.path)
            if parsed_path.path == "/api/shutdown":
                query = parse_qs(parsed_path.query)
                requested_pid = int(query.get("pid", [str(os.getpid())])[0])
                if requested_pid != os.getpid():
                    self._write_json(
                        {"ok": False, "error": "backend_pid_mismatch"},
                        HTTPStatus.CONFLICT,
                    )
                    return
                self._write_json({"ok": True})
                # BaseServer.shutdown() must run outside the request thread.
                threading.Thread(
                    target=self.server.shutdown,
                    name="v424-api-shutdown",
                    daemon=True,
                ).start()
                return
            payload = self._read_json()
            core = get_engine().native
            if self.path == "/api/npcap/config":
                raw = str(payload.get("path", "") or "")
                npcap_config.write_config_path(raw)
                status = npcap_config.status_payload()
                # Echo what was actually stored so the UI never shows a value
                # that only lived in the request body.
                status["saved"] = True
                self._write_json(status)
                return
            if self.path == "/api/npcap/detect":
                candidates = npcap_config.detect_candidates()
                resolved, source = npcap_config.resolve_dll()
                self._write_json(
                    {
                        "candidates": candidates,
                        "resolvedPath": str(resolved) if resolved else "",
                        "installed": resolved is not None,
                        "message": "" if resolved else "未找到 Npcap。",
                        "source": source,
                    }
                )
                return
            if self.path == "/api/npcap/test":
                self._write_json(npcap_config.test_connection())
                return
            if self.path == "/api/capture/start":
                self._write_json(core.start_capture(as_number(payload.get("duration"))))
                return
            if self.path == "/api/capture/stop":
                self._write_json(core.stop_capture())
                return
            if self.path == "/api/capture/auto":
                self._write_json(core.set_capture_auto(bool(payload.get("enabled"))))
                return
            if self.path == "/api/capture/toggles":
                self._write_json(core.set_capture_toggles(payload))
                return
            self._write_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
        except Exception as error:  # noqa: BLE001
            self._handle_error(error)

def serve(host: str = HOST, port: int = DEFAULT_PORT) -> None:
    # Native extensions imported during startup may replace the process-wide
    # exception filter. Install ours once more after all backend modules loaded.
    _install_windows_backend_minidump()
    server = _bind_server_or_wait_for_takeover(host, port)
    engine = get_engine()
    _start_backend_state_heartbeat(engine)
    start_crash_diagnostic_uploader(
        load_v410_score_sync_config,
        status_callback=lambda event, message: _write_backend_diagnostic(
            f"Crash diagnostic cloud upload [{event}]: {message}"
        ),
    )
    pid_path = Path(__file__).resolve().parent / ".v420_backend_pid"
    pid_path.write_text(str(os.getpid()), encoding="ascii")
    _write_backend_diagnostic(
        f"Backend ready: version={VERSION}, endpoint=http://{host}:{port}"
    )
    print(f"Dragon Nest V{VERSION} API listening on http://{host}:{port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        stop_crash_diagnostic_uploader()
        global ENGINE
        engine = ENGINE
        ENGINE = None
        _stop_backend_state_heartbeat(engine)
        if engine is not None:
            try:
                engine.shutdown()
            except Exception as exc:
                print(f"V{VERSION} backend shutdown warning: {exc}", flush=True)
        try:
            if pid_path.read_text(encoding="ascii").strip() == str(os.getpid()):
                pid_path.unlink(missing_ok=True)
        except OSError:
            pass
        _write_backend_diagnostic("Backend stopped normally")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=f"Dragon Nest simulator V{VERSION} local API")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", default=DEFAULT_PORT, type=int)
    args = parser.parse_args()
    serve(args.host, args.port)
