from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from dragon_nest_builder.live_dps import tcp_profile_codec as codec
from tools.audit_full_tcp_key_table import (
    envelope_direction,
    expand_inputs,
    full_table_decode,
    iter_logical_packets,
    load_full_table,
)


KNOWN_OPCODES = {
    "0301": "城镇/大厅地图初始化",
    "0302": "登录职业链",
    "0303": "角色出生身份",
    "0315": "查看信息请求",
    "0403": "自身状态请求",
    "0601": "创建队伍请求",
    "0703": "登录时自身装备档案",
    "070d": "网关路线目录",
    "0731": "卡片装备变更",
    "073d": "查看信息请求",
    "077f": "卡片档案",
    "0794": "刻印档案",
    "0c05": "地图释放/副本结算",
    "1224": "场景卸载",
    "1700": "查看身份响应",
    "1701": "查看信息请求",
    "8245": "地图确认",
}


def load_sparse_candidates(path: Path):
    candidates = defaultdict(list)
    loaded = codec._read_registry_file(path, default_source="legacy_sparse")
    for index, rows in loaded.items():
        candidates[index].extend(rows)
    for index, words in codec._V576_PROFILE_KEYS_FALLBACK.items():
        if not any(row.words == words for row in candidates[index]):
            candidates[index].append(
                codec._TcpKeyCandidate(words=words, source="legacy_fallback")
            )
    return candidates


def legacy_plaintexts(payload: bytes, direction: str, candidates):
    body = payload[3:]
    index = len(body) % 256
    rounds = (len(body) % 2) + 1
    output = []
    for candidate in candidates.get(index, ()):
        if not candidate.applies_to(len(body), direction):
            continue
        plaintext = codec.decrypt_tcp_profile_body(body, candidate.words, rounds)
        if len(plaintext) >= 4 and int.from_bytes(plaintext[:2], "little") == len(body):
            output.append(plaintext)
    return output


def readable_strings(payload: bytes) -> list[str]:
    output: list[str] = []
    for match in re.findall(rb"[ -~]{5,}", payload):
        text = match.decode("ascii", errors="ignore").strip()
        if text and text not in output:
            output.append(text[:80])
    for offset in (0, 1):
        data = payload[offset:]
        for match in re.findall(rb"(?:[\x20-\x7e\x80-\xff]\x00){3,}", data):
            try:
                text = match.decode("utf-16le").strip("\x00 ")
            except UnicodeDecodeError:
                continue
            if text and text.isprintable() and text not in output:
                output.append(text[:80])
    return output[:8]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument(
        "--full-table",
        type=Path,
        default=Path(
            "dragon_nest_builder/data/live_dps_profiles/tcp_key_table_full_v576.json"
        ),
    )
    parser.add_argument(
        "--legacy-registry",
        type=Path,
        default=Path(
            "dragon_nest_builder/data/live_dps_profiles/tcp_profile_keys_v576.json"
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    paths = expand_inputs(args.inputs)
    full = load_full_table(args.full_table)
    sparse = load_sparse_candidates(args.legacy_registry)
    totals = Counter()
    groups = Counter()
    opcode_totals = Counter()
    opcode_sizes = defaultdict(Counter)
    opcode_captures = defaultdict(set)
    opcode_endpoints = defaultdict(Counter)
    examples = defaultdict(list)

    for path in paths:
        for packet in iter_logical_packets(path):
            if packet.transport.upper() != "TCP":
                continue
            direction = envelope_direction(packet)
            decoded = full_table_decode(packet.payload, direction, full)
            if decoded is None:
                continue
            plaintext, key_index, rounds = decoded
            previous = legacy_plaintexts(packet.payload, direction, sparse)
            if any(row == plaintext for row in previous):
                totals["legacy_same_plaintext"] += 1
                continue
            category = "corrected" if previous else "new"
            totals[f"{category}_frames"] += 1
            opcode = plaintext[2:4].hex()
            body_size = len(plaintext)
            group_key = (category, direction, opcode, body_size)
            groups[group_key] += 1
            opcode_key = (category, direction, opcode)
            opcode_totals[opcode_key] += 1
            opcode_sizes[opcode_key][body_size] += 1
            opcode_captures[opcode_key].add(path.name)
            endpoint = (
                f"{packet.source_ip}:{packet.source_port}"
                f"->{packet.destination_ip}:{packet.destination_port}"
            )
            opcode_endpoints[opcode_key][endpoint] += 1
            if len(examples[opcode_key]) < 4:
                examples[opcode_key].append(
                    {
                        "capture": path.name,
                        "timestamp_seconds": round(packet.timestamp_seconds, 6),
                        "body_size": body_size,
                        "key_index": key_index,
                        "rounds": rounds,
                        "zero_ratio": round(plaintext.count(0) / len(plaintext), 4),
                        "plaintext_prefix_hex": plaintext[:128].hex(),
                        "readable_strings": readable_strings(plaintext),
                        "endpoint": endpoint,
                    }
                )

    opcode_rows = []
    for key, count in opcode_totals.most_common():
        category, direction, opcode = key
        opcode_rows.append(
            {
                "category": category,
                "direction": direction,
                "opcode_hex": opcode,
                "opcode_le": int.from_bytes(bytes.fromhex(opcode), "little"),
                "known_parser": KNOWN_OPCODES.get(opcode, ""),
                "count": count,
                "body_sizes": dict(opcode_sizes[key].most_common(20)),
                "capture_count": len(opcode_captures[key]),
                "captures": sorted(opcode_captures[key]),
                "top_endpoints": dict(opcode_endpoints[key].most_common(8)),
                "examples": examples[key],
            }
        )

    result = {
        "schema_version": 1,
        "method": (
            "full current-client table compared with the pre-integration exact-index "
            "sparse registry; captures are classification inputs only"
        ),
        "capture_count": len(paths),
        "totals": dict(totals),
        "opcode_count": len(opcode_rows),
        "opcodes": opcode_rows,
        "top_shapes": [
            {
                "category": key[0],
                "direction": key[1],
                "opcode_hex": key[2],
                "body_size": key[3],
                "count": count,
            }
            for key, count in groups.most_common(300)
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"totals": result["totals"], "opcode_count": len(opcode_rows)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
