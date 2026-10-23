from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / "frontend/src/components/BuffOverlay.vue"
COMPOSABLE = ROOT / "frontend/src/composables/overlay/useBuffOverlay.ts"


def indent(text: str, spaces: int = 2) -> str:
    prefix = " " * spaces
    return "\n".join(prefix + line if line else "" for line in text.splitlines())


def main() -> None:
    text = VIEW.read_text(encoding="utf-8")
    if "useBuffOverlay" in text:
        raise SystemExit("BuffOverlay split already applied")
    script, close, rest = text.partition("</script>")
    if not close:
        raise RuntimeError("script end not found")
    type_start = "type OverlayTone"
    props_row = 'const props = withDefaults(defineProps<{ view?: OverlayWindowKind }>(), { view: "status" });'
    prefix, marker, body_tail = script.partition(type_start)
    if not marker:
        raise RuntimeError("overlay type block not found")
    type_and_props, props_marker, runtime = (marker + body_tail).partition(props_row)
    if not props_marker:
        raise RuntimeError("overlay props row not found")
    types = type_and_props.rstrip()
    runtime = runtime.lstrip("\r\n")

    returns = """  return {
    payload, settings, captureNotice, effectHistory, effectHoverIndex,
    clearedAlert, lowHealthAlert, layoutMode, overlayShell, windowLabel,
    isStatusView, isCurveView, isAlertView, isClearedView, isLowHealthView,
    isEventAlertView, layoutTitle, effectCurveLabels, captureActive,
    currentSeconds, rows, buffRows, debuffRows, statusRows, dpsTargets,
    dpsSeries, curveKeys, curvePalette, effectPlot, effectTimeAxisTicks,
    effectPercentMaximum, effectFreezeMaximum, hasFreezeCurve,
    effectHoverPoint, effectHoverX, effectHoverLeft, updateEffectHover,
    effectCurveValueText, currentEffectCurveValue, curveLine, dpsPlot,
    dpsTimeAxisTicks, maximumDps, dpsLine, compactDamage, cooldownRows,
    liveAlerts, compactListStyle, overlayLayout, statusListStyle, shellStyle,
    rowStyle, rowWidth, remainingText, formatRemainingSeconds, beginDrag,
    finishLayoutMode,
  };
"""
    module = f'''import {{ computed, nextTick, onMounted, onUnmounted, ref, watch }} from "vue";
import {{ invoke }} from "@tauri-apps/api/core";
import {{ emitTo, listen, type UnlistenFn }} from "@tauri-apps/api/event";
import {{ getCurrentWindow }} from "@tauri-apps/api/window";
import {{ simulatorApi }} from "../../services/api";
import type {{ BuffOverlaySettings, BuffOverlayStatus }} from "../../types/api";
import {{
  BUFF_OVERLAY_STORAGE_KEY,
  ALL_OVERLAY_WINDOW_LABELS,
  EFFECT_CURVE_OPTIONS,
  OVERLAY_WINDOW_LABELS,
  TEAMMATE_INFO_WINDOW_LABEL,
  enabledOverlayWindowLabels,
  normalizeBuffOverlaySettings,
  readBuffOverlaySettings,
  type OverlayWindowKind,
}} from "../../utils/buffOverlaySettings";

{types}

export type BuffOverlayProps = {{ view: OverlayWindowKind }};

export function useBuffOverlay(props: BuffOverlayProps) {{
{indent(runtime.rstrip())}
{returns.rstrip()}
}}
'''
    COMPOSABLE.parent.mkdir(parents=True, exist_ok=True)
    COMPOSABLE.write_text(module, encoding="utf-8", newline="\n")

    view_script = '''<script setup lang="ts">
import { Activity, BellRing, CheckCircle2, GripVertical, HeartPulse, Move, TimerReset, TrendingUp } from "@lucide/vue";
import { useBuffOverlay } from "../composables/overlay/useBuffOverlay";
import type { OverlayWindowKind } from "../utils/buffOverlaySettings";

const props = withDefaults(defineProps<{ view?: OverlayWindowKind }>(), { view: "status" });
const {
  settings, captureNotice, effectHoverIndex, clearedAlert, lowHealthAlert,
  layoutMode, overlayShell, isStatusView, isCurveView, isAlertView,
  isClearedView, isLowHealthView, isEventAlertView, layoutTitle,
  effectCurveLabels, captureActive, statusRows, dpsSeries, curveKeys,
  curvePalette, effectPlot, effectTimeAxisTicks, effectPercentMaximum,
  effectFreezeMaximum, hasFreezeCurve, effectHoverPoint, effectHoverX,
  effectHoverLeft, updateEffectHover, effectCurveValueText,
  currentEffectCurveValue, curveLine, dpsPlot, dpsTimeAxisTicks, maximumDps,
  dpsLine, compactDamage, cooldownRows, liveAlerts, compactListStyle,
  statusListStyle, shellStyle, rowStyle, rowWidth, remainingText,
  formatRemainingSeconds, beginDrag, finishLayoutMode,
} = useBuffOverlay({ view: props.view });
</script>'''
    VIEW.write_text(view_script + rest, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
