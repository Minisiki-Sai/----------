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


def _read_server_payloads(path: Path, payload_length: int) -> tuple[bytes, ...]:
    transport = V576TransportReassembler()
    found: list[bytes] = []
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


def _mix(value):
    return ((((value << 4) & _MASK32) ^ LShR(value, 5)) + value) & _MASK32


def _encrypt_zero(key, rounds: int):
    left = BitVecVal(0, 32)
    right = BitVecVal(0, 32)
    running_sum = 0
    for _ in range(rounds):
        left = (
            left
            + (
                _mix(right)
                ^ (BitVecVal(running_sum, 32) + key[running_sum & 3])
            )
        ) & _MASK32
        running_sum = (running_sum + _DELTA) & _MASK32
        right = (
            right
            + (
                _mix(left)
                ^ (
                    BitVecVal(running_sum, 32)
                    + key[(running_sum >> 11) & 3]
                )
            )
        ) & _MASK32
    return left, right


def _decrypt_block(ciphertext: bytes, key, rounds: int):
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
                    + key[(running_sum >> 11) & 3]
                )
            )
        ) & _MASK32
        running_sum = (running_sum - _DELTA) & _MASK32
        left = (
            left
            - (
                _mix(right)
                ^ (BitVecVal(running_sum, 32) + key[running_sum & 3])
            )
        ) & _MASK32
    return left, right


def recover(
    paths: tuple[Path, ...],
    payload_length: int,
    limit: int,
    header_constraints: int,
) -> None:
    payloads = tuple(
        payload
        for path in paths
        for payload in _read_server_payloads(path, payload_length)
    )
    if not payloads:
        raise SystemExit("no matching server TCP payloads")
    bodies = tuple(payload[3:] for payload in payloads)
    body_size = payload_length - 3
    rounds = body_size % 2 + 1
    remainder = body_size % 8
    if rounds not in {1, 2}:
        raise SystemExit("unsupported short-round count")

    blocks = Counter(
        body[offset : offset + 8]
        for body in bodies
        for offset in range(0, len(body) - remainder, 8)
    )
    zero_cipher, repeat_count = blocks.most_common(1)[0]
    tails = Counter(body[-remainder:] for body in bodies) if remainder else Counter()
    tail_cipher, tail_count = tails.most_common(1)[0] if tails else (b"", 0)
    print(
        f"payloads={len(payloads)} body_size={body_size} rounds={rounds} "
        f"zero_cipher={zero_cipher.hex()} repeats={repeat_count} "
        f"tail={tail_cipher.hex()} tail_repeats={tail_count}"
    )
    keys = [BitVec(f"key_{index}", 32) for index in range(4)]
    solver = Solver()
    solver.set(timeout=120_000)
    if rounds == 1:
        # One XTEA round only references words zero and three.  The repeated
        # zero block therefore determines both active words without needing a
        # partial tail.
        solver.add(keys[1] == 0, keys[2] == 0)
    else:
        # A repeated encrypted zero block constrains 64 key bits. Complete the
        # remaining key with independently encrypted first blocks whose
        # plaintext must all begin with the same body length. This avoids
        # guessing that a frequent partial tail is plaintext zero.
        first_blocks = tuple(dict.fromkeys(body[:8] for body in bodies))
        for block in first_blocks[: max(1, header_constraints)]:
            plaintext_left, _plaintext_right = _decrypt_block(
                block, keys, rounds
            )
            solver.add(Extract(15, 0, plaintext_left) == body_size)
    encrypted_left, encrypted_right = _encrypt_zero(keys, rounds)
    expected_left, expected_right = struct.unpack("<2I", zero_cipher)
    solver.add(encrypted_left == BitVecVal(expected_left, 32))
    solver.add(encrypted_right == BitVecVal(expected_right, 32))

    accepted: list[tuple[float, tuple[int, int, int, int], tuple[bytes, ...]]] = []
    model_count = 0
    while model_count < limit and solver.check() == sat:
        model = solver.model()
        candidate = tuple(model.eval(word, model_completion=True).as_long() for word in keys)
        model_count += 1
        plaintexts = tuple(
            decrypt_tcp_profile_body(body, candidate, rounds) for body in bodies
        )
        if all(
            len(plaintext) >= 4
            and int.from_bytes(plaintext[:2], "little") == body_size
            for plaintext in plaintexts
        ):
            zero_ratio = sum(row.count(0) for row in plaintexts) / sum(
                len(row) for row in plaintexts
            )
            accepted.append((zero_ratio, candidate, plaintexts))
        solver.add(Or(*(keys[index] != candidate[index] for index in range(4))))

    print(f"models={model_count} accepted={len(accepted)}")
    for zero_ratio, candidate, plaintexts in sorted(accepted, reverse=True)[:20]:
        print(
            "candidate="
            + ",".join(f"0x{word:08X}" for word in candidate)
            + f" zero_ratio={zero_ratio:.6f}"
        )
        for path, plaintext in zip(paths, plaintexts):
            print(
                f"  {path.name}: head={plaintext[:96].hex(' ')} "
                f"tail={plaintext[-32:].hex(' ')}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Recover a two-round v576 TCP key from a repeated encrypted "
            "zero block and an identical partial zero tail."
        )
    )
    parser.add_argument("captures", nargs="+", type=Path)
    parser.add_argument("--payload-length", type=int, required=True)
    parser.add_argument("--limit", type=int, default=4096)
    parser.add_argument("--header-constraints", type=int, default=8)
    args = parser.parse_args()
    recover(
        tuple(args.captures),
        args.payload_length,
        args.limit,
        args.header_constraints,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
