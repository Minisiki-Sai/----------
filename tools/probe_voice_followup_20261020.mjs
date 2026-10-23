// Diagnostic only: invoke production timing/player code with virtual clocks
// and a silent AudioContext stub. Does not alter settings or run live capture.
import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { QwenVoicePlayer } from '../frontend/src/utils/qwenVoicePlayer.ts';
import { SeaReminderClock } from '../frontend/src/utils/voiceAnnouncements.ts';

const starts = [];
class SilentContext {
  currentTime = 10;
  state = 'running';
  destination = {};
  async resume() {}
  async close() {}
  async decodeAudioData() { return { duration: 1 }; }
  createGain() { return { gain: { value: 0 }, connect() {}, disconnect() {} }; }
  createBufferSource() {
    return { onended: null, connect() {}, disconnect() {}, stop() {},
      start(at) { starts.push(at); } };
  }
}
globalThis.AudioContext = SilentContext;
globalThis.fetch = async url => url.endsWith('manifest.json')
  ? { ok: true, json: async () => ({ phrases: { '六芒星已好': 'a', '马上拉冰': 'b' } }) }
  : { ok: true, arrayBuffer: async () => new ArrayBuffer(0) };
const player = new QwenVoicePlayer('/diagnostic/');
await player.warm('Vivian');
await player.play(['六芒星已好', '马上拉冰'], 'Vivian', 0, false);
const batch = [...starts];
starts.length = 0;
await Promise.all([
  player.play(['六芒星已好'], 'Vivian', 0, false),
  player.play(['马上拉冰'], 'Vivian', 0, false),
]);
const separate = [...starts];
assert.deepEqual(batch, [10, 11]);
assert.deepEqual(separate, [10, 10]);
player.dispose();

function frozenProducer(stallMs, extrapolate) {
  const clock = new SeaReminderClock();
  clock.started(50456, 100000, 'apocalypse', 4235447);
  // Last status sample is fight t=174s; tentacle warning is due at t=175s.
  // Only the producer stalls. The consumer is deliberately still responsive.
  const emitted = [];
  for (let wall = 0; wall <= stallMs + 100; wall += 100) {
    const elapsed = !extrapolate && wall < stallMs ? 174000 : 174000 + wall;
    for (const text of clock.due(elapsed)) emitted.push({ wallMs: wall, text });
  }
  return emitted;
}
const clockCases = [2200, 8000].map(stallMs => ({
  producerStallMs: stallMs,
  current: frozenProducer(stallMs, false),
  independentAnchor: frozenProducer(stallMs, true),
}));
assert.equal(clockCases[0].current[0].wallMs, 2200);
assert.equal(clockCases[0].independentAnchor[0].wallMs, 1000);
assert.equal(clockCases[1].current.length, 0);
assert.equal(clockCases[1].independentAnchor[0].wallMs, 1000);
const result = {
  scope: 'Controlled scheduling proof, not a measurement of yesterday audible latency',
  batchStartsSeconds: batch, separateStartsSeconds: separate, clockCases,
};
const out = new URL('../outputs/verification/voice_latency_followup_20261020/', import.meta.url);
mkdirSync(out, { recursive: true });
writeFileSync(new URL('controlled.json', out), JSON.stringify(result, null, 2));
console.log(JSON.stringify(result, null, 2));
