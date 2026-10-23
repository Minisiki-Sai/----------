from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "frontend/src/composables/overlay/useBuffOverlay.ts"
TARGET = ROOT / "frontend/src/composables/overlay/useBuffOverlayPresentation.ts"


def indent(text: str, spaces: int = 2) -> str:
    prefix = " " * spaces
    return "\n".join(prefix + line if line else "" for line in text.splitlines())


def main() -> None:
    text = SOURCE.read_text(encoding="utf-8")
    if "useBuffOverlayPresentation" in text:
        raise SystemExit("Buff overlay presentation split already applied")
    start = "  const windowLabel = computed(() => OVERLAY_WINDOW_LABELS[props.view]);"
    end = "  function isTauri(): boolean"
    before, marker, tail = text.partition(start)
    if not marker:
        raise RuntimeError("presentation start not found")
    body_tail, marker_end, after = tail.partition(end)
    if not marker_end:
        raise RuntimeError("presentation end not found")
    body = marker[2:] + body_tail
    body = "\n".join(line[2:] if line.startswith("  ") else line for line in body.splitlines())
    load_settings = '''function loadSettings() {
  const raw = localStorage.getItem(BUFF_OVERLAY_STORAGE_KEY) || "";
  if (raw === settingsSignature) return;
  settingsSignature = raw;
  settings.value = readBuffOverlaySettings();
}
'''
    if load_settings not in body:
        raise RuntimeError("loadSettings block not found")
    body = body.replace(load_settings, "", 1)

    import_anchor = 'import { simulatorApi } from "../../services/api";\n'
    import_row = 'import { useBuffOverlayPresentation } from "./useBuffOverlayPresentation";\n'
    if import_anchor not in before:
        raise RuntimeError("simulator API import anchor not found")
    before = before.replace(import_anchor, import_anchor + import_row, 1)
    call = """  const {
    windowLabel, isStatusView, isCurveView, isAlertView, isClearedView,
    isLowHealthView, isEventAlertView, layoutTitle, effectCurveLabels,
    pollLeaderView, textValue, numberValue, captureActive,
    currentSeconds, allowedModes, categoryEnabled, formatRemainingSeconds,
    rows, buffRows, debuffRows, statusRows, dpsTargets, dpsSeries, curveKeys,
    curvePalette, effectPlot, effectTimeAxisTicks, effectPercentMaximum,
    effectFreezeMaximum, hasFreezeCurve, effectHoverPoint, effectHoverX,
    effectHoverLeft, updateEffectHover, effectCurveValueText,
    currentEffectCurveValue, curveLine, dpsPlot, dpsTimeAxisTicks,
    maximumDps, dpsLine, compactDamage, cooldownRows, liveAlerts,
    compactListStyle, overlayLayout, statusListStyle, shellStyle, rowStyle,
    rowWidth, remainingText,
  } = useBuffOverlayPresentation({
    props,
    payload,
    settings,
    fetchedAt,
    clock,
    baseSeconds,
    effectHistory,
    effectHoverIndex,
    layoutMode,
    observedFullDurationTeamBuffs,
    statusPanelGap: STATUS_PANEL_GAP,
  });
  function loadSettings() {{
    const raw = localStorage.getItem(BUFF_OVERLAY_STORAGE_KEY) || "";
    if (raw === settingsSignature) return;
    settingsSignature = raw;
    settings.value = readBuffOverlaySettings();
  }}
"""
    SOURCE.write_text(before + call + "  " + end + after, encoding="utf-8", newline="\n")

    target = f'''import {{ computed }} from "vue";
import type {{ BuffOverlaySettings, BuffOverlayStatus }} from "../../types/api";
import {{ EFFECT_CURVE_OPTIONS, OVERLAY_WINDOW_LABELS, type OverlayWindowKind }} from "../../utils/buffOverlaySettings";

type OverlayTone = keyof BuffOverlaySettings["toneColors"];
type OverlayRow = {{ key: string; colorKey: string; name: string; sourceJob: string; detail: string; remaining: number; duration: number; permanent: boolean; tone: OverlayTone; groupOrder: number; sortOrder: number }};
export type CurvePoint = {{ second: number; values: Record<string, number> }};
type TimeAxisTick = {{ x: number; label: string; anchor: "start" | "middle" | "end" }};
type AlertRow = {{ key: string; label: string; detail: string; tone: "warning" | "danger" | "info"; expiresAt: number }};
type ReadonlyValue<T> = {{ readonly value: T }};
type MutableValue<T> = {{ value: T }};

export function useBuffOverlayPresentation(context: {{
  props: {{ view: OverlayWindowKind }};
  payload: ReadonlyValue<BuffOverlayStatus | null>;
  settings: MutableValue<BuffOverlaySettings>;
  fetchedAt: ReadonlyValue<number>;
  clock: ReadonlyValue<number>;
  baseSeconds: ReadonlyValue<number>;
  effectHistory: MutableValue<CurvePoint[]>;
  effectHoverIndex: MutableValue<number | null>;
  layoutMode: ReadonlyValue<boolean>;
  observedFullDurationTeamBuffs: Set<string>;
  statusPanelGap: number;
}}) {{
  const {{
    props, payload, settings, fetchedAt, clock, baseSeconds, effectHistory,
    effectHoverIndex, layoutMode, observedFullDurationTeamBuffs,
    statusPanelGap: STATUS_PANEL_GAP,
  }} = context;

{indent(body.rstrip())}

  return {{
    windowLabel, isStatusView, isCurveView, isAlertView, isClearedView,
    isLowHealthView, isEventAlertView, layoutTitle, effectCurveLabels,
    pollLeaderView, textValue, numberValue, captureActive,
    currentSeconds, targetId, target, buffSourceJob, allowedModes,
    categoryEnabled, formatValue, formatRemainingSeconds, rows, buffRows,
    debuffRows, statusRows, dpsTargets, dpsSeries, curveKeys, curvePalette,
    effectPlot, effectTimeAxisTicks, effectPercentMaximum,
    effectFreezeMaximum, hasFreezeCurve, effectHoverPoint, effectHoverX,
    effectHoverLeft, updateEffectHover, effectCurveValueText,
    currentEffectCurveValue, curveLine, dpsPlot, dpsTimeAxisTicks,
    maximumDps, dpsLine, compactDamage, cooldownRows, liveAlerts,
    compactListStyle, compactPanelHeight, overlayLayout, statusListStyle,
    shellStyle, rowStyle, rowWidth, remainingText,
  }};
}}
'''
    TARGET.write_text(target, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
