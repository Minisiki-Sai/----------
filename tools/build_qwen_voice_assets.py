"""Build offline announcement assets; never run this model in the capture process."""
from pathlib import Path
import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / 'work/qwen_custom_voice_1_7b'
REVISION = '0c0e3051f131929182e2c023b9537f8b1c68adfe'
REPO = 'Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice'
OUT = ROOT / 'frontend/public/voices/qwen3'
SPEAKERS = ['Vivian', 'Serena', 'Uncle_Fu', 'Dylan']

def download():
    with urllib.request.urlopen(f'https://huggingface.co/api/models/{REPO}/revision/{REVISION}') as r:
        files = json.load(r)['siblings']
    def one(entry):
        name = entry['rfilename']
        if name == '.gitattributes':
            return
        target = MODEL / name
        if target.is_file():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        print('Downloading', name, flush=True)
        tmp = target.with_suffix(target.suffix + '.part')
        urllib.request.urlretrieve(f'https://huggingface.co/{REPO}/resolve/{REVISION}/{name}', tmp)
        tmp.replace(target)
        print('Downloaded', name, target.stat().st_size, flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(one, files))

def phrases(include_hard=True):
    source = (ROOT / 'frontend/src/utils/voiceAnnouncements.ts').read_text(encoding='utf-8')
    result = set(re.findall(r"(?:text|upcoming): '([^']+)'", source))
    classic = '\n'.join((ROOT / f'frontend/src/utils/{name}Mechanics.ts').read_text(encoding='utf-8') for name in (('classic', 'hard', 'sand') if include_hard else ('classic',)))
    for block in re.findall(r'texts:\[([^\]]+)\]', classic):
        result.update(re.findall(r"'([^']+)'", block))
    result.update(['爆炸兵出现', '所有爆炸兵消失'])
    result.update([
        '语音播报试听，请注意战斗机制', '速击杀牛头怪', '牛头怪即将出现', '马上拉冰', '黑影即将爆炸',
        '圣骑士出现', '红衣追随者出现', '红衣追随者退场', '红衣追随者爆炸，注意躲避',
        '马上飞天、注意躲避龙息消灭暗影', '马上飞天、注意上炮',
        '注意拉冰，三连啄后前往右侧安全区', '注意拉冰，三连啄后前往左侧安全区',
        '注意拉冰，前往龙左侧安全区', '注意拉冰，前往龙右侧安全区',
    ])
    jobs = json.loads((ROOT / 'dragon_nest_builder/data/modular/tables/job_map.json').read_text(encoding='utf-8'))
    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            if value is jobs:
                yield from value.keys()
            for item in value.values():
                yield from strings(item)
        elif isinstance(value, list):
            for item in value:
                yield from strings(item)
    result.update(f'注意{job}点名' for job in strings(jobs) if re.fullmatch(r'[\u4e00-\u9fff·]+', job))
    return sorted(result, key=lambda text: (text != '语音播报试听，请注意战斗机制', text))

def generate(limit=0):
    os.environ['HF_HOME'] = str(ROOT / 'work/huggingface')
    import torch
    import soundfile as sf
    from qwen_tts import Qwen3TTSModel
    torch.set_num_threads(4)
    expected = {'model.safetensors': '38b1d5971bdbd982b561cccec982669a53b0537c3cf5e9bd4778ed07bb2f5137',
                'speech_tokenizer/model.safetensors': '836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258'}
    for filename, digest in expected.items():
        with (MODEL / filename).open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                raise RuntimeError(f'Model checksum mismatch: {filename}')
    if not torch.cuda.is_available():
        raise RuntimeError('CUDA required for asset generation; no silent CPU inference fallback')
    print('GPU', torch.cuda.get_device_name(), flush=True)
    model = Qwen3TTSModel.from_pretrained(str(MODEL), device_map='cuda:0', dtype=torch.bfloat16, attn_implementation='sdpa')
    texts = phrases()
    manifest = {'model': REPO, 'revision': REVISION, 'license': 'Apache-2.0', 'voices': SPEAKERS, 'phrases': {}}
    for text in texts:
        key = hashlib.sha256(text.encode()).hexdigest()[:16]
        manifest['phrases'][text] = key
    tasks = [(speaker, text, OUT / speaker / f"{manifest['phrases'][text]}.wav")
             for text in sorted(texts[:limit] if limit else texts, key=len) for speaker in SPEAKERS
             if not (OUT / speaker / f"{manifest['phrases'][text]}.wav").is_file()]
    def batch_generate(batch):
        torch.manual_seed(42)
        started = time.perf_counter()
        try:
            wavs, sr = model.generate_custom_voice(
                text=[text.replace('砸地', '砸帝') for _, text, _ in batch], language=['Chinese'] * len(batch),
                speaker=[speaker.lower() for speaker, _, _ in batch],
                instruct=['用清晰、自然、简洁的普通话播报游戏战斗提示，语速稍快，不要添加任何内容。'] * len(batch),
                non_streaming_mode=True, max_new_tokens=256)
        except torch.cuda.OutOfMemoryError:
            if len(batch) == 1:
                raise
            torch.cuda.empty_cache()
            middle = len(batch) // 2
            batch_generate(batch[:middle]); batch_generate(batch[middle:])
            return
        for (speaker, text, path), wav in zip(batch, wavs, strict=True):
            path.parent.mkdir(parents=True, exist_ok=True)
            sf.write(str(path), wav, sr, subtype='PCM_16')
            print(speaker, text, 'audio_s', len(wav) / sr, flush=True)
        print('Batch', len(batch), 'generation_s', round(time.perf_counter() - started, 2), flush=True)
    for index in range(0, len(tasks), 8):
        batch_generate(tasks[index:index + 8])
    if not limit:
        (OUT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        from normalize_jump_voice import calibrate, cap_notice_levels
        cap_notice_levels(set(phrases()) - set(phrases(False)))
        calibrate(apply=True)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['download', 'generate', 'phrases'])
    parser.add_argument('--limit', type=int, default=0)
    args = parser.parse_args()
    if args.mode == 'download':
        download()
    elif args.mode == 'generate':
        generate(args.limit)
    else:
        print(json.dumps(phrases(), ensure_ascii=False, indent=2))
