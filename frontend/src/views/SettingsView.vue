<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { invoke } from "@tauri-apps/api/core";
import { ArrowLeft, CheckCircle2, FolderOpen, LoaderCircle, RefreshCw, ShieldAlert, XCircle } from "@lucide/vue";
import { simulatorApi, type NpcapConfigPayload } from "../services/api";
import {
  DEFAULT_CAPTURE_HOTKEY,
  DEFAULT_OVERLAY_HOTKEY,
  HOTKEY_STORAGE_KEYS,
  readGlobalHotkeyConfiguration,
} from "../utils/globalHotkeys";

const emit = defineEmits<{ back: [] }>();
const tauriAvailable = Boolean((window as typeof window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__);

const npcapPath = ref("");
const config = ref<NpcapConfigPayload | null>(null);
const busy = ref<"" | "detect" | "test" | "save" | "clear">("");
const message = ref("");
const messageTone = ref<"" | "is-ok" | "is-warn" | "is-error">("");
const adapterCount = ref(0);
const savedHint = ref("");

const captureHotkey = ref(localStorage.getItem(HOTKEY_STORAGE_KEYS.capture) || DEFAULT_CAPTURE_HOTKEY);
const overlayHotkey = ref(localStorage.getItem(HOTKEY_STORAGE_KEYS.overlay) || DEFAULT_OVERLAY_HOTKEY);
const hotkeyMessage = ref("");
const hotkeyError = ref(false);
const minimizeToTray = ref(localStorage.getItem("dragon-nest-v420-minimize-to-tray") !== "0");
const trayMessage = ref("");

const sourceLabel = computed(() => ({
  configured: "手动指定",
  portable: "随包组件",
  system: "系统安装",
  missing: "未找到",
}[config.value?.source || "missing"]));
const configuredInvalid = computed(() =>
  Boolean(config.value?.configuredPath) && config.value?.configuredValid === false);

function setMessage(text: string, tone: "" | "is-ok" | "is-warn" | "is-error") {
  message.value = text;
  messageTone.value = tone;
}

function applyConfig(next: NpcapConfigPayload) {
  config.value = next;
  npcapPath.value = next.configuredPath || "";
  adapterCount.value = next.adapterCount || 0;
}

function describeConfig(next: NpcapConfigPayload) {
  if (!next.installed) {
    setMessage(next.message || "未找到 Npcap：请指定 wpcap.dll，或安装随包 / 官方 Npcap。", "is-warn");
    return;
  }
  if (next.configuredPath && next.configuredValid === false) {
    setMessage(next.message, "is-error");
    return;
  }
  setMessage(next.message, "is-ok");
}

async function loadConfig() {
  try {
    applyConfig(await simulatorApi.npcapConfig());
  } catch (reason) {
    setMessage(reason instanceof Error ? reason.message : String(reason), "is-error");
  }
}

async function choose(kind: "file" | "folder") {
  if (!tauriAvailable) {
    const typed = window.prompt(kind === "file" ? "wpcap.dll 完整路径" : "Npcap 安装目录");
    if (typed) npcapPath.value = typed;
    return;
  }
  try {
    const picked = await invoke<string | null>(kind === "file" ? "pick_npcap_path" : "pick_npcap_folder");
    if (picked) {
      npcapPath.value = picked;
      savedHint.value = "已选择路径，点「保存」生效。";
    }
  } catch (reason) {
    setMessage(reason instanceof Error ? reason.message : String(reason), "is-error");
  }
}

async function savePath() {
  busy.value = "save";
  savedHint.value = "";
  try {
    const next = await simulatorApi.saveNpcapConfig(npcapPath.value.trim());
    applyConfig(next);
    describeConfig(next);
    savedHint.value = next.configuredPath
      ? `已保存到 ${next.configPath}`
      : "已恢复自动检测。";
  } catch (reason) {
    setMessage(reason instanceof Error ? reason.message : String(reason), "is-error");
  } finally {
    busy.value = "";
  }
}

async function clearPath() {
  npcapPath.value = "";
  busy.value = "clear";
  await savePath();
  busy.value = "";
  setMessage("已清除手动路径，改为自动检测随包组件与系统 Npcap。", "is-ok");
}

async function detect() {
  busy.value = "detect";
  setMessage("正在检查随包组件、系统目录与手动路径…", "");
  try {
    const result = await simulatorApi.detectNpcap();
    await loadConfig();
    if (result.installed) {
      setMessage(`已找到可用的 Npcap：${result.resolvedPath}`, "is-ok");
    } else {
      const hint = result.candidates.length
        ? `检测到 ${result.candidates.length} 个候选文件，但均不可加载。`
        : "未找到 Npcap：随包目录与系统目录均无 wpcap.dll。";
      setMessage(hint, "is-warn");
    }
  } catch (reason) {
    setMessage(reason instanceof Error ? reason.message : String(reason), "is-error");
  } finally {
    busy.value = "";
  }
}

async function testConnection() {
  busy.value = "test";
  setMessage("正在加载 Npcap 并枚举抓包接口…", "");
  try {
    const result = await simulatorApi.testNpcap();
    adapterCount.value = result.adapterCount || 0;
    if (result.ok) {
      setMessage(`测试抓包成功：已加载 ${result.resolvedPath}，可用接口 ${result.adapterCount} 个。`, "is-ok");
    } else {
      const stage = {
        load: "无法加载 wpcap.dll（文件缺失或位数不匹配）",
        permission: "权限不足：请以管理员身份运行，或重新安装 Npcap 时取消“仅管理员访问”",
        adapter: "没有可用抓包接口：请确认网卡已启用并已连接游戏",
        unknown: "未知错误",
        "": "抓包测试失败",
      }[result.failedStage || "unknown"];
      setMessage(`${stage}${result.message ? `：${result.message}` : ""}`, "is-error");
    }
  } catch (reason) {
    setMessage(reason instanceof Error ? reason.message : String(reason), "is-error");
  } finally {
    busy.value = "";
  }
}

async function installNpcap() {
  if (!tauriAvailable) return;
  try {
    await invoke("install_npcap", { installerPath: null });
    setMessage("已启动随包 Npcap 安装程序；完成后请点击「检测」。", "");
  } catch (reason) {
    setMessage(reason instanceof Error ? reason.message : String(reason), "is-error");
  }
}

function keyName(event: KeyboardEvent) {
  if (/^F([1-9]|1[0-9]|2[0-4])$/.test(event.key)) return event.key;
  if (event.key.length === 1 && /[a-z0-9]/i.test(event.key)) return event.key.toUpperCase();
  return "";
}

function recordHotkey(event: KeyboardEvent, target: "capture" | "overlay") {
  event.preventDefault();
  if (["Control", "Alt", "Shift", "Meta"].includes(event.key)) return;
  const key = keyName(event);
  if (!key) return;
  const parts = [event.ctrlKey ? "Ctrl" : "", event.altKey ? "Alt" : "", event.shiftKey ? "Shift" : "", event.metaKey ? "Super" : "", key].filter(Boolean);
  if (parts.length < 2) {
    hotkeyError.value = true;
    hotkeyMessage.value = "快捷键至少需要 Ctrl、Alt、Shift 或 Win 中的一个修饰键。";
    return;
  }
  if (target === "capture") captureHotkey.value = parts.join("+");
  else overlayHotkey.value = parts.join("+");
  hotkeyError.value = false;
  hotkeyMessage.value = "";
}

async function saveHotkeys() {
  hotkeyError.value = false;
  hotkeyMessage.value = "";
  if (captureHotkey.value === overlayHotkey.value) {
    hotkeyError.value = true;
    hotkeyMessage.value = "两个功能不能使用同一个快捷键。";
    return;
  }
  try {
    if (tauriAvailable) {
      await invoke("configure_global_hotkeys", {
        ...readGlobalHotkeyConfiguration(),
        startCaptureHotkey: captureHotkey.value,
        toggleOverlayHotkey: overlayHotkey.value,
      });
    }
    localStorage.setItem(HOTKEY_STORAGE_KEYS.capture, captureHotkey.value);
    localStorage.setItem(HOTKEY_STORAGE_KEYS.overlay, overlayHotkey.value);
    hotkeyMessage.value = "快捷键已保存并立即生效。";
  } catch (reason) {
    hotkeyError.value = true;
    hotkeyMessage.value = reason instanceof Error ? reason.message : String(reason);
  }
}

async function saveTray() {
  const enabled = minimizeToTray.value;
  try {
    if (tauriAvailable) await invoke("set_minimize_to_tray", { enabled });
    localStorage.setItem("dragon-nest-v420-minimize-to-tray", enabled ? "1" : "0");
    trayMessage.value = enabled ? "关闭窗口后继续在系统托盘运行。" : "关闭窗口即退出程序。";
  } catch (reason) {
    minimizeToTray.value = !enabled;
    trayMessage.value = reason instanceof Error ? reason.message : String(reason);
  }
}

onMounted(async () => {
  await loadConfig();
  if (!tauriAvailable) return;
  try {
    minimizeToTray.value = await invoke<boolean>("get_minimize_to_tray");
  } catch {
    // Older shells keep the stored value.
  }
});
</script>

<template>
  <div class="npcap-page">
    <header class="frost-page-head">
      <button class="frost-back" type="button" title="返回主面板" @click="emit('back')"><ArrowLeft :size="16" /></button>
      <div>
        <h1>Ncap 路径</h1>
        <p>指定 Npcap 库与驱动路径，检测有效性并测试抓包接口。</p>
      </div>
    </header>

    <div class="frost-form">
      <label class="frost-field">
        <span>Npcap 库与驱动路径</span>
        <div class="frost-input-row">
          <input
            v-model="npcapPath"
            class="frost-input"
            type="text"
            spellcheck="false"
            autocomplete="off"
            :class="{ 'is-invalid': configuredInvalid }"
            placeholder="留空自动检测：随包 third_party/npcap → 系统 System32"
          />
          <button class="frost-btn is-primary" type="button" :disabled="busy !== ''" @click="savePath">
            <LoaderCircle v-if="busy === 'save'" :size="15" class="frost-spin" />保存
          </button>
        </div>
        <div class="frost-input-row is-secondary">
          <button class="frost-btn" type="button" :disabled="busy !== ''" @click="choose('file')">
            <FolderOpen :size="15" />选择 DLL
          </button>
          <button class="frost-btn" type="button" :disabled="busy !== ''" @click="choose('folder')">
            <FolderOpen :size="15" />选择目录
          </button>
          <button class="frost-btn" type="button" :disabled="busy !== '' || !config?.configuredPath" @click="clearPath">
            <LoaderCircle v-if="busy === 'clear'" :size="15" class="frost-spin" />恢复自动检测
          </button>
        </div>
        <small>支持 wpcap.dll 完整路径、Npcap 安装目录（如 C:\Windows\System32\Npcap）或随包组件目录；留空表示自动检测。</small>
        <small v-if="savedHint" class="is-ok">{{ savedHint }}</small>
      </label>

      <div class="frost-inline">
        <button class="frost-btn" type="button" :disabled="busy !== ''" @click="detect">
          <LoaderCircle v-if="busy === 'detect'" :size="15" class="frost-spin" />
          <RefreshCw v-else :size="15" />检测
        </button>
        <button class="frost-btn" type="button" :disabled="busy !== ''" @click="testConnection">
          <LoaderCircle v-if="busy === 'test'" :size="15" class="frost-spin" />
          <ShieldAlert v-else :size="15" />测试连接
        </button>
        <button v-if="config?.installed === false" class="frost-btn" type="button" @click="installNpcap">安装随包 Npcap</button>
      </div>

      <p v-if="message" class="frost-note" :class="messageTone">
        <CheckCircle2 v-if="messageTone === 'is-ok'" :size="15" />
        <XCircle v-else-if="messageTone === 'is-error'" :size="15" />
        <ShieldAlert v-else :size="15" />
        <span>{{ message }}</span>
      </p>

      <dl class="frost-kv">
        <dt>当前来源</dt>
        <dd>{{ sourceLabel }}</dd>
        <dt>实际加载</dt>
        <dd>{{ config?.resolvedPath || "—" }}</dd>
        <dt>抓包接口</dt>
        <dd>{{ !config?.installed ? "尚未就绪" : adapterCount ? `${adapterCount} 个可用接口` : "组件已找到，点「测试连接」可枚举接口" }}</dd>
        <dt>配置文件</dt>
        <dd class="is-mono">{{ config?.configPath || "—" }}</dd>
      </dl>

      <p class="frost-section-title">快捷键与窗口</p>
      <div class="frost-subpanel">
        <div class="frost-inline">
          <label class="frost-check">
            <span>开始统计</span>
            <input class="frost-hotkey" :value="captureHotkey" readonly @keydown="recordHotkey($event, 'capture')" @click="($event.target as HTMLInputElement).select()" />
          </label>
          <label class="frost-check">
            <span>显示 / 关闭悬浮窗</span>
            <input class="frost-hotkey" :value="overlayHotkey" readonly @keydown="recordHotkey($event, 'overlay')" @click="($event.target as HTMLInputElement).select()" />
          </label>
          <button class="frost-btn" type="button" @click="saveHotkeys">保存快捷键</button>
        </div>
        <label class="frost-check">
          <input v-model="minimizeToTray" type="checkbox" @change="saveTray" />
          关闭窗口时最小化到系统托盘
        </label>
        <p v-if="hotkeyMessage" class="frost-note" :class="{ 'is-error': hotkeyError }">{{ hotkeyMessage }}</p>
        <p v-else-if="trayMessage" class="frost-note">{{ trayMessage }}</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.npcap-page { display: flex; flex-direction: column; min-height: 0; height: 100%; overflow: auto; }
.frost-form { padding-bottom: 24px; }
.frost-check span { color: var(--frost-ink-2); }
</style>
