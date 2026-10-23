import type {
  BuffOverlayStatus,
  CaptureStatus,
  LiveTimelineStatus,
} from "../types/api";

const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:48710";

export interface NpcapConfigPayload {
  configuredPath: string;
  configuredValid: boolean;
  configPath: string;
  resolvedPath: string;
  source: "configured" | "portable" | "system" | "missing";
  installed: boolean;
  adapterCount: number;
  message: string;
}

export interface NpcapDetectPayload {
  candidates: Array<{ path: string; source: string }>;
  resolvedPath: string;
  installed: boolean;
  message: string;
}

export interface NpcapTestPayload {
  ok: boolean;
  message: string;
  resolvedPath: string;
  adapterCount: number;
  failedStage: "" | "load" | "permission" | "adapter" | "unknown";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response | null = null;
  let lastError: unknown = null;
  const attempts = path === "/api/health" ? 16 : 1;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      response = await fetch(`${API_BASE}${path}`, {
        ...init,
        headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
      });
      break;
    } catch (reason) {
      lastError = reason;
      if (attempt + 1 < attempts) await new Promise(resolve => setTimeout(resolve, 250));
    }
  }
  if (!response) {
    if (lastError instanceof TypeError) {
      throw new Error("本机解析核心连接已断开，请稍候重试；若持续失败请重新启动程序。");
    }
    throw lastError instanceof Error ? lastError : new Error("本机解析核心尚未启动");
  }
  if (!response.ok) {
    let detail = await response.text();
    try {
      const payload = JSON.parse(detail) as { message?: string };
      detail = payload.message || detail;
    } catch { /* plain text error */ }
    throw new Error(detail || `请求失败：${response.status}`);
  }
  return (await response.json()) as T;
}

const post = <T>(path: string, body: unknown) => request<T>(path, {
  method: "POST",
  body: JSON.stringify(body),
});

export const simulatorApi = {
  health: () => request<{ ok: boolean; version: string }>("/api/health"),
  captureStatus: (includeSnapshot = false) =>
    request<CaptureStatus>(`/api/capture/status?includeSnapshot=${includeSnapshot ? "1" : "0"}`),
  startCapture: (duration = 0) => post<CaptureStatus>("/api/capture/start", { duration }),
  stopCapture: () => post<CaptureStatus>("/api/capture/stop", {}),
  captureToggles: () =>
    request<{ toggles: Record<string, boolean>; labels: Record<string, string> }>("/api/capture/toggles"),
  setCaptureToggles: (toggles: Record<string, boolean>) =>
    post<{ toggles: Record<string, boolean>; labels: Record<string, string> }>("/api/capture/toggles", { toggles }),
  captureVoiceEvents: () =>
    request<Pick<LiveTimelineStatus, "state" | "sessionId" | "elapsedSeconds" | "voiceEvents"> & { clockRunning?: boolean }>(
      "/api/capture/voice-events",
    ),
  buffOverlayStatus: (includeCatalog = false) =>
    request<BuffOverlayStatus>(`/api/capture/buff-overlay?includeCatalog=${includeCatalog ? "1" : "0"}`),
  waitBuffOverlayChange: (afterRevision = 0, timeoutMs = 15_000) =>
    request<import("../types/api").BuffOverlayChangeStatus>(
      `/api/capture/buff-overlay/wait?afterRevision=${Math.max(0, Math.floor(afterRevision))}&timeoutMs=${Math.max(0, Math.min(25_000, Math.floor(timeoutMs)))}`,
    ),
  npcapConfig: () => request<NpcapConfigPayload>("/api/npcap/config"),
  saveNpcapConfig: (path: string) => post<NpcapConfigPayload>("/api/npcap/config", { path }),
  detectNpcap: () => post<NpcapDetectPayload>("/api/npcap/detect", {}),
  testNpcap: () => post<NpcapTestPayload>("/api/npcap/test", {}),
};
