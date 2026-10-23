"""Offline PCM calibration; never adds work to the capture/playback threads."""
import array
import hashlib
import json
import math
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'frontend/public/voices/qwen3'


def read(path):
    with wave.open(str(path), 'rb') as stream:
        assert stream.getsampwidth() == 2 and stream.getnchannels() == 1
        return stream.getparams(), array.array('h', stream.readframes(stream.getnframes()))


def rms(samples):
    return math.sqrt(sum(x * x for x in samples) / len(samples))


def cap_notice_levels(texts):
    """Attenuate only newly introduced notices; retain originals and cache-bust URLs."""
    manifest = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
    jump = hashlib.sha256('跳'.encode()).hexdigest()[:16]
    for text in sorted(texts):
        original = hashlib.sha256(text.encode()).hexdigest()[:16]
        output_id = hashlib.sha256((text + ':notice-level-v1').encode()).hexdigest()[:16]
        for voice in manifest['voices']:
            _, cue = read(ROOT / voice / f'{jump}.wav')
            params, samples = read(ROOT / voice / f'{original}.wav')
            # Reserve 1 dB peak headroom and the jump cue's 0.5 dB RMS advantage,
            # plus rounding margin. No limiter, clipping or timing changes.
            target = rms(cue) * (32767 * 10 ** (-1.1 / 20) / max(abs(x) for x in cue)) / 10 ** (0.5 / 20)
            gain = min(1, target / max(rms(samples), 1))
            output = array.array('h', (round(x * gain) for x in samples))
            with wave.open(str(ROOT / voice / f'{output_id}.wav'), 'wb') as stream:
                stream.setparams(params)
                stream.writeframes(output.tobytes())
        manifest['phrases'][text] = output_id
    (ROOT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')


def calibrate(apply=False):
    manifest = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
    original = hashlib.sha256('跳'.encode()).hexdigest()[:16]
    calibrated = hashlib.sha256('跳:loudness-v1'.encode()).hexdigest()[:16]
    report = []
    for voice in manifest['voices']:
        params, samples = read(ROOT / voice / f'{original}.wav')
        reference = max(rms(read(ROOT / voice / f'{key}.wav')[1])
                        for text, key in manifest['phrases'].items() if text != '跳')
        # At least the loudest phrase RMS, with 0.5 dB extra for the short cue.
        gain = max(1, reference * 10 ** (0.5 / 20) / rms(samples))
        peak = max(abs(x) for x in samples)
        if peak * gain > 32767 * 10 ** (-1 / 20):
            raise ValueError(f'{voice}: target exceeds -1 dBFS headroom; do not clip')
        output = array.array('h', (round(x * gain) for x in samples))
        report.append(dict(voice=voice, gainDb=round(20 * math.log10(gain), 2),
                           beforeDb=round(20 * math.log10(rms(samples) / 32768), 2),
                           afterDb=round(20 * math.log10(rms(output) / 32768), 2),
                           referenceDb=round(20 * math.log10(reference / 32768), 2)))
        if apply:
            with wave.open(str(ROOT / voice / f'{calibrated}.wav'), 'wb') as stream:
                stream.setparams(params)
                stream.writeframes(output.tobytes())
    if apply:
        manifest['phrases']['跳'] = calibrated
        (ROOT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    import sys
    print(json.dumps(calibrate('--apply' in sys.argv), ensure_ascii=False, indent=2))
