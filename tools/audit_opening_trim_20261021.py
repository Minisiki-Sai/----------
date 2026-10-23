"""Read-only census and shadow filter. Never changes captures or the live pipeline."""
import hashlib
import html
import json
import struct
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dragon_nest_builder.live_dps.npcap import (
    PREBATTLE_STATE_ADAPTER_INDEX, SESSION_EVIDENCE_ADAPTER_INDEX,
    decode_prebattle_state_snapshot,
)

HEADER = struct.Struct("<dBII")
CAP = Path.home() / "Documents/DragonNestDpsCapture/live_dps_diagnostics"
OUT = ROOT / "outputs/verification/opening_trim_20261021"


def records(path):
    with path.open("rb") as f:
        assert f.read(6) == b"DNDP1\0", path
        while header := f.read(HEADER.size):
            assert len(header) == HEADER.size, "truncated header"
            timestamp, adapter, original, length = HEADER.unpack(header)
            payload = f.read(length)
            assert len(payload) == length, "truncated frame"
            yield timestamp, adapter, header, payload


def audit(path, field):
    boundaries = [decode_prebattle_state_snapshot(payload)
                  for _, adapter, _, payload in records(path)
                  if adapter == PREBATTLE_STATE_ADAPTER_INDEX]
    assert len(boundaries) == 1, "ambiguous or missing saved boundary"
    evidence = boundaries[0]
    boundary = float(evidence["boundary_timestamp"])
    assert boundary > 0
    shown = datetime.fromisoformat(field["overlayShownAt"]).timestamp()
    before_hash, after_hash = hashlib.sha256(), hashlib.sha256()
    raw = old = old_bytes = pre_visible = session = old_session = 0
    # Reserved appendix records are not ordinary packets. In particular, the
    # session evidence follows a narrow observer, not normal combat replay.
    for timestamp, adapter, header, payload in records(path):
        if adapter >= 240:
            if adapter == SESSION_EVIDENCE_ADAPTER_INDEX:
                session += 1
                old_session += timestamp < boundary
            continue
        raw += 1
        before_hash.update(header)
        before_hash.update(payload)
        if timestamp < boundary:
            old += 1
            old_bytes += len(payload)
        else:
            after_hash.update(header)
            after_hash.update(payload)
            pre_visible += timestamp < shown
    return {
        "file": path.name, "boundaryEpoch": boundary,
        "boundaryLocal": datetime.fromtimestamp(boundary).isoformat(timespec="milliseconds"),
        "overlayShownAt": field["overlayShownAt"],
        "boundaryToOverlayVisibleSeconds": round(shown - boundary, 3),
        "rawFrames": raw, "shadowRemovedFrames": old, "shadowRemovedBytes": old_bytes,
        "postBoundaryPreVisibleFrames": pre_visible,
        "sessionEvidenceFrames": session, "preBoundarySessionEvidenceFrames": old_session,
        "originalInputHash": before_hash.hexdigest(),
        "filteredInputHash": after_hash.hexdigest(),
        "inputUnchanged": before_hash.digest() == after_hash.digest(),
        "boundaryConfidence": evidence.get("confidence", ""),
        "boundaryMapId": evidence.get("map_id", 0),
    }


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    fields = json.loads((ROOT / "outputs/verification/voice_latency_1020/field_audit.json")
                        .read_text(encoding="utf-8"))["captures"]
    rows = [audit(CAP / row["file"], row) for row in fields]
    result = {
        "scope": "All ordinary physical frames in seven 1020 captures; saved formal boundary. "
                 "Shadow filtering only. Does not certify boundary accuracy, unsaved probe traffic, "
                 "or end-to-end audio latency. Overlay-visible time is a UI milestone, not decoder-ready time.",
        "rawFrames": sum(r["rawFrames"] for r in rows),
        "removedFrames": sum(r["shadowRemovedFrames"] for r in rows),
        "allInputsUnchanged": all(r["inputUnchanged"] for r in rows),
        "rows": rows,
    }
    (OUT / "shadow_trim_audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    table = "".join(
        f"<tr><td>{html.escape(r['file'])}</td><td>{r['rawFrames']:,}</td>"
        f"<td>{r['shadowRemovedFrames']}</td>"
        f"<td>{r['boundaryToOverlayVisibleSeconds']:.3f}s</td>"
        f"<td>{r['postBoundaryPreVisibleFrames']:,}</td>"
        f"<td>{'一致' if r['inputUnchanged'] else '变化'}</td></tr>" for r in rows
    )
    report = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>进本前裁剪影子验证 · 2026-10-21</title>
<style>body{{font:16px/1.7 system-ui,"Microsoft YaHei";background:#f3f7f8;color:#29444d}}
main{{max-width:1100px;margin:32px auto;background:white;padding:32px;border-radius:14px}}
table{{border-collapse:collapse;width:100%}}td,th{{padding:10px;text-align:left;border-bottom:1px solid #dce8eb}}
th{{background:#edf5f6}}h2{{color:#007f88;font-size:21px}}.note{{padding:16px;background:#fff5e2}}</style>
<main><h1>增加进本前裁剪，能缓解开场积压吗？</h1>
<p class="note">对这 7 把：没有发现可裁的进本前普通包。共检查 {result['rawFrames']:,} 条普通帧，
可裁 {result['removedFrames']} 条，裁剪前后的完整输入哈希全部一致。不能将这项裁剪作为本批延迟的有效修复。</p>
<h2>逐场结果</h2><table><tr><th>抓包</th><th>普通帧</th><th>可裁帧</th>
<th>进本边界→悬浮出现</th><th>边界后、悬浮前的帧</th><th>输入哈希</th></tr>{table}</table>
<p>悬浮出现时间来自此前独立桌面日志配对，只是 UI 里程碑，不等于解码启动或完成时刻。
最后两列不能解释为所有这些帧在悬浮出现时仍排队，更不能将它们全部视为无用包。</p>
<h2>怎么验证的</h2><p>读取每份原文件的战前状态元数据，取得其保存的正式进本边界。
对所有普通网卡帧执行影子过滤：只留下时间戳不早于边界的帧，并核对过滤前后包含时间戳、
网卡编号、长度和原始字节的 SHA-256。原文件没有改写；影子过滤没有进入生产代码。</p>
<p>因为 7 份过滤前后的输入完全相同，所以没有把两次相同输入的耗时波动称作“提速”，
也没有虚构裁剪后的声卡播放延迟。这个结果表示可消除的普通包解码工作量为 0，
并不表示知道所有启动阶段分别耗时多少。</p>
<h2>为什么文件里还会有较早的时间戳</h2>
<p>另有 8–30 条／场的会话证据记录，使用保留的元数据网卡编号，而不是普通网卡流。
它们用于恢复必要身份、血量等上下文；不能将文件里最早的证据时间当作战斗开始，
也不能算作这次要清理的普通队列垃圾。</p>
<h2>下一步该优化哪里</h2>
<p>现有探针已经按识别到的进本边界裁剪了预缓冲。这批样本的待处理数据主要问题不是残留的庭院普通包。
应进一步测量边界确认、探针移交、目录／解析器初始化和状态恢复；减少必需开场数据的重复处理，
并控制快照和身份维护竞争。不要把边界改为悬浮出现时间来制造裁剪收益。</p>
<p>限制：没有证明保存的边界就是游戏的绝对真实进本瞬间；没有重建未保存的探针前史；
没有将跨边界 TCP 片段或必要状态作为可删除对象。若其他抓包确实存在边界前普通包，
须另做状态完整性和协议重组验证，不能直接套用无条件丢包。</p>
<p><a href="shadow_trim_audit.json">完整数据与哈希</a> ·
复验工具：tools/audit_opening_trim_20261021.py</p></main></html>"""
    (OUT / "进本前裁剪与开场积压实测.html").write_text(report, encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
