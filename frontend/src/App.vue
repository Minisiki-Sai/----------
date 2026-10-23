<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { AlertTriangle, Database, LoaderCircle, Moon, Sun } from "@lucide/vue";
import { getCurrentWindow } from "@tauri-apps/api/window";
import LiveDpsView from "./views/LiveDpsView.vue";
import SettingsView from "./views/SettingsView.vue";
import BuffOverlay from "./components/BuffOverlay.vue";
import { simulatorApi } from "./services/api";
import { APP_NAME, APP_SHORT_NAME, APP_VERSION_LABEL } from "./appVersion";
import { readGlobalHotkeyConfiguration } from "./utils/globalHotkeys";
import {
  beginOverlaySession,
  endOverlaySession,
  overlaySessionEnding,
} from "./utils/overlaySession";
import { enabledOverlayWindowLabels, readBuffOverlaySettings, type OverlayWindowKind } from "./utils/buffOverlaySettings";
import { createOverlayEventPublisher, type OverlayEventPublisher } from "./utils/overlayEventPublisher";

type Theme = "light" | "dark";
const THEME_STORAGE_KEY = "dnact-frost-theme";

const params = new URLSearchParams(window.location.search);
const overlayViewName = params.get("view") || "";
const overlayKind = ({
  "buff-overlay": "status",
  "skill-countdown-overlay": "skill",
} as Record<string, OverlayWindowKind>)[overlayViewName] || null;
const isOverlayWindow = Boolean(overlayKind);
const tauriAvailable = Boolean((window as typeof window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__);

beginOverlaySession(localStorage, isOverlayWindow);
if (isOverlayWindow) {
  document.documentElement.classList.add("dn-overlay-scene");
  document.body.classList.add("dn-overlay-scene");
} else {
  document.documentElement.classList.add("frost-scene");
  document.body.classList.add("frost-scene");
}

const theme = ref<Theme>(localStorage.getItem(THEME_STORAGE_KEY) === "dark" ? "dark" : "light");
watch(theme, value => {
  if (isOverlayWindow) return;
  document.documentElement.dataset.theme = value;
  localStorage.setItem(THEME_STORAGE_KEY, value);
}, { immediate: true });

const page = ref<"control" | "npcap">("control");
const connected = ref(false);
const busy = ref(true);
const error = ref("");
let unlistenHotkey: UnlistenFn | null = null;
let unlistenExit: UnlistenFn | null = null;
let overlayPublisher: OverlayEventPublisher | null = null;

const themeLabel = computed(() => (theme.value === "dark" ? "深色" : "浅色"));
const appWindow = tauriAvailable ? getCurrentWindow() : null;
const closeApp = () => void appWindow?.close();
const minimizeApp = () => void appWindow?.minimize();
const toggleMaximize = () => void appWindow?.toggleMaximize();

async function connect(retries = 8) {
  busy.value = true;
  error.value = "";
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    try {
      await simulatorApi.health();
      connected.value = true;
      busy.value = false;
      return;
    } catch (reason) {
      if (attempt === retries) error.value = reason instanceof Error ? reason.message : String(reason);
      else await new Promise(resolve => window.setTimeout(resolve, 350));
    }
  }
  busy.value = false;
}

function toggleTheme() {
  theme.value = theme.value === "dark" ? "light" : "dark";
}

async function keepOverlaysInSync() {
  if (!tauriAvailable || isOverlayWindow) return;
  if (localStorage.getItem("dragon-nest-v420-buff-overlay-enabled") !== "1") return;
  try {
    await invoke("set_buff_overlay_visible", {
      visible: true,
      overlayLabels: enabledOverlayWindowLabels(readBuffOverlaySettings()),
    });
  } catch {
    // The overlay windows are re-created on the next capture start.
  }
}

async function handleHotkey(action: string) {
  if (overlaySessionEnding()) return;
  if (action === "start-capture") {
    const current = await simulatorApi.captureStatus(false);
    if (!["starting", "running"].includes(current.state)) {
      await simulatorApi.startCapture(0);
      localStorage.setItem("dragon-nest-v420-buff-overlay-enabled", "1");
      await keepOverlaysInSync();
    }
    window.dispatchEvent(new CustomEvent("dragon-nest-live-hotkey-state", { detail: { action: "start-capture" } }));
    return;
  }
  if (action !== "toggle-overlay") return;
  const visible = localStorage.getItem("dragon-nest-v420-buff-overlay-enabled") !== "1";
  if (visible) {
    const current = await simulatorApi.captureStatus(false);
    if (!["starting", "running"].includes(current.state)) await simulatorApi.startCapture(0);
  }
  localStorage.setItem("dragon-nest-v420-buff-overlay-enabled", visible ? "1" : "0");
  await invoke("set_buff_overlay_visible", {
    visible,
    overlayLabels: enabledOverlayWindowLabels(readBuffOverlaySettings()),
  });
  window.dispatchEvent(new CustomEvent("dragon-nest-live-hotkey-state", { detail: { action: "toggle-overlay", visible } }));
}

onMounted(async () => {
  if (isOverlayWindow) return;
  void connect();
  overlayPublisher = createOverlayEventPublisher();
  overlayPublisher.start();
  if (!tauriAvailable) return;
  try {
    await invoke("configure_global_hotkeys", readGlobalHotkeyConfiguration());
    unlistenHotkey = await listen<string>("dragon-nest-global-hotkey", event => {
      void handleHotkey(event.payload).catch(reason => {
        error.value = reason instanceof Error ? reason.message : String(reason);
      });
    });
  } catch {
    // Browser preview has no native hotkey host.
  }
  try {
    // The native shell waits for this acknowledgement before exiting. Remove
    // the build-save delay: this build has no persistent editor state.
    unlistenExit = await listen("dragon-nest-save-builds-before-exit", () => {
      endOverlaySession(localStorage);
      overlayPublisher?.stop();
      void invoke("finish_exit_after_build_save").catch(() => undefined);
    });
  } catch {
    // Older shells fall back to their own exit timeout.
  }
});

onUnmounted(() => {
  if (unlistenHotkey) unlistenHotkey();
  if (unlistenExit) unlistenExit();
  overlayPublisher?.stop();
  overlayPublisher = null;
});
</script>

<template>
  <BuffOverlay v-if="overlayKind" :view="overlayKind" />
  <div v-else class="frost-app">
    <div class="frost-titlebar" data-tauri-drag-region>
      <button class="frost-traffic is-close" type="button" title="关闭" @click="closeApp" />
      <button class="frost-traffic is-min" type="button" title="最小化" @click="minimizeApp" />
      <button class="frost-traffic is-max" type="button" title="最大化" @click="toggleMaximize" />
    </div>
    <main class="frost-panel">
      <template v-if="!connected">
        <div class="frost-boot">
          <LoaderCircle v-if="busy" :size="30" class="frost-spin" />
          <AlertTriangle v-else :size="30" />
          <h1>{{ busy ? "正在唤醒解析核心" : "解析核心未响应" }}</h1>
          <p>{{ busy ? "正在连接本机抓包解析服务，通常只需几秒。" : (error || `请确认 ${APP_VERSION_LABEL} 后端已启动后重试。`) }}</p>
          <button v-if="!busy" class="frost-pill" @click="connect()">重新连接</button>
        </div>
      </template>
      <template v-else>
        <KeepAlive><LiveDpsView v-if="page === 'control'" @open-npcap="page = 'npcap'" /></KeepAlive>
        <KeepAlive><SettingsView v-if="page === 'npcap'" @back="page = 'control'" /></KeepAlive>
      </template>
      <div v-if="error && connected" class="frost-note is-error" role="alert" style="margin-top: 12px">
        <AlertTriangle :size="15" />{{ error }}
      </div>
    </main>

    <aside class="frost-rail">
      <button class="frost-brand" type="button" :title="APP_NAME" @click="page = 'control'">
        <img src="/app-icon.png" alt="" />
        <span class="frost-brand-copy">
          <strong>{{ APP_SHORT_NAME }}</strong>
          <span>{{ APP_VERSION_LABEL }} · 抓包倒计时</span>
        </span>
      </button>

      <p class="frost-rail-caption">设置</p>
      <nav class="frost-rail-nav" aria-label="设置导航">
        <button
          class="frost-rail-item"
          :class="{ 'is-active': page === 'npcap' }"
          type="button"
          @click="page = 'npcap'"
        >
          <Database :size="17" :stroke-width="1.7" />
          <span>Ncap 路径</span>
        </button>
      </nav>

      <div class="frost-rail-footer">
        <span>{{ connected ? "解析核心已连接" : "正在连接解析核心" }}</span>
        <button class="frost-theme-toggle" type="button" @click="toggleTheme">
          <Moon v-if="theme === 'light'" :size="13" />
          <Sun v-else :size="13" />
          {{ themeLabel }}主题
        </button>
      </div>
    </aside>
  </div>
</template>
