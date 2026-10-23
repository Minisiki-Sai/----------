from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / "frontend/src/views/LiveDpsView.vue"
COMPOSABLE = ROOT / "frontend/src/composables/liveDps/useReplayProfiles.ts"


def indent(text: str, spaces: int = 2) -> str:
    prefix = " " * spaces
    return "\n".join(prefix + line if line else "" for line in text.splitlines())


def main() -> None:
    text = VIEW.read_text(encoding="utf-8")
    if "useReplayProfiles" in text:
        raise SystemExit("LiveDpsView profile split already applied")
    state = 'const selectedProfileIndex = ref(0);\nconst profileTab = ref("总面板");\n'
    if state not in text:
        raise RuntimeError("profile state not found")
    text = text.replace(state, "", 1)

    start = "const profiles = computed<Record<string, unknown>[]>(() => {"
    end = "const {\n  curveNode,"
    before, marker, tail = text.partition(start)
    if not marker:
        raise RuntimeError("profile block start not found")
    block_tail, marker_end, after = tail.partition(end)
    if not marker_end:
        raise RuntimeError("score timeline call anchor not found")
    block = marker + block_tail

    import_anchor = 'import { useLiveScoring } from "../composables/liveDps/useLiveScoring";\n'
    import_row = 'import { useReplayProfiles } from "../composables/liveDps/useReplayProfiles";\n'
    if import_anchor not in before:
        raise RuntimeError("scoring import anchor not found")
    before = before.replace(import_anchor, import_anchor + import_row, 1)
    call = """const {
  selectedProfileIndex,
  profileTab,
  profiles,
  profilePanels,
  selectedProfile,
  profileTabs,
  selectedProfilePanel,
  panelValue,
  pairRows,
  selectedCardEstimate,
  selectedCardEstimateRows,
  selectedAccountDefaults,
  selectedAccountMastery,
  cardTotalValue,
  cardMasterySource,
  selectedCardEstimateStatus,
  profileRows,
} = useReplayProfiles({ snapshot, displaySource, capture, textValue, numberValue });

"""
    VIEW.write_text(before + call + end + after, encoding="utf-8", newline="\n")

    module = f'''import {{ computed, ref }} from "vue";
import type {{ CaptureStatus, ReplayPayload }} from "../../types/api";

type ReadonlyValue<T> = {{ readonly value: T }};

export function useReplayProfiles(context: {{
  snapshot: ReadonlyValue<ReplayPayload | null>;
  displaySource: ReadonlyValue<"capture" | "replay" | null>;
  capture: ReadonlyValue<CaptureStatus | null>;
  textValue: (value: unknown, fallback?: string) => string;
  numberValue: (value: unknown, fallback?: number) => number;
}}) {{
  const {{ snapshot, displaySource, capture, textValue, numberValue }} = context;
{indent(state.rstrip())}
{indent(block.rstrip())}

  return {{
    selectedProfileIndex, profileTab, profiles, profilePanels,
    selectedProfile, profileTabs, selectedProfilePanel, panelValue, pairRows,
    selectedCardEstimate, selectedCardEstimateRows, selectedAccountDefaults,
    selectedAccountMastery, cardTotalValue, cardMasterySource,
    selectedCardEstimateStatus, profileRows,
  }};
}}
'''
    COMPOSABLE.parent.mkdir(parents=True, exist_ok=True)
    COMPOSABLE.write_text(module, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
