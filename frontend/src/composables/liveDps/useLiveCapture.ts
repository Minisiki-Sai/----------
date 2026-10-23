import { computed, onMounted, onUnmounted, ref } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { emitTo } from "@tauri-apps/api/event";
import { simulatorApi } from "../../services/api";
import type { BuffOverlaySettings, BuffOverlayStatus, CaptureStatus } from "../../types/api";
import { overlaySessionEnding } from "../../utils/overlaySession";
import {
  ALL_OVERLAY_WINDOW_LABELS,
  BUFF_OVERLAY_STORAGE_KEY,
  enabledOverlayWindowLabels,
  readBuffOverlaySettings,
} from "../../utils/buffOverlaySettings";

const CAPTURE_NOTICE_MS = 900;
const TARGET_SELECTION_STORAGE_KEY = "dragon-nest-v420-buff-overlay-enabled";

export function isCaptureActiveState(state: unknown): boolean {
  return ["starting", "running"].includes(String(state || ""));
}

export function useLiveCapture() {
  const capture = ref<CaptureStatus | null>(null);
  const loading = ref(false);
  const error = ref("");
  const captureToggles = ref<Record<string, boolean>>({});
  const captureToggleLabels = ref<Record<string, string>>({});
  const overlaySettingsOpen = ref(false);
  const overlayOptions = ref<BuffOverlayStatus["buffOptions"]>([]);
  const overlaySettings = ref<BuffOverlaySettings>(readBuffOverlaySettings());
  let captureTimer: number | null = null;
  let refreshInFlight = false;
  let refreshPending = false;
  let requestGeneration = 0;

  const captureRunning = computed(() => isCaptureActiveState(capture.value?.state));
  const captureStopping = computed(() => capture.value?.state === "stopping");
  const captureBusy = computed(() => captureRunning.value || captureStopping.value);

  function isTauri(): boolean {
    return "__TAURI_INTERNALS__" in window;
  }

  async function refreshCapture() {
    if (refreshInFlight) {
      refreshPending = true;
      return;
    }
    refreshInFlight = true;
    try {
      do {
        refreshPending = false;
        const generation = requestGeneration;
        const wasRunning = captureRunning.value;
        try {
          const next = await simulatorApi.captureStatus(false);
          if (generation !== requestGeneration) continue;
          capture.value = next;
          if (!wasRunning && isCaptureActiveState(next.state)) await showStartedOverlay();
          if (wasRunning && !isCaptureActiveState(next.state)) await hideAfterStop();
          if (next.error) error.value = next.error;
        } catch (reason) {
          if (generation !== requestGeneration) continue;
          const message = reason instanceof Error ? reason.message : String(reason);
          if (capture.value && isCaptureActiveState(capture.value.state)) {
            capture.value = { ...capture.value, state: "error", status: "解析核心连接中断", error: message };
          }
          error.value = message;
        }
      } while (refreshPending);
    } finally {
      refreshInFlight = false;
    }
  }

  async function emitNotice(message: string, tone: "start" | "stop") {
    if (!isTauri()) return;
    const labels = enabledOverlayWindowLabels(overlaySettings.value);
    await Promise.allSettled(labels.map(label => emitTo(label, "buff-overlay-capture-notice", {
      message, tone, durationMs: CAPTURE_NOTICE_MS,
    })));
  }

  async function applyOverlayVisibility(visible: boolean): Promise<boolean> {
    if (!isTauri()) return true;
    try {
      await invoke("set_buff_overlay_visible", {
        visible,
        overlayLabels: enabledOverlayWindowLabels(overlaySettings.value),
      });
      return true;
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : String(reason);
      return false;
    }
  }

  async function showStartedOverlay(): Promise<void> {
    localStorage.setItem(TARGET_SELECTION_STORAGE_KEY, "1");
    if (await applyOverlayVisibility(true)) await emitNotice("开始采集", "start");
  }

  let stoppingNotice: Promise<void> | null = null;
  function hideAfterStop(): Promise<void> {
    if (localStorage.getItem(TARGET_SELECTION_STORAGE_KEY) !== "1") return Promise.resolve();
    if (stoppingNotice) return stoppingNotice;
    stoppingNotice = (async () => {
      await emitNotice("关闭采集", "stop");
      await new Promise(resolve => window.setTimeout(resolve, CAPTURE_NOTICE_MS));
      if (!isCaptureActiveState(capture.value?.state)) await applyOverlayVisibility(false);
    })().finally(() => { stoppingNotice = null; });
    return stoppingNotice;
  }

  async function startCapture() {
    requestGeneration += 1;
    loading.value = true;
    error.value = "";
    try {
      capture.value = await simulatorApi.startCapture(0);
      await showStartedOverlay();
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : String(reason);
    } finally {
      loading.value = false;
      await refreshCapture();
    }
  }

  async function stopCapture() {
    requestGeneration += 1;
    refreshPending = false;
    if (capture.value) capture.value = { ...capture.value, state: "stopping", status: "正在结束统计" };
    try {
      await hideAfterStop();
      capture.value = await simulatorApi.stopCapture();
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : String(reason);
    } finally {
      await refreshCapture();
    }
  }

  async function loadCaptureToggles() {
    try {
      const payload = await simulatorApi.captureToggles();
      captureToggles.value = payload.toggles || {};
      captureToggleLabels.value = payload.labels || {};
    } catch {
      // Keep the panel usable while the core is still starting.
    }
  }

  async function setCaptureToggle(key: string, enabled: boolean) {
    const previous = captureToggles.value[key];
    captureToggles.value = { ...captureToggles.value, [key]: enabled };
    try {
      const payload = await simulatorApi.setCaptureToggles(captureToggles.value);
      captureToggles.value = payload.toggles || captureToggles.value;
      captureToggleLabels.value = payload.labels || captureToggleLabels.value;
    } catch (reason) {
      captureToggles.value = { ...captureToggles.value, [key]: previous };
      error.value = reason instanceof Error ? reason.message : String(reason);
    }
  }

  async function openOverlaySettings() {
    overlaySettings.value = readBuffOverlaySettings();
    try {
      const payload = await simulatorApi.buffOverlayStatus(true);
      overlayOptions.value = payload.buffOptions || [];
    } catch {
      // A transient catalog failure must not block the local appearance settings.
    }
    overlaySettingsOpen.value = true;
  }

  function saveOverlaySettings(value: BuffOverlaySettings) {
    overlaySettings.value = value;
    localStorage.setItem(BUFF_OVERLAY_STORAGE_KEY, JSON.stringify(value));
    if (isTauri()) {
      void Promise.allSettled(ALL_OVERLAY_WINDOW_LABELS.map(label =>
        emitTo(label, "buff-overlay-settings-changed", value)));
      if (localStorage.getItem(TARGET_SELECTION_STORAGE_KEY) === "1") void applyOverlayVisibility(true);
    }
    overlaySettingsOpen.value = false;
  }

  async function adjustOverlayLayout(): Promise<boolean> {
    if (!isTauri()) {
      error.value = "悬浮窗位置调整仅支持桌面版。";
      return false;
    }
    try {
      await invoke("set_buff_overlay_visible", {
        visible: true,
        overlayLabels: [...ALL_OVERLAY_WINDOW_LABELS],
      });
      await Promise.allSettled(ALL_OVERLAY_WINDOW_LABELS.map(label =>
        emitTo(label, "buff-overlay-layout-mode", true)));
      return true;
    } catch (reason) {
      error.value = reason instanceof Error ? reason.message : String(reason);
      return false;
    }
  }

  function finishOverlayLayout() {
    if (!isTauri()) return;
    const visible = localStorage.getItem(TARGET_SELECTION_STORAGE_KEY) === "1";
    void invoke("set_buff_overlay_visible", {
      visible,
      overlayLabels: enabledOverlayWindowLabels(overlaySettings.value),
    }).catch(() => undefined);
  }

  const onHotkeyState = () => { void refreshCapture(); };

  onMounted(() => {
    void refreshCapture();
    void loadCaptureToggles();
    captureTimer = window.setInterval(() => void refreshCapture(), 1000);
    window.addEventListener("dragon-nest-live-hotkey-state", onHotkeyState);
  });

  onUnmounted(() => {
    if (captureTimer !== null) window.clearInterval(captureTimer);
    window.removeEventListener("dragon-nest-live-hotkey-state", onHotkeyState);
  });

  return {
    capture, loading, error,
    captureRunning, captureStopping, captureBusy,
    captureToggles, captureToggleLabels, setCaptureToggle,
    overlaySettingsOpen, overlayOptions, overlaySettings,
    startCapture, stopCapture, refreshCapture,
    openOverlaySettings, saveOverlaySettings,
    adjustOverlayLayout, finishOverlayLayout,
  };
}
