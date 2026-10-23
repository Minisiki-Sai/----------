from __future__ import annotations

import argparse
import ipaddress
import os
import socket
import struct
from collections import Counter
from pathlib import Path


MAGIC = b"DNDP1\x00"
FRAME_HEADER = struct.Struct("<dBII")
TCP = 6
UDP = 17


def _ip_offset(frame: bytes) -> tuple[int, int] | None:
    """Return (IP header offset, IP version) for supported Npcap link frames."""

    if frame and frame[0] >> 4 in {4, 6}:
        return 0, frame[0] >> 4

    # Npcap loopback frames use a four-byte DLT_NULL family header.
    if len(frame) >= 5 and frame[4] >> 4 in {4, 6}:
        return 4, frame[4] >> 4

    if len(frame) < 14:
        return None
    offset = 14
    ethertype = int.from_bytes(frame[12:14], "big")
    while ethertype in {0x8100, 0x88A8, 0x9100}:
        if len(frame) < offset + 4:
            return None
        ethertype = int.from_bytes(frame[offset + 2 : offset + 4], "big")
        offset += 4
    if ethertype == 0x0800:
        return offset, 4
    if ethertype == 0x86DD:
        return offset, 6
    return None


def _transport_tuple(
    frame: bytes,
) -> tuple[int, str, int, str, int] | None:
    located = _ip_offset(frame)
    if located is None:
        return None
    offset, version = located
    if version == 4:
        if len(frame) < offset + 20:
            return None
        header_length = (frame[offset] & 0x0F) * 4
        if header_length < 20 or len(frame) < offset + header_length + 4:
            return None
        protocol = frame[offset + 9]
        source_ip = socket.inet_ntoa(frame[offset + 12 : offset + 16])
        destination_ip = socket.inet_ntoa(frame[offset + 16 : offset + 20])
        transport_offset = offset + header_length
    else:
        if len(frame) < offset + 44:
            return None
        # The capture corpus uses ordinary IPv6 TCP/UDP packets. Extension
        # headers are intentionally rejected instead of guessing offsets.
        protocol = frame[offset + 6]
        if protocol not in {TCP, UDP}:
            return None
        source_ip = socket.inet_ntop(socket.AF_INET6, frame[offset + 8 : offset + 24])
        destination_ip = socket.inet_ntop(
            socket.AF_INET6, frame[offset + 24 : offset + 40]
        )
        transport_offset = offset + 40

    if protocol not in {TCP, UDP} or len(frame) < transport_offset + 4:
        return None
    source_port, destination_port = struct.unpack(
        "!HH", frame[transport_offset : transport_offset + 4]
    )
    return protocol, source_ip, source_port, destination_ip, destination_port


def clean_capture(source: Path, destination: Path) -> dict[str, object]:
    if source.resolve() == destination.resolve():
        raise ValueError("输出文件不能覆盖原始抓包")
    if destination.exists():
        raise FileExistsError(f"输出文件已存在：{destination}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    if temporary.exists():
        temporary.unlink()

    counts: Counter[str] = Counter()
    bytes_by_kind: Counter[str] = Counter()
    first_timestamp: float | None = None
    last_timestamp: float | None = None

    try:
        with source.open("rb", buffering=1024 * 1024) as reader, temporary.open(
            "wb", buffering=1024 * 1024
        ) as writer:
            if reader.read(len(MAGIC)) != MAGIC:
                raise ValueError("不是有效的 Dragon Nest .dpscap 文件")
            writer.write(MAGIC)
            while True:
                header = reader.read(FRAME_HEADER.size)
                if not header:
                    break
                if len(header) != FRAME_HEADER.size:
                    raise ValueError("抓包尾部帧头不完整")
                timestamp, adapter, original_length, captured_length = (
                    FRAME_HEADER.unpack(header)
                )
                frame = reader.read(captured_length)
                if len(frame) != captured_length:
                    raise ValueError("抓包尾部帧数据不完整")

                counts["input"] += 1
                bytes_by_kind["input"] += captured_length
                transport = _transport_tuple(frame)
                if transport is None:
                    counts["unsupported"] += 1
                    bytes_by_kind["unsupported"] += captured_length
                    continue
                protocol, source_ip, _source_port, destination_ip, _destination_port = (
                    transport
                )
                if ipaddress.ip_address(source_ip).is_loopback or ipaddress.ip_address(
                    destination_ip
                ).is_loopback:
                    counts["loopback"] += 1
                    bytes_by_kind["loopback"] += captured_length
                    continue

                kind = "tcp" if protocol == TCP else "udp"
                counts[kind] += 1
                bytes_by_kind[kind] += captured_length
                writer.write(header)
                writer.write(frame)
                first_timestamp = timestamp if first_timestamp is None else first_timestamp
                last_timestamp = timestamp

            writer.flush()
            os.fsync(writer.fileno())
        temporary.replace(destination)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise

    return {
        "source": str(source),
        "destination": str(destination),
        "counts": dict(counts),
        "captured_bytes": dict(bytes_by_kind),
        "duration_seconds": (
            max(0.0, last_timestamp - first_timestamp)
            if first_timestamp is not None and last_timestamp is not None
            else 0.0
        ),
        "output_bytes": destination.stat().st_size,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove loopback/non-TCP/UDP noise from a .dpscap capture."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    result = clean_capture(args.source, args.destination)
    for key, value in result.items():
        print(f"{key}={value}")


if __name__ == "__main__":
    main()
