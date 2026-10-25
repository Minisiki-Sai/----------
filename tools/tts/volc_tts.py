#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""豆包（火山引擎）语音合成 · 小何2.0 / Vivi2.0 批量生成器

两种模式：
  preview  生成固定的一组战斗播报样例句（用于挑音色），同时写 volc/manifest.json
  batch    按 frontend/public/voices/qwen3/manifest.json 的 158 句全量生成，
           文件名沿用原 hash，可直接覆盖现有音色目录

凭证（控制台 → 语音技术 → 应用管理，或开通"语音合成大模型"后的 AppID / Access Token）：
  环境变量  VOLC_APP_ID / VOLC_ACCESS_TOKEN
  或参数    --appid / --token
  或本文件同目录 tts_cred.json: {"appid": "...", "token": "..."}

用法：
  python tools/tts/volc_tts.py probe                    # 先验证凭证（合成 2 个字）
  python tools/tts/volc_tts.py preview                  # 生成样例到 .workbuddy/audition/volc
  python tools/tts/volc_tts.py batch --voice-dir xiaohe --dry-run
  python tools/tts/volc_tts.py batch --voice-dir xiaohe # 真写盘
"""

import argparse
import base64
import hashlib
import json
import os
import sys
import time
import uuid
import urllib.error
import urllib.request

V3_URL = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
V1_URL = "https://openspeech.bytedance.com/api/v1/tts"

# 2.0 音色 → (voice_type, 显示名)
VOICES = {
    "xiaohe": ("zh_female_xiaohe_uranus_bigtts", "小何 2.0"),
    "vv": ("zh_female_vv_uranus_bigtts", "Vivi 2.0"),
}
RESOURCE_ID_2 = "seed-tts-2.0"  # 音色对应的模型版本

# 挑音色用的样例句：覆盖 极短 / 短 / 中 / 长，且都是实战真会播的句子
PREVIEW = [
    ("跳", "f0dc90703bef68e0"),
    ("冰捆已好", "47e8efaf15b287d7"),
    ("注意冰眼", "5a6be54fe9e1d2c5"),
    ("即将冰眼", "1258da2d7eed9b6a"),
    ("牛头怪即将出现", "4b1683d13fb0b049"),
    ("注意元素点名", "515eb6659b8678d9"),
    ("注意冲锋一次接砸地", "d62c29bc0192140a"),
    ("注意砸地，前往9点", "526687e66413e854"),
    ("马上飞天、注意躲避龙息消灭暗影", "51f9012e083efde9"),
]

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


# ---------------------------------------------------------------- credentials
def load_cred(args):
    appid = args.appid or os.environ.get("VOLC_APP_ID")
    token = args.token or os.environ.get("VOLC_ACCESS_TOKEN")
    if not (appid and token):
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tts_cred.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            appid = appid or d.get("appid")
            token = token or d.get("token")
    if not (appid and token):
        sys.exit(
            "缺少凭证。设置环境变量 VOLC_APP_ID / VOLC_ACCESS_TOKEN，\n"
            "或写入 tools/tts/tts_cred.json：{\"appid\":\"...\",\"token\":\"...\"}"
        )
    return appid, token


# ---------------------------------------------------------------- transport
def _post(url, headers, body, timeout=60):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except urllib.error.URLError as e:
        sys.exit("网络不可达：%s" % e)


def _scan_json_objects(raw):
    """v3 是流式返回，可能是多个 JSON 对象拼接。用括号配平逐个切出来。"""
    text = raw.decode("utf-8", "ignore")
    out, depth, start, in_str, esc = [], 0, -1, False, False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    out.append(json.loads(text[start:i + 1]))
                except Exception:
                    pass
                start = -1
    return out


def synth_v3(text, speaker, appid, token, fmt="wav", sample_rate=24000, speech_rate=0):
    headers = {
        "Content-Type": "application/json",
        "X-Api-App-Id": appid,
        "X-Api-Access-Key": token,
        "X-Api-Resource-Id": RESOURCE_ID_2,
        "X-Api-Request-Id": str(uuid.uuid4()),
    }
    body = {
        "user": {"uid": "dnact"},
        "req_params": {
            "text": text,
            "speaker": speaker,
            "audio_params": {"format": fmt, "sample_rate": sample_rate, "speech_rate": speech_rate},
        },
    }
    status, raw = _post(V3_URL, headers, body)
    if status != 200:
        raise RuntimeError("HTTP %s: %s" % (status, raw[:400].decode("utf-8", "ignore")))
    objs = _scan_json_objects(raw)
    if not objs:
        raise RuntimeError("无法解析响应：%s" % raw[:400].decode("utf-8", "ignore"))
    chunks, err = [], None
    for o in objs:
        if o.get("data"):
            chunks.append(base64.b64decode(o["data"]))
        code = o.get("code")
        if code not in (None, 0, 20000000, 3000):
            err = "code=%s message=%s" % (code, o.get("message"))
    if err:
        raise RuntimeError(err)
    audio = b"".join(chunks)
    if not audio:
        raise RuntimeError("响应里没有音频数据")
    return audio


def synth_v1(text, speaker, appid, token, fmt="wav", sample_rate=24000, speech_rate=0, cluster="volcano_tts"):
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer;%s" % token,
    }
    body = {
        "app": {"appid": appid, "token": token, "cluster": cluster},
        "user": {"uid": "dnact"},
        "audio": {
            "voice_type": speaker,
            "encoding": fmt,
            "sample_rate": sample_rate,
            "speed_ratio": 1.0 + speech_rate / 100.0,
            "volume_ratio": 1.0,
            "pitch_ratio": 1.0,
        },
        "request": {
            "reqid": str(uuid.uuid4()),
            "text": text,
            "text_type": "plain",
            "operation": "query",
            "with_frontend": 1,
            "frontend_type": "unitTson",
        },
    }
    status, raw = _post(V1_URL, headers, body)
    if status != 200:
        raise RuntimeError("HTTP %s: %s" % (status, raw[:400].decode("utf-8", "ignore")))
    try:
        d = json.loads(raw.decode("utf-8", "ignore"))
    except Exception:
        raise RuntimeError("响应不是 JSON：%s" % raw[:400].decode("utf-8", "ignore"))
    if d.get("code") not in (3000, 0):
        raise RuntimeError("code=%s message=%s" % (d.get("code"), d.get("message")))
    return base64.b64decode(d["data"])


def synth(text, voice_key, appid, token, api, **kw):
    speaker = VOICES[voice_key][0]
    if api == "v1":
        return synth_v1(text, speaker, appid, token, **kw)
    try:
        return synth_v3(text, speaker, appid, token, **kw)
    except RuntimeError as e:
        # 2.0 音色若在当前账号下只能走 v1，自动降级一次
        print("    v3 失败(%s)，回退 v1 重试…" % str(e)[:90])
        return synth_v1(text, speaker, appid, token, **kw)


def wav_info(blob):
    if blob[:4] != b"RIFF":
        return None
    try:
        import io
        import wave
        w = wave.open(io.BytesIO(blob))
        return w.getnframes() / float(w.getframerate()), w.getframerate(), w.getnchannels()
    except Exception:
        return None


# ---------------------------------------------------------------- commands
def cmd_probe(args):
    appid, token = load_cred(args)
    for key in args.voices:
        try:
            t0 = time.time()
            blob = synth("冰眼", key, appid, token, args.api, fmt="wav", sample_rate=24000)
            info = wav_info(blob)
            print("[OK]   %-8s %-10s %6.1f KB  %s  首次耗时 %.2fs"
                  % (key, VOICES[key][1], len(blob) / 1024.0,
                     ("%.2fs" % info[0]) if info else "非 wav", time.time() - t0))
        except Exception as e:
            print("[FAIL] %-8s %-10s %s" % (key, VOICES[key][1], e))


def cmd_preview(args):
    appid, token = load_cred(args)
    out = os.path.join(ROOT, ".workbuddy", "audition", "volc")
    manifest = {}
    mpath = os.path.join(out, "manifest.json")
    if os.path.exists(mpath):
        try:
            with open(mpath, encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception:
            manifest = {}
    total = 0
    for text, hid in PREVIEW:
        manifest.setdefault(text, {})
        for key in args.voices:
            name = VOICES[key][1]
            target = os.path.join(out, key, hid + ".wav")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            try:
                t0 = time.time()
                blob = synth(text, key, appid, token, args.api, fmt="wav", sample_rate=24000)
                with open(target, "wb") as f:
                    f.write(blob)
                info = wav_info(blob)
                manifest[text][key] = {"file": "%s/%s.wav" % (key, hid),
                                       "dur": round(info[0], 2) if info else None}
                total += 1
                print("  %-10s %-22s %5.1fKB %s  %.2fs"
                      % (name, text[:20], len(blob) / 1024.0,
                         ("%5.2fs" % info[0]) if info else "   ?  ", time.time() - t0))
            except Exception as e:
                print("  %-10s %-22s 失败: %s" % (name, text[:20], e))
            time.sleep(args.gap)
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print("\n完成 %d 条 → %s" % (total, out))
    print("打开 http://127.0.0.1:8777/index.html 第 04 节即可对比试听。")


def cmd_batch(args):
    mf = os.path.join(ROOT, "frontend", "public", "voices", "qwen3", "manifest.json")
    with open(mf, encoding="utf-8") as f:
        phrases = json.load(f)["phrases"]
    items = list(phrases.items())
    if args.limit:
        items = items[:args.limit]
    out = args.out or os.path.join(ROOT, "frontend", "public", "voices", "qwen3", args.voice_dir)
    print("目标：%s\n句数：%d  音色：%s  写盘：%s\n"
          % (out, len(items), VOICES[args.voice][1], "否（dry-run）" if args.dry_run else "是"))
    appid = token = None
    if not args.dry_run:
        appid, token = load_cred(args)
    ok = fail = 0
    for i, (text, hid) in enumerate(items, 1):
        target = os.path.join(out, hid + ".wav")
        if args.skip_existing and os.path.exists(target):
            continue
        if args.dry_run:
            print("  [%3d/%d] %s → %s.wav" % (i, len(items), text, hid))
            continue
        try:
            blob = synth(text, args.voice, appid, token, args.api, fmt="wav", sample_rate=24000)
            os.makedirs(out, exist_ok=True)
            with open(target, "wb") as f:
                f.write(blob)
            ok += 1
            info = wav_info(blob)
            print("  [%3d/%d] %-28s %s" % (i, len(items), text[:26], ("%.2fs" % info[0]) if info else ""))
        except Exception as e:
            fail += 1
            print("  [%3d/%d] %-28s 失败: %s" % (i, len(items), text[:26], e))
        time.sleep(args.gap)
    if not args.dry_run:
        print("\n成功 %d，失败 %d" % (ok, fail))
        if fail:
            print("提醒：manifest 里缺音频的句子在实战中会直接静音，请务必重跑到 0 失败。")


def main():
    ap = argparse.ArgumentParser(description="豆包 TTS 小何2.0 / Vivi2.0 生成器")
    ap.add_argument("cmd", choices=["probe", "preview", "batch"])
    ap.add_argument("--appid", default=None)
    ap.add_argument("--token", default=None)
    ap.add_argument("--api", choices=["v3", "v1"], default="v3", help="默认 v3（2.0 音色用）")
    ap.add_argument("--voices", nargs="+", default=["xiaohe", "vv"], choices=list(VOICES))
    ap.add_argument("--voice", default="xiaohe", choices=list(VOICES), help="batch 模式用")
    ap.add_argument("--voice-dir", default="xiaohe", help="batch 输出的音色目录名")
    ap.add_argument("--out", default=None, help="batch 输出目录（默认 qwen3/<voice-dir>）")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--gap", type=float, default=0.25, help="请求间隔秒，防限流")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-existing", action="store_true")
    args = ap.parse_args()
    {"probe": cmd_probe, "preview": cmd_preview, "batch": cmd_batch}[args.cmd](args)


if __name__ == "__main__":
    main()
