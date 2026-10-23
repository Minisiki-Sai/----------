from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / "frontend/src/views/LiveDpsView.vue"
COMPOSABLE = ROOT / "frontend/src/composables/liveDps/useLiveCapture.ts"


def remove_exact(text: str, block: str) -> str:
    if block not in text:
        raise RuntimeError(f"block not found: {block[:60]!r}")
    return text.replace(block, "", 1)


def indent(text: str, spaces: int = 2) -> str:
    prefix = " " * spaces
    return "\n".join(prefix + line if line else "" for line in text.splitlines())


def main() -> None:
    text = VIEW.read_text(encoding="utf-8")
    if "useLiveCapture" in text:
        raise SystemExit("LiveDpsView capture split already applied")

    private_timeline = """let liveTimelineSequence = 0;
let liveTimelineSession = "";
let liveTimelineRequestRunning = false;
let liveTimelineGeneration = 0;
"""
    controls = """const replayInput = ref<HTMLInputElement | null>(null);
const storedAutoCaptureSetting = localStorage.getItem(
  "dragon-nest-auto-capture-enabled",
);
const autoCaptureEnabled = ref(storedAutoCaptureSetting !== "0");
if (storedAutoCaptureSetting == null) {
  localStorage.setItem("dragon-nest-auto-capture-enabled", "1");
}
"""
    overlay_state = """const overlayEnabled = ref(localStorage.getItem("dragon-nest-v420-buff-overlay-enabled") === "1");
const overlaySettingsOpen = ref(false);
const overlayOptions = ref<BuffOverlayStatus["buffOptions"]>([]);
const overlayPlayers = ref<BuffOverlayStatus["players"]>([]);
const overlaySettings = ref<BuffOverlaySettings>(readBuffOverlaySettings());
"""
    private_capture = """let captureTimer: number | null = null;
let liveTimelineTimer: number | null = null;
let captureRefreshInFlight = false;
let captureRefreshPending = false;
let captureRequestGeneration = 0;
const CAPTURE_NOTICE_MS = 900;
"""
    for block in (private_timeline, controls, overlay_state, private_capture):
        text = remove_exact(text, block)

    start = "async function chooseFile()"
    lifecycle = "watch(tab, value => {"
    script_end = "</script>"
    before, marker, tail = text.partition(start)
    if not marker:
        raise RuntimeError("capture controller start not found")
    functions_tail, lifecycle_marker, lifecycle_tail = tail.partition(lifecycle)
    if not lifecycle_marker:
        raise RuntimeError("capture lifecycle start not found")
    lifecycle_body, script_marker, after_script = lifecycle_tail.partition(script_end)
    if not script_marker:
        raise RuntimeError("script end not found")
    functions = marker + functions_tail
    lifecycle_code = lifecycle_marker + lifecycle_body

    import_anchor = 'import { useReplayProfiles } from "../composables/liveDps/useReplayProfiles";\n'
    import_row = 'import { useLiveCapture } from "../composables/liveDps/useLiveCapture";\n'
    if import_anchor not in before:
        raise RuntimeError("profile import anchor not found")
    before = before.replace(import_anchor, import_anchor + import_row, 1)

    call = """const {
  replayInput,
  autoCaptureEnabled,
  overlayEnabled,
  overlaySettingsOpen,
  overlayOptions,
  overlayPlayers,
  overlaySettings,
  chooseFile,
  uploadFile,
  refreshCapture,
  refreshLiveTimeline,
  startCapture,
  setAutoCapture,
  setOverlayVisible,
  toggleOverlay,
  adjustOverlayLayout,
  openOverlaySettings,
  saveOverlaySettings,
  installNpcap,
  openHistory,
  exportSummary,
  copySummary,
  stopCapture,
  clearCapture,
  load,
} = useLiveCapture({
  snapshot,
  tab,
  selectedPlayer,
  loading,
  progress,
  progressLabel,
  error,
  capture,
  displaySource,
  liveTimelineEvents,
  players,
  totalDps,
  captureRunning,
  captureStopping,
  captureBusy,
  captureScoreReady,
  score,
  scoreDetail,
  scoreLoading,
  liveRoughBaseline,
  liveRoughScoreEnabled,
  selectedScoreSource,
  selectedScoreBoss,
  resetLiveRoughScore,
  resetLiveScoreRequestClock,
  updateLiveRoughScore,
  calculateScore,
  refreshLiveScoreIfDue,
  refreshLiveRoughScoreIfDue,
  cancelLiveScoringJobs,
  isCaptureActiveState,
});

"""
    VIEW.write_text(before + call + script_end + after_script, encoding="utf-8", newline="\n")

    module = f'''import {{ onMounted, onUnmounted, ref, watch }} from "vue";
import {{ invoke }} from "@tauri-apps/api/core";
import {{ emitTo }} from "@tauri-apps/api/event";
import {{ simulatorApi }} from "../../services/api";
import {{ waitForBackgroundJob }} from "../../state/backgroundJobs";
import type {{
  BuffOverlaySettings,
  BuffOverlayStatus,
  CaptureStatus,
  ReplayPayload,
  ReplayPlayer,
  ScorePayload,
}} from "../../types/api";
import {{ compactNumber, percent }} from "../../utils/format";
import {{
  BUFF_OVERLAY_STORAGE_KEY,
  ALL_OVERLAY_WINDOW_LABELS,
  OVERLAY_WINDOW_LABELS,
  TEAMMATE_INFO_WINDOW_LABEL,
  enabledOverlayWindowLabels,
  readBuffOverlaySettings,
}} from "../../utils/buffOverlaySettings";

type ReadonlyValue<T> = {{ readonly value: T }};
type MutableValue<T> = {{ value: T }};
type DataRow = Record<string, unknown>;
type LiveBaseline = {{ sourceHash: string }};

export function useLiveCapture(context: {{
  snapshot: MutableValue<ReplayPayload | null>;
  tab: MutableValue<string>;
  selectedPlayer: MutableValue<number>;
  loading: MutableValue<boolean>;
  progress: MutableValue<number>;
  progressLabel: MutableValue<string>;
  error: MutableValue<string>;
  capture: MutableValue<CaptureStatus | null>;
  displaySource: MutableValue<"capture" | "replay" | null>;
  liveTimelineEvents: MutableValue<DataRow[]>;
  players: ReadonlyValue<ReplayPlayer[]>;
  totalDps: ReadonlyValue<number>;
  captureRunning: ReadonlyValue<boolean>;
  captureStopping: ReadonlyValue<boolean>;
  captureBusy: ReadonlyValue<boolean>;
  captureScoreReady: ReadonlyValue<boolean>;
  score: MutableValue<ScorePayload | null>;
  scoreDetail: MutableValue<DataRow | null>;
  scoreLoading: ReadonlyValue<boolean>;
  liveRoughBaseline: ReadonlyValue<LiveBaseline | null>;
  liveRoughScoreEnabled: ReadonlyValue<boolean>;
  selectedScoreSource: MutableValue<string>;
  selectedScoreBoss: MutableValue<string>;
  resetLiveRoughScore: () => Promise<void>;
  resetLiveScoreRequestClock: () => void;
  updateLiveRoughScore: () => void;
  calculateScore: (force?: boolean, background?: boolean, automaticLive?: boolean) => Promise<void>;
  refreshLiveScoreIfDue: () => Promise<void>;
  refreshLiveRoughScoreIfDue: () => Promise<void>;
  cancelLiveScoringJobs: () => Promise<void>;
  isCaptureActiveState: (state: unknown) => boolean;
}}) {{
  const {{
    snapshot, tab, selectedPlayer, loading, progress, progressLabel, error,
    capture, displaySource, liveTimelineEvents, players, totalDps,
    captureRunning, captureStopping, captureBusy, captureScoreReady, score, scoreDetail,
    scoreLoading, liveRoughBaseline, liveRoughScoreEnabled,
    selectedScoreSource, selectedScoreBoss, resetLiveRoughScore,
    resetLiveScoreRequestClock, updateLiveRoughScore, calculateScore,
    refreshLiveScoreIfDue, refreshLiveRoughScoreIfDue,
    cancelLiveScoringJobs, isCaptureActiveState,
  }} = context;

{indent(private_timeline.rstrip())}
{indent(controls.rstrip())}
{indent(overlay_state.rstrip())}
{indent(private_capture.rstrip())}
{indent(functions.rstrip())}
{indent(lifecycle_code.rstrip().replace("watch(tab, value =>", "watch(() => tab.value, value =>"))}

  return {{
    replayInput, autoCaptureEnabled, overlayEnabled, overlaySettingsOpen,
    overlayOptions, overlayPlayers, overlaySettings, chooseFile, uploadFile,
    refreshCapture, refreshLiveTimeline, startCapture, setAutoCapture,
    setOverlayVisible, toggleOverlay, adjustOverlayLayout, openOverlaySettings,
    saveOverlaySettings, installNpcap, openHistory, exportSummary, copySummary,
    stopCapture, clearCapture, load,
  }};
}}
'''
    COMPOSABLE.parent.mkdir(parents=True, exist_ok=True)
    COMPOSABLE.write_text(module, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
