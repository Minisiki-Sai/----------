from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / "frontend/src/views/LiveDpsView.vue"
COMPOSABLE = ROOT / "frontend/src/composables/liveDps/useLiveScoring.ts"


def take(text: str, start: str, end: str) -> tuple[str, str]:
    before, marker, tail = text.partition(start)
    if not marker:
        raise RuntimeError(f"start marker not found: {start!r}")
    body_tail, marker_end, after = tail.partition(end)
    if not marker_end:
        raise RuntimeError(f"end marker not found: {end!r}")
    return before + end + after, marker + body_tail


def indent(text: str, spaces: int = 2) -> str:
    prefix = " " * spaces
    return "\n".join(prefix + line if line else "" for line in text.splitlines())


def main() -> None:
    text = VIEW.read_text(encoding="utf-8")
    if "useLiveScoring" in text:
        raise SystemExit("LiveDpsView scoring split already applied")

    text, state = take(
        text,
        "const score = ref<ScorePayload | null>(null);",
        "const selectedProfileIndex = ref(0);",
    )
    text, rough_setting = take(
        text,
        'const LIVE_ROUGH_SCORE_STORAGE_KEY = "dragon-nest-v422-live-rough-score-enabled";',
        'const damageCurvePlayer = ref("team");',
    )
    text, rough_timers = take(
        text,
        "// Exact calibration is intentionally sparse during capture.",
        "const tabs = [",
    )
    text, presentation = take(
        text,
        "const scoreRows = computed(() => (score.value?.rows || []).map(normalizeScoreSummary));",
        "function isCaptureActiveState",
    )
    text, rough_logic = take(
        text,
        "function normalizeScoreSummary(row: ScoreSummary): ScoreSummary {",
        "function pairRows",
    )
    text, controller = take(
        text,
        "async function calculateScore(force = false, background = false, automaticLive = false) {",
        "watch(tab, value => {",
    )

    source_watch = """watch(selectedScoreSource, () => {
  if (score.value) {
    void loadScoreDetail();
    void ensureScoreGlyphEditor();
  }
});
"""
    boss_watch = """watch(selectedScoreBoss, () => {
  scoreDetailRequestSerial += 1;
  score.value = null;
  scoreDetail.value = null;
});
"""
    for block in (source_watch, boss_watch):
        if block not in text:
            raise RuntimeError("score watcher block not found")
        text = text.replace(block, "", 1)

    import_anchor = 'import { useScoreTimeline } from "../composables/liveDps/useScoreTimeline";\n'
    import_row = 'import { useLiveScoring } from "../composables/liveDps/useLiveScoring";\n'
    if import_anchor not in text:
        raise RuntimeError("score timeline import anchor not found")
    text = text.replace(import_anchor, import_anchor + import_row, 1)

    call_anchor = "const displayedTimelineEvents = computed(() => liveTimelineActive.value\n  ? liveTimelineEvents.value\n  : snapshot.value?.skill_cast_events || [],\n);\n"
    call = """const {
  score,
  scoreDetail,
  scoreLoading,
  liveRoughScoreRows,
  liveRoughScoreLoading,
  liveRoughBaseline,
  liveRoughScoreEnabled,
  selectedScoreSource,
  selectedScoreBoss,
  scoreDetailTab,
  scoreSkillFilters,
  selectedSupportDetailIndex,
  scoreRows,
  scoreDisplayName,
  selectedScore,
  selectedScoreBaseJob,
  selectedScoreGlyphRows,
  selectedScoreGlyphCatalog,
  scoreGlyphLoading,
  scoreGlyphEffects,
  scoreGlyphOptions,
  changeScoreGlyph,
  changeScoreGlyphOption,
  ensureScoreGlyphEditor,
  applyScoreGlyphs,
  resetScoreGlyphs,
  cloudBenchmark,
  scoreBenchmarkCards,
  benchmarkMean,
  benchmarkPosition,
  skillBenchmark,
  supportBenchmark,
  benchmarkMetric,
  benchmarkAverageText,
  benchmarkFormattedMean,
  survivalDetail,
  hpSurvivalShadow,
  hpDangerAvailable,
  hpDangerSimulated,
  survivalClassification,
  specialtyMetricCards,
  skillDetails,
  excludedSkillDetails,
  skillDamageCompletion,
  skillCountCompletion,
  supportDetails,
  selectedSupportDetail,
  selectedSupportSummary,
  improvementRows,
  improvementSummary,
  liveRoughWarmupRemaining,
  liveRoughCalibrationText,
  resetLiveRoughScore,
  cancelLiveScoringJobs,
  resetLiveScoreRequestClock,
  setLiveRoughScoreEnabled,
  updateLiveRoughScore,
  calculateScore,
  refreshLiveScoreIfDue,
  refreshLiveRoughScoreIfDue,
  loadScoreDetail,
} = useLiveScoring({
  snapshot,
  displaySource,
  capture,
  captureRunning,
  captureScoreReady,
  tab,
  loading,
  progress,
  progressLabel,
  error,
  targets,
  rawPlayers,
  players,
  bossId,
  bossTargetIds,
  numberValue,
  optionalNumber,
  textValue,
  coveragePercent,
  playerDisplayName,
});
"""
    if call_anchor not in text:
        raise RuntimeError("displayed timeline anchor not found")
    text = text.replace(call_anchor, call_anchor + call, 1)

    text = text.replace(
        "    resetLiveRoughScore();\n    lastLiveScoreRequestAt = 0;",
        "    resetLiveRoughScore();\n    resetLiveScoreRequestClock();",
    )
    text = text.replace(
        "  resetLiveRoughScore();\n  lastLiveScoreRequestAt = 0;",
        "  resetLiveRoughScore();\n  resetLiveScoreRequestClock();",
    )

    module = f'''import {{ computed, ref, shallowRef, watch }} from "vue";
import {{ simulatorApi }} from "../../services/api";
import {{ BackgroundJobCancelledError, cancelBackgroundJob, waitForBackgroundJob }} from "../../state/backgroundJobs";
import type {{
  CaptureStatus,
  ReplayPayload,
  ReplayPlayer,
  ScorePayload,
  ScoreSummary,
  SkillGlyphCatalogEntry,
  SkillGlyphSelection,
}} from "../../types/api";
import {{ compactNumber, percent }} from "../../utils/format";

type ReadonlyValue<T> = {{ readonly value: T }};
type MutableValue<T> = {{ value: T }};
type DataRow = Record<string, unknown>;

export function useLiveScoring(context: {{
  snapshot: MutableValue<ReplayPayload | null>;
  displaySource: ReadonlyValue<"capture" | "replay" | null>;
  capture: ReadonlyValue<CaptureStatus | null>;
  captureRunning: ReadonlyValue<boolean>;
  captureScoreReady: ReadonlyValue<boolean>;
  tab: ReadonlyValue<string>;
  loading: MutableValue<boolean>;
  progress: MutableValue<number>;
  progressLabel: MutableValue<string>;
  error: MutableValue<string>;
  targets: ReadonlyValue<DataRow[]>;
  rawPlayers: ReadonlyValue<ReplayPlayer[]>;
  players: ReadonlyValue<ReplayPlayer[]>;
  bossId: (row: DataRow, index?: number) => string;
  bossTargetIds: (row: DataRow) => string[];
  numberValue: (value: unknown, fallback?: number) => number;
  optionalNumber: (value: unknown) => number | null;
  textValue: (value: unknown, fallback?: string) => string;
  coveragePercent: (value: unknown) => number;
  playerDisplayName: (sourceId: unknown, job?: unknown, fallback?: unknown) => string;
}}) {{
  const {{
    snapshot, displaySource, capture, captureRunning, captureScoreReady, tab,
    loading, progress, progressLabel, error, targets, rawPlayers, players,
    bossId, bossTargetIds, numberValue, optionalNumber, textValue,
    coveragePercent,
    playerDisplayName,
  }} = context;

{indent(state.rstrip())}
{indent(rough_setting.rstrip())}
{indent(rough_timers.rstrip())}
{indent(presentation.rstrip())}
{indent(rough_logic.rstrip())}
  async function waitJob<T>(jobId: string): Promise<T> {{
    return waitForBackgroundJob<T>(
      jobId,
      "replay-score",
      "全队评分",
      job => {{
        progress.value = job.progress;
        progressLabel.value = job.state === "queued" ? "任务正在后台排队" : job.label;
      }},
    );
  }}
{indent(controller.rstrip())}
{indent(source_watch.rstrip())}
{indent(boss_watch.rstrip())}

  function resetLiveScoreRequestClock() {{
    lastLiveScoreRequestAt = 0;
  }}

  return {{
    score, scoreDetail, scoreLoading, liveRoughBaseline,
    liveRoughScoreRows, liveRoughScoreLoading, liveRoughScoreEnabled,
    selectedScoreSource, selectedScoreBoss, scoreDetailTab, scoreSkillFilters,
    selectedSupportDetailIndex, scoreRows, scoreDisplayName, selectedScore,
    selectedScoreBaseJob, selectedScoreGlyphRows, selectedScoreGlyphCatalog,
    scoreGlyphLoading, scoreGlyphEffects, scoreGlyphOptions, changeScoreGlyph,
    changeScoreGlyphOption, ensureScoreGlyphEditor, applyScoreGlyphs,
    resetScoreGlyphs, cloudBenchmark, scoreBenchmarkCards, benchmarkMean,
    benchmarkPosition, skillBenchmark, supportBenchmark, benchmarkMetric,
    benchmarkAverageText, benchmarkFormattedMean, survivalDetail,
    hpSurvivalShadow, hpDangerAvailable, hpDangerSimulated,
    survivalClassification, specialtyMetricCards, skillDetails,
    excludedSkillDetails, skillDamageCompletion, skillCountCompletion,
    supportDetails, selectedSupportDetail, selectedSupportSummary,
    improvementRows, improvementSummary, liveRoughWarmupRemaining,
    liveRoughCalibrationText, resetLiveRoughScore, cancelLiveScoringJobs,
    resetLiveScoreRequestClock, setLiveRoughScoreEnabled,
    updateLiveRoughScore, calculateScore, refreshLiveScoreIfDue,
    refreshLiveRoughScoreIfDue, loadScoreDetail,
  }};
}}
'''
    COMPOSABLE.parent.mkdir(parents=True, exist_ok=True)
    COMPOSABLE.write_text(module, encoding="utf-8", newline="\n")
    VIEW.write_text(text, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
