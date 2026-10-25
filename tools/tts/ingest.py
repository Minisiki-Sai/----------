#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「外部生成的音频」导入成程序能直接吃的音色目录。

适用：你用火山控制台、剪映、Azure、GPT-SoVITS 等任何工具生成好 wav，
      按句子命名丢进一个文件夹，本脚本负责 匹配 → 校验 → 转 24k/单声道/16bit → 改 hash 名入库。

两个子命令：
  check   体检一个音色目录：覆盖了几何句、缺哪些、有没有静音/削波/时长异常
  import  把源文件夹里的音频导入到音色目录

源文件命名支持三种（auto 会依次尝试）：
  1) 句子名    注意冰眼.wav                    —— 最省事，推荐
  2) 序号+清单  001.wav 002.wav … + --list 清单.txt（每行一句，顺序对应）
  3) hash 名    5a6be54fe9e1d2c5.wav           —— 已经按 manifest 命名过

用法：
  python tools/tts/ingest.py check --voice-dir Vivian
  python tools/tts/ingest.py check --all
  python tools/tts/ingest.py import --src D:/tts_out/xiaohe --voice xiaohe --dry-run
  python tools/tts/ingest.py import --src D:/tts_out/xiaohe --voice xiaohe
  python tools/tts/ingest.py import --src D:/tts_out --voice xiaohe --list 清单.txt --no-trim --normalize
"""

import argparse
import array
import json
import os
import re
import shutil
import subprocess
import sys
import wave

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
QWEN = os.path.join(ROOT, "frontend", "public", "voices", "qwen3")
TARGET_RATE = 24000

AUDIO_EXT = {".wav", ".mp3", ".m4a", ".ogg", ".opus", ".flac", ".aac"}


# ------------------------------------------------------------------ manifest
def load_manifest():
    with open(os.path.join(QWEN, "manifest.json"), encoding="utf-8") as f:
        return json.load(f)


def norm(text):
    """归一化：只留中文/字母/数字。用于文件名与句子比对。"""
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fa5]", "", text).lower()


# ------------------------------------------------------------------ wav io
def read_wav(path):
    """返回 (array('h') 单声道样本, 采样率)。非 16bit / 多声道在这里统一掉。"""
    with wave.open(path, "rb") as w:
        ch, sw, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if sw == 2:
        s = array.array("h")
        s.frombytes(raw)
        if sys.byteorder == "big":
            s.byteswap()
    elif sw == 1:
        s = array.array("h", ((b - 128) << 8 for b in raw))
        ch = ch  # 1 字节本身无声道交叉问题，按帧取
    elif sw == 3:
        s = array.array("h", (
            int.from_bytes(raw[i:i + 3], "little", signed=True) >> 8
            for i in range(0, len(raw) - 2, 3)))
    elif sw == 4:
        s = array.array("h")
        for i in range(0, len(raw) - 3, 4):
            v = int.from_bytes(raw[i:i + 4], "little", signed=True)
            s.append(max(-32768, min(32767, v >> 16)))
    else:
        raise ValueError("不支持的位深 %d" % (sw * 8))

    if ch > 1:
        frames = len(s) // ch
        mono = array.array("h", bytes(2 * frames))
        for i in range(frames):
            base = i * ch
            mono[i] = sum(s[base + c] for c in range(ch)) // ch
        s = mono
    return s, rate


def resample(s, src, dst=TARGET_RATE):
    if src == dst or not len(s):
        return s
    n = int(round(len(s) * dst / float(src)))
    out = array.array("h", bytes(2 * n))
    step = (len(s) - 1) / float(max(n - 1, 1))
    for i in range(n):
        pos = i * step
        j = int(pos)
        frac = pos - j
        a = s[j]
        b = s[j + 1] if j + 1 < len(s) else a
        out[i] = int(a + (b - a) * frac)
    return out


def trim(s, keep_ms=30):
    if not len(s):
        return s
    th = 900  # ≈ -31 dBFS
    lo, hi = 0, len(s) - 1
    while lo < hi and abs(s[lo]) < th:
        lo += 1
    while hi > lo and abs(s[hi]) < th:
        hi -= 1
    pad = int(TARGET_RATE * keep_ms / 1000.0)
    lo = max(0, lo - pad)
    hi = min(len(s) - 1, hi + pad)
    return s[lo:hi + 1]


def normalize(s, peak_db=-3.0):
    peak = max((abs(v) for v in s), default=0)
    if peak < 32:
        return s
    target = 32767.0 * (10 ** (peak_db / 20.0))
    k = target / peak
    if abs(k - 1.0) < 0.02:
        return s
    return array.array("h", (max(-32768, min(32767, int(v * k))) for v in s))


def write_wav(path, s, rate=TARGET_RATE):
    if sys.byteorder == "big":
        s = array.array("h", s)
        s.byteswap()
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(s.tobytes())


def stats(s, rate=TARGET_RATE):
    n = len(s)
    peak = max((abs(v) for v in s), default=0)
    clipped = sum(1 for v in s if abs(v) >= 32700)
    return {
        "dur": round(n / float(rate), 3),
        "peak_db": round(20 * __import__("math").log10(peak / 32768.0), 1) if peak else -99.0,
        "clip_pct": round(100.0 * clipped / n, 3) if n else 0.0,
    }


# ------------------------------------------------------------------ transcode
def have_ffmpeg():
    return shutil.which("ffmpeg") is not None


def to_wav_24k(src, tmp):
    """非 wav 或无 ffmpeg 时的兜底：用 ffmpeg 转。转不了就报清楚。"""
    if not have_ffmpeg():
        raise RuntimeError(
            "需要 ffmpeg 才能转 %s。请用 wav 格式导出，或先装 ffmpeg。" % os.path.splitext(src)[1])
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", src, "-ac", "1", "-ar", str(TARGET_RATE),
           "-sample_fmt", "s16", tmp]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg 失败：%s" % (r.stderr.strip()[:200]))
    return tmp


# ------------------------------------------------------------------ commands
def cmd_check(args):
    m = load_manifest()
    phrases = m["phrases"]
    voices = m.get("voices", [])
    targets = [args.voice_dir] if args.voice_dir else (voices or ["Vivian"])
    problems = 0
    for v in targets:
        d = os.path.join(QWEN, v)
        if not os.path.isdir(d):
            print("[%s] 目录不存在：%s" % (v, d))
            continue
        files = [f for f in os.listdir(d) if f.lower().endswith(".wav")]
        have = set(os.path.splitext(f)[0] for f in files)
        need = set(phrases.values())
        missing = need - have
        extra = have - need
        print("\n=== %s ===" % v)
        print("覆盖 %d/%d 句  |  多余文件 %d  |  目录内 wav %d"
              % (len(need) - len(missing), len(need), len(extra), len(files)))
        if extra:
            print("  多余（不影响运行，但说明 manifest 已改过）：%s%s"
                  % (", ".join(sorted(extra)[:5]), " …" if len(extra) > 5 else ""))
        if missing:
            problems += 1
            rev = {hid: t for t, hid in phrases.items()}
            print("  ✗ 缺音频 %d 句 —— 这些句子实战会直接静音：" % len(missing))
            for hid in sorted(missing):
                print("      %-30s %s.wav" % (rev.get(hid, "?"), hid))
        bad = []
        for hid in sorted(need & have):
            p = os.path.join(d, hid + ".wav")
            try:
                s, rate = read_wav(p)
                st = stats(s, rate)
                if st["dur"] < 0.08:
                    bad.append((hid, "过短 %.2fs" % st["dur"]))
                elif st["peak_db"] < -40:
                    bad.append((hid, "近静音 %.1fdB" % st["peak_db"]))
                elif st["clip_pct"] > 0.5:
                    bad.append((hid, "削波 %.2f%%" % st["clip_pct"]))
                elif abs(rate - TARGET_RATE) > 1:
                    bad.append((hid, "采样率 %d" % rate))
            except Exception as e:
                bad.append((hid, "读不出：%s" % e))
        if bad:
            problems += 1
            print("  ✗ 异常 %d 条：" % len(bad))
            for hid, why in bad[:12]:
                print("      %s  %s" % (hid, why))
    print("\n%s" % ("全部通过。" if not problems else "有问题，见上。"))
    return 0 if not problems else 1


def collect_sources(src):
    out = []
    for r, _, fs in os.walk(src):
        for f in fs:
            if os.path.splitext(f)[1].lower() in AUDIO_EXT:
                out.append(os.path.join(r, f))
    return sorted(out)


def cmd_import(args):
    m = load_manifest()
    phrases = list(m["phrases"].items())            # [(text, hash), ...]
    by_norm = {norm(t): (t, h) for t, h in phrases}
    by_hash = {h: t for t, h in phrases}
    files = collect_sources(args.src)
    if not files:
        sys.exit("源目录里没有音频文件：%s" % args.src)
    print("源文件 %d 个，manifest %d 句\n" % (len(files), len(phrases)))

    matches = {}          # hash -> (src_path, text)
    unmatched = []
    used = set()

    # 策略 3：文件名本身是 hash
    for p in files:
        stem = os.path.splitext(os.path.basename(p))[0]
        if stem in by_hash:
            matches[stem] = (p, by_hash[stem])
            used.add(p)

    # 策略 1：文件名 = 句子
    for p in files:
        if p in used:
            continue
        stem = os.path.splitext(os.path.basename(p))[0]
        hit = by_norm.get(norm(stem))
        if hit:
            matches[hit[1]] = (p, hit[0])
            used.add(p)

    # 策略 2：序号 + 清单
    if args.list_path:
        with open(args.list_path, encoding="utf-8-sig") as f:
            order = [ln.strip() for ln in f if ln.strip()]
        rest = sorted([p for p in files if p not in used], key=lambda x: (
            int(re.sub(r"\D", "", os.path.basename(x)) or 0), os.path.basename(x)))
        for line, p in zip(order, rest):
            hit = by_norm.get(norm(line))
            if hit:
                matches.setdefault(hit[1], (p, hit[0]))
                used.add(p)

    for p in files:
        if p not in used:
            unmatched.append(p)

    out_dir = args.out or os.path.join(QWEN, args.voice)
    print("匹配成功 %d 句 / 未匹配 %d 个文件" % (len(matches), len(unmatched)))
    if unmatched:
        for p in unmatched[:10]:
            print("  未匹配：%s" % os.path.basename(p))

    missing = [t for t, h in phrases if h not in matches]
    if missing:
        print("\n✗ 还缺 %d 句（这批音频不完整）：" % len(missing))
        for t in missing[:15]:
            print("    %s" % t)
        if len(missing) > 15:
            print("    … 共 %d 句" % len(missing))

    if args.dry_run:
        print("\n[dry-run] 未写盘。将写入：%s" % out_dir)
        return 0 if not missing else 1

    os.makedirs(out_dir, exist_ok=True)
    ok = fail = 0
    tmp = os.path.join(out_dir, "_tmp_ingest.wav")
    for hid, (src, text) in sorted(matches.items()):
        try:
            path = src
            if os.path.splitext(src)[1].lower() != ".wav":
                path = to_wav_24k(src, tmp)
            s, rate = read_wav(path)
            if abs(rate - TARGET_RATE) > 1:
                s = resample(s, rate)
            if args.trim:
                s = trim(s)
            if args.normalize:
                s = normalize(s)
            st = stats(s)
            write_wav(os.path.join(out_dir, hid + ".wav"), s)
            ok += 1
            print("  ✓ %-30s %5.2fs  peak %5.1fdB" % (text[:28], st["dur"], st["peak_db"]))
        except Exception as e:
            fail += 1
            print("  ✗ %-30s %s" % (text[:28], e))
    for leftover in (tmp, tmp.replace("_tmp_ingest", "_tmp_ingest0")):
        if os.path.exists(leftover):
            os.remove(leftover)

    print("\n入库 %d 条，失败 %d 条 → %s" % (ok, fail, out_dir))
    cov = len(matches) - fail
    print("覆盖率 %d/%d 句%s" % (cov, len(phrases),
                                "" if cov == len(phrases) else "  ← 未满 158 句，缺的那几句实战会静音"))
    if ok and os.path.abspath(out_dir).startswith(os.path.abspath(QWEN)):
        print("\n自检一遍：")
        args.voice_dir = args.voice
        cmd_check(args)
    return 0 if fail == 0 and not missing else 1


def main():
    ap = argparse.ArgumentParser(description="外部音频 → 程序音色目录")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="体检音色目录覆盖率与音频质量")
    c.add_argument("--voice-dir", default=None)
    c.add_argument("--all", action="store_true")

    i = sub.add_parser("import", help="导入源文件夹")
    i.add_argument("--src", required=True)
    i.add_argument("--voice", required=True, help="目标音色目录名，如 xiaohe")
    i.add_argument("--out", default=None, help="自定义输出目录（测试用）")
    i.add_argument("--list", dest="list_path", default=None, help="序号清单：每行一句")
    i.add_argument("--dry-run", action="store_true")
    i.add_argument("--no-trim", dest="trim", action="store_false", help="不裁掉首尾静音")
    i.add_argument("--normalize", action="store_true", help="峰值归一到 -3dBFS")
    i.set_defaults(trim=True)

    args = ap.parse_args()
    if args.cmd == "check":
        sys.exit(cmd_check(args))
    sys.exit(cmd_import(args))


if __name__ == "__main__":
    main()
