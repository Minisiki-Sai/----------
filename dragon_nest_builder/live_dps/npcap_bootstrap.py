from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from .npcap import find_npcap_dll


_INSTALLER_ENV = "DN_SIMULATOR_NPCAP_INSTALLER"
_INSTALL_TIMEOUT_SECONDS = 5 * 60
_OFFICIAL_DOWNLOAD_URL = "https://npcap.com/#download"


def _application_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _resource_directory() -> Path | None:
    value = getattr(sys, "_MEIPASS", None)
    return Path(value).resolve() if value else None


def _downloads_directory() -> Path:
    user_profile = Path(os.environ.get("USERPROFILE", Path.home()))
    return user_profile / "Downloads"


def _looks_like_npcap_installer(path: Path) -> bool:
    name = path.name.casefold()
    return path.is_file() and path.suffix.casefold() == ".exe" and name.startswith("npcap")


def _installer_files(directory: Path | None) -> Iterable[Path]:
    if directory is None or not directory.is_dir():
        return ()
    direct_names = (
        "npcap-installer.exe",
        "NpcapInstaller.exe",
    )
    rows = [directory / name for name in direct_names]
    rows.extend(sorted(directory.glob("npcap-*.exe"), reverse=True))
    return rows


def npcap_installer_candidates() -> list[Path]:
    """Return safe, local Npcap installer candidates in preference order."""
    candidates: list[Path] = []
    configured = os.environ.get(_INSTALLER_ENV, "").strip().strip('"')
    if configured:
        candidates.append(Path(configured).expanduser())

    app_dir = _application_directory()
    resource_dir = _resource_directory()
    search_directories = (
        app_dir,
        app_dir / "third_party" / "npcap",
        app_dir / "dragon_nest_builder" / "assets" / "third_party" / "npcap",
        resource_dir,
        resource_dir / "third_party" / "npcap" if resource_dir else None,
        (
            resource_dir
            / "dragon_nest_builder"
            / "assets"
            / "third_party"
            / "npcap"
            if resource_dir
            else None
        ),
        _downloads_directory(),
    )
    for directory in search_directories:
        candidates.extend(_installer_files(directory))

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        key = os.path.normcase(str(resolved))
        if key in seen or not _looks_like_npcap_installer(resolved):
            continue
        seen.add(key)
        unique.append(resolved)
    return unique


def find_npcap_installer() -> Path | None:
    candidates = npcap_installer_candidates()
    return candidates[0] if candidates else None


class NpcapBootstrap(QObject):
    """Detect Npcap at startup and guide a user-confirmed installation."""

    availability_changed = Signal(bool)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.parent_widget = parent
        self._process: subprocess.Popen[bytes] | None = None
        self._started_at = 0.0
        self._process_finished_at: float | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._poll_installation)

    def check_on_startup(self) -> None:
        installed = find_npcap_dll() is not None
        self.availability_changed.emit(installed)
        if not installed:
            self._offer_install(manual=False)

    def check_or_install(self) -> None:
        dll_path = find_npcap_dll()
        if dll_path is not None:
            self.availability_changed.emit(True)
            QMessageBox.information(
                self.parent_widget,
                "Npcap",
                f"Npcap \u5df2\u5b89\u88c5\uff0c\u5b9e\u6218\u7edf\u8ba1\u53ef\u4ee5\u6b63\u5e38\u4f7f\u7528\u3002\n\n{dll_path}",
            )
            return
        self._offer_install(manual=True)

    def _offer_install(self, *, manual: bool) -> None:
        if self._timer.isActive():
            if manual:
                QMessageBox.information(
                    self.parent_widget,
                    "Npcap",
                    "Npcap \u5b89\u88c5\u7a0b\u5e8f\u6b63\u5728\u8fd0\u884c\uff0c\u5b8c\u6210\u540e\u4f1a\u81ea\u52a8\u590d\u68c0\u3002",
                )
            return

        installer = find_npcap_installer()
        if installer is None:
            box = QMessageBox(self.parent_widget)
            box.setWindowTitle("安装 Npcap")
            box.setIcon(QMessageBox.Information)
            box.setText("实战统计需要 Npcap，当前电脑未检测到。")
            box.setInformativeText(
                "当前发布包未内置有再分发许可的 Npcap 安装器。可打开官方页面下载，"
                "也可以选择电脑上已有的 npcap*.exe。\n\n"
                "安装时不要勾选“仅管理员可访问 Npcap”。"
            )
            download_button = box.addButton("打开官方下载页", QMessageBox.ActionRole)
            select_button = box.addButton("选择安装包", QMessageBox.AcceptRole)
            box.addButton(QMessageBox.Cancel)
            box.exec()
            clicked = box.clickedButton()
            if clicked is download_button:
                QDesktopServices.openUrl(QUrl(_OFFICIAL_DOWNLOAD_URL))
                return
            if clicked is not select_button:
                return
            selected, _ = QFileDialog.getOpenFileName(
                self.parent_widget,
                "\u9009\u62e9 Npcap \u5b89\u88c5\u5305",
                str(_downloads_directory()),
                "Npcap installer (npcap*.exe);;Executable (*.exe)",
            )
            if not selected:
                return
            installer = Path(selected)
            if not _looks_like_npcap_installer(installer):
                QMessageBox.warning(
                    self.parent_widget,
                    "Npcap",
                    "\u9009\u62e9\u7684\u6587\u4ef6\u4e0d\u50cf Npcap \u5b89\u88c5\u5305\uff0c\u672a\u6267\u884c\u3002",
                )
                return
        else:
            result = QMessageBox.question(
                self.parent_widget,
                "\u5b89\u88c5 Npcap",
                "\u5b9e\u6218\u7edf\u8ba1\u9700\u8981 Npcap\uff0c\u5f53\u524d\u7535\u8111\u672a\u68c0\u6d4b\u5230\u3002\n\n"
                f"\u5df2\u627e\u5230\u5b89\u88c5\u5305\uff1a\n{installer}\n\n"
                "\u662f\u5426\u73b0\u5728\u5b89\u88c5\uff1fWindows \u4f1a\u5f39\u51fa\u7ba1\u7406\u5458\u6388\u6743\u7a97\u53e3\u3002\n"
                "\u5b89\u88c5\u65f6\u4e0d\u8981\u52fe\u9009\u201c\u4ec5\u7ba1\u7406\u5458\u53ef\u8bbf\u95ee Npcap\u201d\u3002",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if result != QMessageBox.Yes:
                return

        self._launch_installer(installer)

    def _launch_installer(self, installer: Path) -> None:
        try:
            self._process = subprocess.Popen(
                [str(installer)],
                cwd=str(installer.parent),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            QMessageBox.warning(
                self.parent_widget,
                "Npcap",
                f"\u65e0\u6cd5\u542f\u52a8 Npcap \u5b89\u88c5\u7a0b\u5e8f\uff1a\n{exc}",
            )
            return
        self._started_at = time.monotonic()
        self._process_finished_at = None
        self._timer.start()
        status_bar = getattr(self.parent_widget, "statusBar", None)
        if callable(status_bar):
            status_bar().showMessage("Npcap \u5b89\u88c5\u7a0b\u5e8f\u5df2\u542f\u52a8\uff0c\u6b63\u5728\u7b49\u5f85\u5b89\u88c5\u5b8c\u6210...", 15000)

    def _poll_installation(self) -> None:
        dll_path = find_npcap_dll()
        if dll_path is not None:
            self._timer.stop()
            self._process = None
            self.availability_changed.emit(True)
            QMessageBox.information(
                self.parent_widget,
                "Npcap",
                "Npcap \u5b89\u88c5\u5b8c\u6210\uff0c\u5b9e\u6218\u7edf\u8ba1\u5df2\u53ef\u4f7f\u7528\u3002",
            )
            return

        now = time.monotonic()
        return_code = self._process.poll() if self._process is not None else None
        if return_code is not None and self._process_finished_at is None:
            self._process_finished_at = now
        if return_code not in (None, 0) and self._process_finished_at is not None and now - self._process_finished_at >= 2:
            self._finish_with_error(f"Npcap \u5b89\u88c5\u7a0b\u5e8f\u5df2\u9000\u51fa\uff08\u4ee3\u7801 {return_code}\uff09\uff0c\u4f46\u4ecd\u672a\u68c0\u6d4b\u5230 Npcap\u3002")
            return
        if now - self._started_at >= _INSTALL_TIMEOUT_SECONDS:
            self._finish_with_error("\u7b49\u5f85 Npcap \u5b89\u88c5\u8d85\u65f6\u3002\u53ef\u5728\u201c\u8bbe\u7f6e -> \u68c0\u67e5/\u5b89\u88c5 Npcap\u201d\u4e2d\u91cd\u8bd5\u3002")

    def _finish_with_error(self, message: str) -> None:
        self._timer.stop()
        self._process = None
        self.availability_changed.emit(False)
        QMessageBox.warning(self.parent_widget, "Npcap", message)
