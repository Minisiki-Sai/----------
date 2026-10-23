from __future__ import annotations

import json
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..live_dps.supabase_score_sync import TeamScoreSupabaseSyncService
from ..live_dps.team_score_sync import (
    TeamScoreExportRow,
    TeamScoreSyncStore,
    default_team_score_sync_path,
    protect_secret,
    resolve_score_nest,
    unprotect_secret,
)


_SETTINGS_PREFIX = "live_dps/team_score_sync"
_BUNDLED_CONFIG_RELATIVE_PATH = (
    Path("dragon_nest_builder")
    / "data"
    / "modular"
    / "supabase_score_sync.json"
)
_LEGACY_DEFAULT_SCORE_ENDPOINTS = {
    "https://cbszidcntkwdkqcmnwfg.supabase.co",
    "https://cbszidcntkwdkqcmnwfg.supabase.co/functions/v1/team-score-sync",
}


def _migrated_score_endpoint(stored: object, bundled: object) -> str:
    stored_text = str(stored or "").strip().rstrip("/")
    bundled_text = str(bundled or "").strip().rstrip("/")
    if stored_text.casefold() in _LEGACY_DEFAULT_SCORE_ENDPOINTS and bundled_text:
        return bundled_text
    return stored_text or bundled_text


@dataclass(frozen=True, slots=True)
class TeamScoreSyncConfig:
    enabled: bool
    endpoint: str
    upload_token: str
    installation_id: str

    @property
    def ready(self) -> bool:
        return bool(
            self.enabled
            and self.endpoint.strip()
            and self.upload_token.strip()
            and self.installation_id.strip()
        )


@dataclass(frozen=True, slots=True)
class TeamScoreSyncJob:
    capture_path: Path
    boss_names: tuple[str, ...]
    target_ids: tuple[str, ...]
    rows: tuple[TeamScoreExportRow, ...]
    config: TeamScoreSyncConfig


def _setting_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"1", "true", "yes", "on"}


def _bundled_team_score_sync_defaults() -> dict[str, object]:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    path = base / _BUNDLED_CONFIG_RELATIVE_PATH
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def load_team_score_sync_config(settings: QSettings) -> TeamScoreSyncConfig:
    bundled = _bundled_team_score_sync_defaults()
    installation_id = str(
        settings.value(f"{_SETTINGS_PREFIX}/installation_id", "") or ""
    ).strip()
    if not installation_id:
        installation_id = str(uuid.uuid4())
        settings.setValue(
            f"{_SETTINGS_PREFIX}/installation_id", installation_id
        )
        settings.sync()
    return TeamScoreSyncConfig(
        enabled=_setting_bool(
            settings.value(
                f"{_SETTINGS_PREFIX}/enabled",
                bundled.get("enabled", True),
            ),
            _setting_bool(bundled.get("enabled"), True),
        ),
        endpoint=_migrated_score_endpoint(
            settings.value(f"{_SETTINGS_PREFIX}/supabase_endpoint", ""),
            bundled.get("endpoint", ""),
        ),
        upload_token=(
            unprotect_secret(
                str(
                    settings.value(
                        f"{_SETTINGS_PREFIX}/supabase_upload_token", ""
                    )
                    or ""
                )
            )
            if settings.contains(
                f"{_SETTINGS_PREFIX}/supabase_upload_token"
            )
            else str(bundled.get("upload_token", "") or "").strip()
        ),
        installation_id=installation_id,
    )


def save_team_score_sync_config(
    settings: QSettings, config: TeamScoreSyncConfig
) -> None:
    settings.setValue(f"{_SETTINGS_PREFIX}/enabled", config.enabled)
    settings.setValue(
        f"{_SETTINGS_PREFIX}/supabase_endpoint", config.endpoint.strip()
    )
    settings.setValue(
        f"{_SETTINGS_PREFIX}/supabase_upload_token",
        protect_secret(config.upload_token),
    )
    settings.setValue(
        f"{_SETTINGS_PREFIX}/installation_id", config.installation_id
    )
    settings.sync()


class TeamScoreSyncWorker(QObject):
    finished = Signal(bool, str)

    def __init__(self, job: TeamScoreSyncJob) -> None:
        super().__init__()
        self.job = job

    @Slot()
    def run(self) -> None:
        store: TeamScoreSyncStore | None = None
        capture_hash = ""
        try:
            nest = resolve_score_nest(
                self.job.boss_names,
                capture_path=self.job.capture_path,
                target_ids=self.job.target_ids,
            )
            store = TeamScoreSyncStore()
            capture_hash, duplicate, stored_rows = store.record_capture(
                self.job.capture_path, nest, self.job.rows
            )
            if not self.job.config.ready:
                suffix = (
                    "；该数据此前已记录"
                    if duplicate
                    else f"；已保存 {stored_rows} 名队员"
                )
                self.finished.emit(
                    True,
                    f"评分已进入本地离线队列{suffix}，配置云端服务后可补传",
                )
                return
            service = TeamScoreSupabaseSyncService(
                self.job.config.endpoint,
                self.job.config.upload_token,
                self.job.config.installation_id,
            )
            result = service.sync_store(store)
            self.finished.emit(True, result.summary())
        except Exception as exc:
            if store is not None:
                try:
                    store.mark_supabase_error(
                        str(exc), (capture_hash,) if capture_hash else ()
                    )
                except Exception:
                    pass
            self.finished.emit(False, str(exc))
        finally:
            if store is not None:
                store.close()


class TeamScoreOfflineQueueWorker(QObject):
    finished = Signal(bool, str)

    def __init__(self, config: TeamScoreSyncConfig) -> None:
        super().__init__()
        self.config = config

    @Slot()
    def run(self) -> None:
        if not self.config.ready:
            self.finished.emit(False, "云端评分服务尚未配置")
            return
        try:
            service = TeamScoreSupabaseSyncService(
                self.config.endpoint,
                self.config.upload_token,
                self.config.installation_id,
            )
            with TeamScoreSyncStore() as store:
                result = service.sync_store(store)
            self.finished.emit(True, result.summary())
        except Exception as exc:
            self.finished.emit(False, str(exc))


class SupabaseScoreSyncDialog(QDialog):
    def __init__(self, settings: QSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("云端评分自动上传")
        self.setMinimumWidth(660)
        config = load_team_score_sync_config(settings)

        root = QVBoxLayout(self)
        description = QLabel(
            "评分先按 .dpscap 内容哈希写入本地 SQLite 离线队列，再由云端 "
            "同步服务批量写入 PostgreSQL。断网或服务不可用不会影响评分；"
            "恢复联网后会自动补传。"
        )
        description.setWordWrap(True)
        description.setStyleSheet("color:#41596d;")
        root.addWidget(description)

        self.enabled_check = QCheckBox("自动上传新记录并补传离线队列")
        self.enabled_check.setChecked(config.enabled)
        root.addWidget(self.enabled_check)

        form = QFormLayout()
        self.endpoint_edit = QLineEdit(config.endpoint)
        self.endpoint_edit.setPlaceholderText(
            "https://.../team-score-sync"
        )
        form.addRow("云端服务地址", self.endpoint_edit)

        self.token_edit = QLineEdit(config.upload_token)
        self.token_edit.setEchoMode(QLineEdit.Password)
        self.token_edit.setPlaceholderText(
            "TEAM_SCORE_UPLOAD_TOKEN（不是数据库 API Key）"
        )
        form.addRow("评分上传密钥", self.token_edit)

        self.installation_edit = QLineEdit(config.installation_id)
        self.installation_edit.setReadOnly(True)
        form.addRow("本机安装标识", self.installation_edit)
        root.addLayout(form)

        queue_path = default_team_score_sync_path()
        try:
            with TeamScoreSyncStore(queue_path) as store:
                pending = store.pending_supabase_count()
        except Exception:
            pending = 0
        self.queue_label = QLabel(
            f"本地待上传：{pending} 份\n离线队列：{queue_path}"
        )
        self.queue_label.setWordWrap(True)
        self.queue_label.setStyleSheet("color:#687f91;")
        root.addWidget(self.queue_label)

        security_hint = QLabel(
            "设置页手工保存的上传密钥使用 Windows DPAPI 加密；"
            "这里不能填写数据库 API Key。服务端 API Key 仅存在于云函数环境变量中，"
            "不会写入模拟器或安装包。"
        )
        security_hint.setWordWrap(True)
        security_hint.setStyleSheet("color:#687f91;")
        root.addWidget(security_hint)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        button_row = QHBoxLayout()
        self.test_button = QPushButton("测试连接并补传")
        self.test_button.clicked.connect(self._test_connection)
        button_row.addWidget(self.test_button)
        button_row.addStretch(1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        button_row.addWidget(buttons)
        root.addLayout(button_row)

    def _current_config(self) -> TeamScoreSyncConfig:
        return TeamScoreSyncConfig(
            enabled=self.enabled_check.isChecked(),
            endpoint=self.endpoint_edit.text().strip(),
            upload_token=self.token_edit.text().strip(),
            installation_id=self.installation_edit.text().strip(),
        )

    def _test_connection(self) -> None:
        config = self._current_config()
        if not config.endpoint or not config.upload_token:
            QMessageBox.warning(
                self,
                "云端评分自动上传",
                "请先填写云端服务地址和评分上传密钥。",
            )
            return
        self.test_button.setEnabled(False)
        self.status_label.setStyleSheet("color:#41596d;")
        self.status_label.setText("正在连接云端评分服务并补传离线队列...")
        self.status_label.repaint()
        try:
            service = TeamScoreSupabaseSyncService(
                config.endpoint, config.upload_token, config.installation_id
            )
            version = service.health_check()
            with TeamScoreSyncStore() as store:
                result = service.sync_store(store)
            self.status_label.setStyleSheet("color:#23744b;")
            self.status_label.setText(
                f"连接成功（服务版本 {version}）。{result.summary()}"
            )
            self.queue_label.setText(
                f"本地待上传：{result.remaining_captures} 份\n"
                f"离线队列：{default_team_score_sync_path()}"
            )
        except Exception as exc:
            self.status_label.setStyleSheet("color:#a33b45;")
            self.status_label.setText(f"连接失败：{exc}")
        finally:
            self.test_button.setEnabled(True)

    def _save(self) -> None:
        config = self._current_config()
        if config.enabled and (not config.endpoint or not config.upload_token):
            answer = QMessageBox.question(
                self,
                "云端评分自动上传",
                "自动上传已开启，但云端服务地址或上传密钥尚未填写。\n"
                "评分仍会进入本地离线队列，是否继续保存？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if answer != QMessageBox.Yes:
                return
        save_team_score_sync_config(self.settings, config)
        self.accept()


# Preserve the old import name for third-party extensions and older page code.
TencentScoreSyncDialog = SupabaseScoreSyncDialog


def start_team_score_sync_thread(
    owner: QObject,
    job: TeamScoreSyncJob,
    callback,
) -> tuple[QThread, TeamScoreSyncWorker]:
    thread = QThread(owner)
    worker = TeamScoreSyncWorker(job)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(callback)
    worker.finished.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    return thread, worker


def start_team_score_queue_sync_thread(
    owner: QObject,
    config: TeamScoreSyncConfig,
    callback,
) -> tuple[QThread, TeamScoreOfflineQueueWorker]:
    thread = QThread(owner)
    worker = TeamScoreOfflineQueueWorker(config)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(callback)
    worker.finished.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    return thread, worker
