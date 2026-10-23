from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / "frontend/src/views/LiveDpsView.vue"
COMPOSABLE = ROOT / "frontend/src/composables/liveDps/useScoreTimeline.ts"


def indent(text: str, spaces: int = 2) -> str:
    prefix = " " * spaces
    return "\n".join(prefix + line if line else "" for line in text.splitlines())


def main() -> None:
    text = VIEW.read_text(encoding="utf-8")
    if "useScoreTimeline" in text:
        raise SystemExit("LiveDpsView score timeline split already applied")
    start = "const curveNode = computed<NativeNode | null>(() => snapshot.value ? {"
    end = "async function chooseFile()"
    before, marker, tail = text.partition(start)
    if not marker:
        raise RuntimeError("score timeline start marker not found")
    body_tail, marker_end, after = tail.partition(end)
    if not marker_end:
        raise RuntimeError("score timeline end marker not found")
    body = marker + body_tail

    import_anchor = 'import { useBossScope } from "../composables/liveDps/useBossScope";\n'
    import_row = 'import { useScoreTimeline } from "../composables/liveDps/useScoreTimeline";\n'
    if import_anchor not in before:
        raise RuntimeError("Boss scope import anchor not found")
    before = before.replace(import_anchor, import_anchor + import_row, 1)
    call = """const {
  curveNode,
  timelineNode,
  scoreTimelineDuration,
  scoreTimelineNode,
  scoreSkillOptions,
  scoreSupportTimelineNode,
} = useScoreTimeline({
  snapshot,
  players,
  damageCurvePlayer,
  bossPhases,
  selectedScoreBoss,
  targets,
  selectedScore,
  scoreDetail,
  scoreSkillFilters,
  supportDetails,
  selectedSupportDetail,
  hpCurveSeries,
  bossId,
  bossTargetIds,
  bossPhaseRows,
  numberValue,
  textValue,
  coveragePercent,
  playerDisplayName,
});

"""
    VIEW.write_text(before + call + end + after, encoding="utf-8", newline="\n")

    module = f'''import {{ computed, watch }} from "vue";
import type {{ NativeNode, ReplayPayload, ReplayPlayer, ScoreSummary }} from "../../types/api";
import {{ compactNumber }} from "../../utils/format";

type ReadonlyValue<T> = {{ readonly value: T }};
type MutableValue<T> = {{ value: T }};
type DataRow = Record<string, unknown>;

export function useScoreTimeline(context: {{
  snapshot: ReadonlyValue<ReplayPayload | null>;
  players: ReadonlyValue<ReplayPlayer[]>;
  damageCurvePlayer: ReadonlyValue<string>;
  bossPhases: ReadonlyValue<Array<{{ name: string; start: number; end: number }}> >;
  selectedScoreBoss: ReadonlyValue<string>;
  targets: ReadonlyValue<DataRow[]>;
  selectedScore: ReadonlyValue<ScoreSummary | null>;
  scoreDetail: ReadonlyValue<DataRow | null>;
  scoreSkillFilters: MutableValue<string[]>;
  supportDetails: ReadonlyValue<DataRow[]>;
  selectedSupportDetail: ReadonlyValue<DataRow | null>;
  hpCurveSeries: (curves: DataRow[], tracks: DataRow[]) => Record<string, Array<[number, number]>>;
  bossId: (row: DataRow, index?: number) => string;
  bossTargetIds: (row: DataRow) => string[];
  bossPhaseRows: (rows: DataRow[]) => Array<{{ name: string; start: number; end: number }}>;
  numberValue: (value: unknown, fallback?: number) => number;
  textValue: (value: unknown, fallback?: string) => string;
  coveragePercent: (value: unknown) => number;
  playerDisplayName: (sourceId: unknown, job?: unknown, fallback?: unknown) => string;
}}) {{
  const {{
    snapshot, players, damageCurvePlayer, bossPhases, selectedScoreBoss,
    targets, selectedScore, scoreDetail, scoreSkillFilters, supportDetails,
    selectedSupportDetail, hpCurveSeries, bossId, bossTargetIds,
    bossPhaseRows, numberValue, textValue, coveragePercent, playerDisplayName,
  }} = context;

{indent(body.rstrip())}

  return {{
    curveNode,
    timelineNode,
    scoreTimelineDuration,
    scoreTimelineNode,
    scoreSkillOptions,
    scoreSupportTimelineNode,
  }};
}}
'''
    COMPOSABLE.parent.mkdir(parents=True, exist_ok=True)
    COMPOSABLE.write_text(module, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
