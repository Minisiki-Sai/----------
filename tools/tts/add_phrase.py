#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给播报文案加字（或改字），把「代码文案 + manifest + 4 个音色音频」一次改齐。

背景：文案与音频是预绑定的严格匹配 ——
  announcementParts() 用最长前缀在 manifest 里找句子，找不到就抛「缺少本地语音」，
  这条播报完全不响（不会退回系统朗读）。所以改文案必须同时补音频，否则是静默失败。

本脚本做三件事，并且**在音频不全时默认拒绝写代码**，避免留下半成品状态：
  1. 按 sha256(新文案)[:16] 算出音频文件名
  2. 插入 manifest.phrases 一条（保持原格式，只加一行）
  3. 精确替换代码里的文案（skill 号定位 或 字面量定位，匹配数必须等于预期）
最后打印还缺哪几个 wav、以及验收用的测试命令。

用法：
  # 沙龙·狮蝎砸地（skill 50311）的实战文案改动
  python tools/tts/add_phrase.py --skill 50311 --field text --new 注意狮蝎砸地 --dry-run

  # 改别的文件里的字面量（必须唯一，否则请用 --count 明确预期）
  python tools/tts/add_phrase.py --file frontend/src/utils/classicMechanics.ts \
      --old 注意砸地 --new 注意食人魔砸地 --count 1
"""

import argparse
import hashlib
import json
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MANIFEST_REL = "frontend/public/voices/qwen3/manifest.json"
VOICE_SRC_REL = "frontend/src/utils/voiceAnnouncements.ts"


def phrase_id(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def read_text(path):
    """newline='' 保留原始 CRLF，避免整文件被改成 LF 后 diff 炸开。"""
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def insert_manifest(mpath, text, pid):
    """在 "phrases": { 后插入一行，保持原有缩进与换行，避免整文件重排。"""
    src = read_text(mpath)
    m = re.search(r'([ \t]*)"phrases"[ \t]*:[ \t]*\{[ \t]*(\r?\n)', src)
    if not m:
        sys.exit("manifest 里找不到 phrases 段，格式可能变了，请人工处理。")
    indent = m.group(1) + "  "
    eol = m.group(2)
    line = indent + json.dumps(text, ensure_ascii=False) + ": " + json.dumps(pid) + "," + eol
    out = src[:m.end()] + line + src[m.end():]
    json.loads(out)          # 写入前先验证仍是合法 JSON
    return out, line.rstrip()


def patch_code(path, args):
    src = read_text(path)
    if args.skill:
        pat = re.compile(r"(\b%d\s*:\s*\{[^}]*?\b%s\s*:\s*')([^']*)(')" % (args.skill, args.field))
        hits = list(pat.finditer(src))
        if len(hits) != 1:
            sys.exit("按 skill %d 的 %s 字段找到 %d 处（应为 1 处），请人工确认。"
                     % (args.skill, args.field, len(hits)))
        m = hits[0]
        old = m.group(2)
        new_src = src[:m.start(2)] + args.new + src[m.end(2):]
        where = "skill %d 的 %s" % (args.skill, args.field)
    else:
        n = src.count(args.old)
        if n != args.count:
            sys.exit("字面量「%s」出现 %d 次，与 --count %d 不符，请人工确认。"
                     % (args.old, n, args.count))
        old = args.old
        new_src = src.replace(args.old, args.new)
        where = "字面量"
    if old == args.new:
        sys.exit("新旧文案相同，无需修改。")
    return new_src, old, where


def main():
    ap = argparse.ArgumentParser(description="播报文案加字（文案+清单+音频三件套）")
    ap.add_argument("--new", required=True, help="新文案，如 注意狮蝎砸地")
    ap.add_argument("--skill", type=int, default=None, help="SEA_MECHANICS 里的技能号")
    ap.add_argument("--field", default="text", choices=["text", "upcoming"])
    ap.add_argument("--file", default=None, help="字面量模式：要改的文件（相对仓库根）")
    ap.add_argument("--old", default=None, help="字面量模式：原文案")
    ap.add_argument("--count", type=int, default=1, help="字面量模式：预期出现次数")
    ap.add_argument("--root", default=ROOT, help="仓库根（测试用）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="音频缺失也先改代码")
    args = ap.parse_args()

    if not args.skill and not (args.file and args.old):
        sys.exit("要么给 --skill，要么给 --file + --old。")

    root = os.path.abspath(args.root)
    mpath = os.path.join(root, MANIFEST_REL)
    cpath = os.path.join(root, args.file) if args.file else os.path.join(root, VOICE_SRC_REL)
    manifest = load_json(mpath)
    phrases = manifest["phrases"]
    voices = manifest.get("voices", [])
    pid = phrase_id(args.new)

    print("新文案：%s" % args.new)
    print("音频名：%s.wav   （sha256 前 16 位）" % pid)

    # ---- 代码侧
    new_src, old, where = patch_code(cpath, args)
    print("代码侧：%s  「%s」 → 「%s」  (%s)"
          % (os.path.relpath(cpath, root), old, args.new, where))

    # ---- 清单侧
    if args.new in phrases:
        print("清单侧：已存在 %s → %s，跳过插入" % (args.new, phrases[args.new]))
        if phrases[args.new] != pid:
            print("  注意：已有 id 与 sha256 规则不同（历史遗留），沿用已有的 %s" % phrases[args.new])
        pid = phrases[args.new]
    else:
        print("清单侧：新增一行  %s → %s" % (args.new, pid))

    # ---- 音频侧
    missing = []
    for v in voices:
        p = os.path.join(root, "frontend/public/voices/qwen3", v, pid + ".wav")
        if not os.path.exists(p):
            missing.append(p)
    if missing:
        print("\n音频侧：缺 %d 个文件" % len(missing))
        for p in missing:
            print("  ✗ %s" % os.path.relpath(p, root))
        print("\n补法（任选其一）：")
        print("  · 自己生成后放进 <音色目录>/%s.wav" % pid)
        print("  · 句子名导出后批量入库：")
        print("      python tools/tts/ingest.py import --src <你的文件夹> --voice <音色名>")
        print("    （文件名写成「%s.wav」即可自动对上）" % args.new)
    else:
        print("\n音频侧：4 个音色齐备 ✓")

    if args.dry_run:
        print("\n[dry-run] 未写盘。")
        return 0
    if missing and not args.force:
        print("\n已中止：音频不全时改代码会让这条播报直接静音。\n"
              "补齐音频后重跑，或加 --force 先改代码（不推荐）。")
        return 2

    # ---- 写盘
    if args.new not in phrases:
        out, line = insert_manifest(mpath, args.new, pid)
        write_text(mpath, out)
        print("\n已写入 manifest：+%s" % line.strip())
    write_text(cpath, new_src)
    print("已写入代码：%s" % os.path.relpath(cpath, root))

    print("\n验收：")
    print("  node --test frontend/tests/qwenVoiceAssets.test.ts")
    print("  python tools/tts/ingest.py check --all")
    return 0


if __name__ == "__main__":
    sys.exit(main())
