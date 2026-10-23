from __future__ import annotations

import argparse
import struct
from collections import Counter
from pathlib import Path

from z3 import BitVec, BitVecVal, Extract, LShR, Or, Solver, sat

from dragon_nest_builder.live_dps.protocol_v576 import (
    V576TransportReassembler,
    decode_captured_frame,
)
from dragon_nest_builder.live_dps.tcp_profile_codec import (
    decrypt_tcp_profile_body,
)

try:
    from analyze_npcap_trace import read_trace
except ImportError:  # pragma: no cover - module execution fallback
    from tools.analyze_npcap_trace import read_trace


_MASK32 = 0xFFFFFFFF
_DELTA = 0x9E3779B9


def _mix(value):
    return ((((value << 4) & _MASK32) ^ LShR(value, 5)) + value) & _MASK32


def _decrypt_block(ciphertext: bytes, keys, rounds: int):
    left_value, right_value = struct.unpack("<2I", ciphertext)
    left = BitVecVal(left_value, 32)
    right = BitVecVal(right_value, 32)
    running_sum = (rounds * _DELTA) & _MASK32
    for _ in range(rounds):
        right = (
            right
            - (
                _mix(left)
                ^ (
                    BitVecVal(running_sum, 32)
                    + keys[(running_sum >> 11) & 3]
                )
            )
        ) & _MASK32
        running_sum = (running_sum - _DELTA) & _MASK32
        left = (
            left
            - (
                _mix(right)
                ^ (BitVecVal(running_sum, 32) + keys[running_sum & 3])
            )
        ) & _MASK32
    return left, right


def _capture_paths(inputs: tuple[Path, ...]) -> tuple[Path, ...]:
    paths: list[Path] = []
    for item in inputs:
        if item.is_dir():
            paths.extend(item.rglob("*.dpscap"))
        elif item.is_file():
            paths.append(item)
    return tuple(sorted(set(path.resolve() for path in paths), key=str))


def _read_server_payloads(
    paths: tuple[Path, ...],
    payload_length: int,
) -> tuple[bytes, ...]:
    found: list[bytes] = []
    for path in paths:
        transport = V576TransportReassembler()
        for timestamp, adapter, original_length, frame in read_trace(path):
            if adapter in {254, 255}:
                continue
            packet = decode_captured_frame(timestamp, frame, original_length)
            if packet is None:
                continue
            for logical in transport.feed(packet):
                if (
                    logical.transport.upper() == "TCP"
                    and logical.inferred_direction() == "server_to_client"
                    and len(logical.payload) == payload_length
                ):
                    found.append(logical.payload)
        for logical in transport.flush():
            if (
                logical.transport.upper() == "TCP"
                and logical.inferred_direction() == "server_to_client"
                and len(logical.payload) == payload_length
            ):
                found.append(logical.payload)
    return tuple(found)


def recover(
    inputs: tuple[Path, ...],
    payload_length: int,
    constraint_count: int,
    model_limit: int,
    candidate_key: tuple[int, int, int, int] | None = None,
    common_tail_plaintext: bytes | None = None,
) -> None:
    paths = _capture_paths(inputs)
    payloads = _read_server_payloads(paths, payload_length)
    if not payloads:
        raise SystemExit("no matching server TCP payloads")
    bodies = tuple(payload[3:] for payload in payloads)
    body_size = payload_length - 3
    rounds = body_size % 2 + 1
    first_blocks = tuple(dict.fromkeys(body[:8] for body in bodies))
    tail_size = body_size % 8
    tail_counts = Counter(
        body[-tail_size:] for body in bodies
    ) if tail_size else Counter()
    constraints = first_blocks[: max(1, constraint_count)]
    print(
        f"captures={len(paths)} payloads={len(payloads)} "
        f"unique_headers={len(first_blocks)} body_size={body_size} "
        f"rounds={rounds} constraints={len(constraints)}"
    )
    if tail_counts:
        print(
            "common_tails="
            + ",".join(
                f"{value.hex()}:{count}"
                for value, count in tail_counts.most_common(12)
            )
        )

    if candidate_key is not None:
        plaintexts = tuple(
            decrypt_tcp_profile_body(body, candidate_key, rounds)
            for body in bodies
        )
        valid = sum(
            int.from_bytes(plaintext[:2], "little") == body_size
            for plaintext in plaintexts
        )
        opcode_counts = Counter(
            plaintext[2:4].hex() for plaintext in plaintexts if len(plaintext) >= 4
        )
        plaintext_tail_counts = Counter(
            plaintext[-tail_size:] for plaintext in plaintexts
        ) if tail_size else Counter()
        zero_ratio = sum(row.count(0) for row in plaintexts) / sum(
            len(row) for row in plaintexts
        )
        print(
            "candidate_key="
            + ",".join(f"0x{word:08X}" for word in candidate_key)
            + f" valid={valid}/{len(plaintexts)} zero_ratio={zero_ratio:.6f}"
        )
        print(
            "candidate_opcodes="
            + ",".join(
                f"{opcode}:{count}" for opcode, count in opcode_counts.most_common(20)
            )
        )
        if plaintext_tail_counts:
            print(
                "candidate_plaintext_tails="
                + ",".join(
                    f"{value.hex()}:{count}"
                    for value, count in plaintext_tail_counts.most_common(20)
                )
            )
        for plaintext in tuple(dict.fromkeys(plaintexts))[:20]:
            print(f"candidate_sample={plaintext.hex(' ')}")

    keys = [BitVec(f"key_{index}", 32) for index in range(4)]
    solver = Solver()
    solver.set(timeout=180_000)
    if rounds == 1:
        # One round only references key words 0 and 3. Keep unused words at
        # zero so the recovered registry entry is deterministic.
        solver.add(keys[1] == 0, keys[2] == 0)
    if common_tail_plaintext is not None:
        if not tail_counts or len(common_tail_plaintext) != tail_size:
            raise SystemExit(
                "--common-tail-plaintext must contain exactly "
                f"{tail_size} bytes"
            )
        common_ciphertext = tail_counts.most_common(1)[0][0]
        for index, (cipher_value, plain_value) in enumerate(
            zip(common_ciphertext, common_tail_plaintext)
        ):
            word_index, byte_index = divmod(index, 4)
            solver.add(
                Extract(
                    byte_index * 8 + 7,
                    byte_index * 8,
                    keys[word_index],
                )
                == (cipher_value ^ plain_value)
            )
    for block in constraints:
        plaintext_left, _plaintext_right = _decrypt_block(block, keys, rounds)
        solver.add(Extract(15, 0, plaintext_left) == body_size)

    candidates: list[tuple[int, float, tuple[int, int, int, int], bytes]] = []
    checked = 0
    while checked < model_limit and solver.check() == sat:
        model = solver.model()
        candidate = tuple(
            model.eval(word, model_completion=True).as_long() for word in keys
        )
        checked += 1
        plaintexts = tuple(
            decrypt_tcp_profile_body(body, candidate, rounds) for body in bodies
        )
        valid = sum(
            int.from_bytes(plaintext[:2], "little") == body_size
            for plaintext in plaintexts
        )
        zero_ratio = sum(row.count(0) for row in plaintexts) / sum(
            len(row) for row in plaintexts
        )
        if valid:
            candidates.append((valid, zero_ratio, candidate, plaintexts[0]))
        solver.add(Or(*(keys[index] != candidate[index] for index in range(4))))

    print(f"models={checked} candidates={len(candidates)}")
    for valid, zero_ratio, candidate, sample in sorted(candidates, reverse=True)[:20]:
        print(
            "key="
            + ",".join(f"0x{word:08X}" for word in candidate)
            + f" valid={valid}/{len(payloads)} zero_ratio={zero_ratio:.6f} "
            + f"sample={sample[:96].hex(' ')} "
            + f"tail={sample[-32:].hex(' ')}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Recover short-round v576 TCP keys by requiring the decrypted "
            "two-byte body length across independent encrypted messages."
        )
    )
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--payload-length", type=int, required=True)
    parser.add_argument("--constraints", type=int, default=12)
    parser.add_argument("--models", type=int, default=32)
    parser.add_argument(
        "--candidate-key",
        nargs=4,
        type=lambda value: int(value, 0),
        metavar=("K0", "K1", "K2", "K3"),
        help="also validate and summarize one explicit four-word key",
    )
    parser.add_argument(
        "--common-tail-plaintext",
        type=lambda value: bytes.fromhex(value.replace(" ", "")),
        help=(
            "known plaintext for the most frequent partial tail; used as "
            "additional key-byte constraints"
        ),
    )
    args = parser.parse_args()
    recover(
        tuple(args.inputs),
        args.payload_length,
        args.constraints,
        args.models,
        tuple(args.candidate_key) if args.candidate_key is not None else None,
        args.common_tail_plaintext,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
