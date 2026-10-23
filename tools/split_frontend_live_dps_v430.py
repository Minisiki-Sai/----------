from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / "frontend/src/views/LiveDpsView.vue"
COMPOSABLE = ROOT / "frontend/src/composables/liveDps/useBossScope.ts"


def between(text: str, start: str, end: str) -> tuple[str, str, str]:
    left, found, tail = text.partition(start)
    if not found:
        raise RuntimeError(f"start marker not found: {start!r}")
    body, found_end, right = tail.partition(end)
    if not found_end:
        raise RuntimeError(f"end marker not found: {end!r}")
    return left, start + body, end + right


def indent(text: str, spaces: int = 2) -> str:
    prefix = " " * spaces
    return "\n".join(prefix + line if line else "" for line in text.splitlines())


def main() -> None:
    text = VIEW.read_text(encoding="utf-8")
    if "useBossScope" in text:
        raise SystemExit("LiveDpsView boss scope split already applied")

    state = """const bossTab = ref("composition");
const selectedBossIds = ref<string[]>([]);
const selectedBossPlayer = ref(0);
const bossCursor = ref(0);
const buffMode = ref("overview");
const buffTargetFilter = ref("");
const buffSourceFilter = ref("");
const buffKindFilter = ref("");
"""
    if state not in text:
        raise RuntimeError("boss state block not found")
    text = text.replace(state, "", 1)

    boss_phase = "const bossPhases = computed(() => bossPhaseRows(targets.value));\n"
    if boss_phase not in text:
        raise RuntimeError("boss phase declaration not found")
    text = text.replace(boss_phase, "", 1)

    left, boss_helpers, right = between(
        text,
        "const bossTabs = [\n",
        "function normalizeScoreSummary",
    )
    text = left + "function normalizeScoreSummary" + right[len("function normalizeScoreSummary") :]

    left, boss_logic, right = between(
        text,
        "function textValue(value: unknown, fallback = \"\")",
        "function pairRows",
    )
    text = left + "function pairRows" + right[len("function pairRows") :]

    import_anchor = 'import { compactNumber, percent } from "../utils/format";\n'
    import_row = 'import { useBossScope } from "../composables/liveDps/useBossScope";\n'
    if import_anchor not in text:
        raise RuntimeError("format import anchor not found")
    text = text.replace(import_anchor, import_anchor + import_row, 1)

    call_anchor = "const buffs = computed(() => snapshot.value?.player_buffs || []);\n"
    destructure = """const {
  bossTab,
  selectedBossIds,
  selectedBossPlayer,
  bossCursor,
  buffMode,
  buffTargetFilter,
  buffSourceFilter,
  buffKindFilter,
  bossTabs,
  numberValue,
  optionalNumber,
  textValue,
  coveragePercent,
  hpCurvePoints,
  hpCurvePointsForTracks,
  hpCurveSegments,
  hpCurvePointsForCurves,
  hpCurveSeries,
  hpTrackBounds,
  targetId,
  bossId,
  bossTargetIds,
  bossPhaseRows,
  eventSecond,
  bossPhases,
  selectedTargets,
  selectedTargetIds,
  bossRanges,
  inSelectedBossTime,
  isSelectedTarget,
  combatEvents,
  bossCombatEvents,
  bossPlayerRows,
  bossPlayer,
  eventSeries,
  selectedHpTracks,
  selectedHpCurves,
  bossCurveNode,
  selectedBossCasts,
  bossTimelineNode,
  selectedBossActions,
  bossActionNode,
  selectedEffects,
  selectedEffectEvents,
  selectedEffectViews,
  selectedEffectSeries,
  selectedBuffs,
  selectedBuffEvents,
  buffTargets,
  buffSources,
  buffKinds,
  filteredBuffs,
  filteredBuffEvents,
  currentBossState,
  toggleBoss,
  handleBossCurveAction,
} = useBossScope({ snapshot, targets, effects, buffs, players, playerDisplayName });
"""
    if call_anchor not in text:
        raise RuntimeError("buff computed anchor not found")
    text = text.replace(call_anchor, call_anchor + destructure, 1)

    returns = """  return {
    bossTab, selectedBossIds, selectedBossPlayer, bossCursor,
    buffMode, buffTargetFilter, buffSourceFilter, buffKindFilter,
    bossTabs, numberValue, optionalNumber, textValue, coveragePercent,
    hpCurvePoints, hpCurvePointsForTracks, hpCurveSegments,
    hpCurvePointsForCurves, hpCurveSeries, hpTrackBounds,
    targetId, bossId, bossTargetIds, bossPhaseRows, eventSecond, bossPhases,
    selectedTargets, selectedTargetIds, bossRanges, inSelectedBossTime,
    isSelectedTarget, combatEvents, bossCombatEvents, bossPlayerRows,
    bossPlayer, eventSeries, selectedHpTracks, selectedHpCurves,
    bossCurveNode, selectedBossCasts, bossTimelineNode, selectedBossActions,
    bossActionNode, selectedEffects, selectedEffectEvents, selectedEffectViews,
    selectedEffectSeries, selectedBuffs, selectedBuffEvents, buffTargets,
    buffSources, buffKinds, filteredBuffs, filteredBuffEvents,
    currentBossState, toggleBoss, handleBossCurveAction,
  };
"""
    module = f'''import {{ computed, ref, watch }} from "vue";
import type {{ NativeNode, ReplayPayload, ReplayPlayer }} from "../../types/api";
import {{ compactNumber }} from "../../utils/format";

type ReadonlyValue<T> = {{ readonly value: T }};
type DataRow = Record<string, unknown>;

export function useBossScope(context: {{
  snapshot: ReadonlyValue<ReplayPayload | null>;
  targets: ReadonlyValue<DataRow[]>;
  effects: ReadonlyValue<DataRow[]>;
  buffs: ReadonlyValue<DataRow[]>;
  players: ReadonlyValue<ReplayPlayer[]>;
  playerDisplayName: (sourceId: unknown, job?: unknown, fallback?: unknown) => string;
}}) {{
  const {{ snapshot, targets, effects, buffs, players, playerDisplayName }} = context;
{indent(state.rstrip())}
{indent(boss_helpers.rstrip())}
  const bossPhases = computed(() => bossPhaseRows(targets.value));
{indent(boss_logic.rstrip())}
{returns.rstrip()}
}}
'''
    COMPOSABLE.parent.mkdir(parents=True, exist_ok=True)
    COMPOSABLE.write_text(module, encoding="utf-8", newline="\n")
    VIEW.write_text(text, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
