<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue';
import { ShadowExplosionClock } from '../utils/shadowExplosion';
import { ClassicMechanics } from '../utils/classicMechanics';
import { HardMechanics } from '../utils/hardMechanics';
import { SandMechanics } from '../utils/sandMechanics';
import { publishOverlayEvent } from '../utils/overlayEventBus';
import { OVERLAY_EVENT_TOPICS } from '../utils/overlayEventProtocol';
import { SkillCountdownTracker, type SkillCountdown } from '../utils/skillCountdowns';
import { BUFF_OVERLAY_STORAGE_KEY, readBuffOverlaySettings } from '../utils/buffOverlaySettings';
import { voiceGroups, selectVoiceGroup, voiceGroupState } from '../utils/voicePreviews';
import { QWEN_VOICES, qwenVoiceKey, QwenVoicePlayer } from '../utils/qwenVoicePlayer';
import { LiveReminderTime } from '../utils/liveReminderTime';
import { simulatorApi } from '../services/api';
import { immediateMechanicText, hpAnnouncement, seaMechanicKey, voiceModes, classicAddAnnouncement } from '../utils/voiceAnnouncements';
import { VOICE_KEY, VOICE_RULES, normalizeVoiceSettings, voiceRule, seaBreathText, seaMechanicText, SeaReminderClock, monsterAppearanceText } from '../utils/voiceAnnouncements';
const props = defineProps<{ running: boolean; nest: string; live: boolean }>();
const open = ref(false);
const message = ref('');
const voiceChoices = QWEN_VOICES;
const previewIndex = ref<Record<string, number>>({});
const expandedGroups = ref<Record<string, boolean>>({
  'sea-nightmare:狮蝎': true, 'classic:食人魔': true,
  'hard:萝丝琳德': true, 'sand-hard:陌生的少女': true,
});
const groupsByNest = Object.fromEntries(['classic','hard','sand-hard','sea-nightmare'].map(key => [key,voiceGroups(key)]));
function enabledCount(keys: string[]) { return keys.filter(key => settings.value.skills.includes(key)).length; }
function selectGroup(keys: string[], event: Event) {
  Object.assign(settings.value, selectVoiceGroup(settings.value, keys, (event.target as HTMLInputElement).checked));
}
let saved = {};
try { saved = JSON.parse(localStorage.getItem(VOICE_KEY) || '{}'); } catch { /* damaged preferences use safe defaults */ }
const settings = ref(normalizeVoiceSettings(saved));
// A single-mode row is controlled solely by its main checkbox. Preserve old
// disabled choices when removing the now-redundant mode checkbox.
for (const rule of VOICE_RULES) {
  const modes = voiceModes(rule);
  if (modes.release === modes.warning) continue;
  const selected = modes.release ? settings.value.releaseSkills : settings.value.warningSkills;
  if (!selected.includes(rule.key)) {
    settings.value.skills = settings.value.skills.filter(key => key !== rule.key);
    selected.push(rule.key);
  }
}
const nestChoices = [
  { key: 'classic', name: '黑龙经典／黑龙123' },
  { key: 'hard', name: '黑龙硬核' },
  { key: 'sand-hard', name: '沙龙硬核' },
  { key: 'sea-nightmare', name: '梦魇海龙' },
];
settings.value.voice = qwenVoiceKey(settings.value.voice);
const supported = typeof window.AudioContext !== 'undefined';
const player = new QwenVoicePlayer(`${import.meta.env.BASE_URL}voices/qwen3/`);
function warmVoice() {
  if (supported) void player.warm(settings.value.voice).catch(e => { message.value = String(e.message || e); });
}
function openSettings() {
  open.value = true;
  if (supported) void player.unlock().catch(() => {});
  warmVoice();
}
function speak(text: string | string[], preview = false) {
  if (!supported) { message.value = '当前运行环境不支持音频播放。'; return; }
  const texts = Array.isArray(text) ? text : [text];
  message.value = texts.join('，');
  void player.play(texts, settings.value.voice, settings.value.volume, preview)
    .catch(e => { message.value = `语音播放失败：${e.message || e}`; });
}
let session = '', sequence = 0, busy = false, generation = 0;
const recent = new Map<string, number>();
const reminders = new SeaReminderClock();
const shadowClock = new ShadowExplosionClock();
const classicClock = new ClassicMechanics();
const hardClock = new HardMechanics();
const sandClock = new SandMechanics();
const visualClock = new SkillCountdownTracker();
const reminderTime = new LiveReminderTime();
let clockRunning = false;
let visualSettings = readBuffOverlaySettings(), visualSettingsRaw: string | null = null;
function countdownSettings() {
  const raw = localStorage.getItem(BUFF_OVERLAY_STORAGE_KEY);
  if (raw !== visualSettingsRaw) { visualSettingsRaw = raw; visualSettings = readBuffOverlaySettings(); }
  return visualSettings;
}
let countdownPublishedAt = 0, countdownSignature = '';
function publishCountdowns(rows: SkillCountdown[], force = false) {
  const now = Date.now(), signature = rows.map(row => row.key).join('|');
  if (!force && signature === countdownSignature && (!rows.length || now - countdownPublishedAt < 250)) return;
  countdownPublishedAt = now; countdownSignature = signature;
  void publishOverlayEvent(OVERLAY_EVENT_TOPICS.skillCountdowns, { rows, publishedAt: now }, ['skill-countdown-overlay']).catch(() => {});
}
function reset() { generation++; session = ''; sequence = 0; clockRunning = false; reminderTime.clear(); recent.clear(); reminders.clear(); shadowClock.clear(); classicClock.clear(); hardClock.clear(); sandClock.clear(); visualClock.clear(); publishCountdowns([], true); player.stop(); }
watch(settings, () => {
  try { localStorage.setItem(VOICE_KEY, JSON.stringify(settings.value)); } catch { message.value = '配置保存失败，存储空间不可用。'; }
  if (countdownSettings().showSkillCountdownOverlay) {
    // Changing/muting audio must not erase independently running visual timers.
    generation++; recent.clear(); reminders.clear(); shadowClock.clear(); classicClock.clear(); hardClock.clear(); sandClock.clear(); player.stop();
  } else reset();
}, { deep: true });
watch(() => settings.value.voice, warmVoice);
watch(() => [props.running, props.live, props.nest], reset);
async function poll() {
  if (busy || !props.running || !props.live) return;
  const visual = countdownSettings();
  if (!visual.showSkillCountdownOverlay) { visualClock.clear(); publishCountdowns([]); }
  if ((!settings.value.enabled || !supported) && !visual.showSkillCountdownOverlay) return;
  busy = true; const token = generation;
  try {
    const data = await simulatorApi.captureVoiceEvents();
    if (token !== generation) return;
    if (data.state !== 'running' || data.clockRunning === false) { reset(); return; }
    const rows = data.voiceEvents || [];
    const latest = Math.max(0, ...rows.map(r => r.sequence));
    // Never replay a backlog when enabling, switching records or mounting the page.
    if (session !== data.sessionId) {
      reminderTime.clear();
      reminderTime.observe(data.elapsedSeconds * 1000);
      clockRunning = true;
      reminders.clear(); shadowClock.clear(); classicClock.clear(); hardClock.clear(); sandClock.clear(); visualClock.clear(); session = data.sessionId; sequence = latest;
      publishCountdowns([], true);
      // Seed HP for phase-dependent cooldowns without replaying old casts.
      if (visual.showSkillCountdownOverlay) visualClock.observe(
        rows.filter(row => row.kind === 'boss_hp_state' && row.hpPercent !== undefined), props.nest);
      for (const row of rows) if (row.kind === 'boss_hp_state' && row.hpPercent !== undefined)
        reminders.health(row.templateId || 0, row.hpPercent, row.timestamp, row.source);
      for (const row of rows) classicClock.observe(row, props.nest, settings.value, true);
      for (const row of rows) hardClock.observe(row, props.nest, settings.value, true);
      for (const row of rows) sandClock.observe(row, props.nest, settings.value, true);
      return;
    }
    reminderTime.observe(data.elapsedSeconds * 1000);
    clockRunning = true;
    const hpTexts: string[] = [];
    const classicTexts: string[] = [];
    const hardTexts: string[] = [];
    const sandTexts: string[] = [];
    for (const row of rows) if (row.sequence > sequence && row.hpPercent === 0)
      sandClock.observe(row, props.nest, settings.value);
    for (const row of rows) if (row.sequence > sequence && Date.now()/1000-row.observedAt<=5 && data.elapsedSeconds*1000-row.timestamp<=6000)
      sandTexts.push(...sandClock.observe(row, props.nest, settings.value));
    for (const row of rows) if (row.sequence > sequence && row.hpPercent === 0)
      hardClock.observe(row, props.nest, settings.value);
    for (const row of rows) if (row.sequence > sequence && Date.now()/1000-row.observedAt<=5 && data.elapsedSeconds*1000-row.timestamp<=6000)
      hardTexts.push(...hardClock.observe(row, props.nest, settings.value));
    // Clear dead actors first, even if a delayed packet batch contains a cast.
    for (const row of rows) if (row.sequence > sequence && row.hpPercent === 0)
      classicClock.observe(row, props.nest, settings.value);
    for (const row of rows) if (row.sequence > sequence && Date.now()/1000-row.observedAt<=5 && data.elapsedSeconds*1000-row.timestamp<=6000)
      classicTexts.push(...classicClock.observe(row, props.nest, settings.value));
    if (visual.showSkillCountdownOverlay) visualClock.observe(rows.filter(row => row.sequence > sequence
      && ((row.kind === 'boss_hp_state' && row.hpPercent === 0) || row.kind === 'boss_entity_end'
        || (Date.now() / 1000 - row.observedAt <= 5 && data.elapsedSeconds * 1000 - row.timestamp <= 6000))), props.nest);
    // Apply deaths before dispatching this batch: an earlier action in the same
    // polling batch must not start audio after its source is already known dead.
    for (const row of rows) if (row.sequence > sequence &&
      ((row.kind === 'boss_hp_state' && row.hpPercent === 0) || row.kind === 'boss_entity_end'))
      reminders.health(row.templateId || 0, 0, row.timestamp, row.source);
    for (const row of rows) {
      if (row.sequence <= sequence) continue;
      sequence = row.sequence;
      if (!settings.value.enabled || !supported) continue;
      // Cleanup is authoritative even when the UI was paused and this event is old.
      if ((row.kind === 'boss_hp_state' && row.hpPercent === 0) || row.kind === 'boss_entity_end') {
        if (row.templateId === 4237023) shadowClock.clear();
        reminders.health(row.templateId || 0, 0, row.timestamp, row.source);
        continue;
      }
      if (Date.now() / 1000 - row.observedAt > 5 || data.elapsedSeconds * 1000 - row.timestamp > 6000) continue;
      if (row.kind === 'monster_spawn' && row.templateId === 4237024) {
        shadowClock.rescued(row.source, row.timestamp);
        continue;
      }
      if (row.kind === 'shadow_start' && row.skill === 325478 && row.templateId === 4237025) {
        const shadowRule = voiceRule(row.skill, props.nest, settings.value);
        if (shadowRule && settings.value.warningSkills.includes(shadowRule.key)) shadowClock.started(row.source, row.timestamp);
        continue;
      }
      if (reminders.isDead(row.source)) continue;
      if (row.kind === 'boss_hp_state' || row.kind === 'boss_hp_threshold') {
        if (row.hpPercent !== undefined) reminders.health(row.templateId || 0, row.hpPercent, row.timestamp, row.source);
        if (row.kind === 'boss_hp_threshold') {
          const text = hpAnnouncement(row.templateId || 0, row.threshold || 0, props.nest, settings.value);
          if (text) hpTexts.push(text);
        }
        continue;
      }
      const appearanceText = monsterAppearanceText(row.kind, row.templateId, props.nest, settings.value);
      if (appearanceText) {
        const key = `spawn:${row.templateId}`;
        if (row.timestamp - (recent.get(key) ?? -Infinity) < 10000) continue;
        recent.set(key, row.timestamp);
        speak(appearanceText);
        continue;
      }
      const rule = voiceRule(row.skill, props.nest, settings.value);
      if (!rule) continue;
      const immediateText = immediateMechanicText(row.skill);
      if (immediateText) {
        const key = `${row.source}:${row.skill}`;
        if (row.timestamp - (recent.get(key) ?? -Infinity) < 10000) continue;
        recent.set(key, row.timestamp);
        if (settings.value.releaseSkills.includes(rule.key)) speak(immediateText);
        continue;
      }
      if (seaMechanicText(row.skill)) {
        if (row.confirmedEndTimestamp !== undefined) {
          reminders.ended(row.skill, row.confirmedEndTimestamp);
          continue;
        }
        const key = `${row.source}:${seaMechanicKey(row.skill)}`;
        if (row.timestamp - (recent.get(key) ?? -Infinity) < 10000) continue;
        recent.set(key, row.timestamp);
        if (settings.value.warningSkills.includes(rule.key)) reminders.started(row.skill, row.timestamp, row.source);
        if (settings.value.releaseSkills.includes(rule.key)) speak(seaMechanicText(row.skill));
        continue;
      }
      if (rule.key === 'sea-breath') {
        const text = seaBreathText(row.skill, row.templateId);
        if (!text) continue;
        const key = `${row.source}:${row.templateId}:${row.skill}`;
        if (row.timestamp - (recent.get(key) ?? -Infinity) < 10000) continue;
        recent.set(key, row.timestamp);
        if (settings.value.releaseSkills.includes(rule.key)) speak(text);
        continue;
      }
      const recipients = (row.recipients || []).filter(target => {
        const key = `${row.source}:${rule.key}:${target.id}`;
        if (row.timestamp - (recent.get(key) ?? -Infinity) < 10000) return false;
        recent.set(key, row.timestamp);
        return true;
      });
      if (recipients.length && settings.value.releaseSkills.includes(rule.key)) speak([...new Set(recipients.map(t => t.job))].map(job => `注意${job}点名`));
    }
    const texts = [...new Set([...classicTexts, ...hardTexts, ...sandTexts, ...hpTexts])];
    if (settings.value.enabled && supported && texts.length) speak(texts);
    tickReminders();
  } catch { message.value = '暂时无法获取实时事件，将自动重试。'; }
  finally { busy = false; }
}
function tickReminders() {
  if (!clockRunning || !props.running || !props.live) return;
  const now = reminderTime.now();
  if (now === undefined) return;
  if (settings.value.enabled && supported) {
    const texts = [...new Set([
      ...reminders.due(now), ...classicClock.due(now, settings.value),
      ...hardClock.due(now, settings.value), ...sandClock.due(now, settings.value),
      ...shadowClock.due(now),
    ])];
    if (texts.length) speak(texts);
  }
  const visual = countdownSettings();
  publishCountdowns(visual.showSkillCountdownOverlay
    ? visualClock.countdowns(now, visual.selectedSkillCountdowns) : []);
}
let timer: number | undefined;
let reminderTimer: number | undefined;
onMounted(() => {
  if (settings.value.enabled) warmVoice();
  timer = window.setInterval(poll, 100);
  reminderTimer = window.setInterval(tickReminders, 100);
});
onUnmounted(() => { reset(); window.clearInterval(timer); window.clearInterval(reminderTimer); player.dispose(); });
</script>

<template>
  <button class="frost-entry voice-entry" type="button" @click="openSettings">语音播报<span class="voice-entry-state">{{ settings.enabled ? '开' : '关' }}</span></button>
  <Teleport to="body">
    <div v-if="open" class="voice-backdrop" @click.self="open = false" @keydown.esc="open = false">
      <section class="voice-dialog" role="dialog" aria-modal="true" aria-label="语音播报设置">
        <header><h2>语音播报</h2><button class="secondary-button" @click="open = false">关闭</button></header>
        <label><input v-model="settings.enabled" type="checkbox" :disabled="!supported"> 开启实时语音播报</label>
        <p>配置自动保存。仅实时统计播报，历史抓包不播报。只播报已识别目标的职业，例如“注意影舞者点名”；职业未知时不猜测。同职业队员无法仅靠语音区分。不保证提前预警。</p>
        <fieldset><legend>巢穴（多选）</legend>
          <label v-for="nest in nestChoices" :key="nest.key"><input v-model="settings.nests" type="checkbox" :value="nest.key"> {{ nest.name }}</label>
        </fieldset>
        <fieldset v-for="nest in nestChoices.filter(n => settings.nests.includes(n.key))" :key="nest.key"><legend>{{ nest.name }} · 技能（多选）</legend>
          <p class="voice-help">每条技能可分别开启释放播报和预警播报（冷却／血量）；下拉框仅选择试听内容。折叠不会关闭播报。</p>
          <section v-for="group in groupsByNest[nest.key]" :key="group.key" :class="{ 'voice-boss-group': group.name }">
            <div v-if="group.name" class="voice-boss-heading">
              <button type="button" class="voice-boss-toggle" :aria-expanded="!!expandedGroups[group.key]" :aria-controls="`voice-group-${group.key}`" @click="expandedGroups[group.key] = !expandedGroups[group.key]">
                <span aria-hidden="true">{{ expandedGroups[group.key] ? '▾' : '▸' }}</span>
                <strong>{{ group.name }}</strong>
                <span class="voice-count">已开启 {{ enabledCount(group.rows.map(r => r.key)) }}/{{ group.rows.length }}</span>
              </button>
              <label class="voice-group-all"><input type="checkbox" :aria-label="`${group.name}全部播报`" :checked="voiceGroupState(settings,group.rows.map(r => r.key)).checked" :indeterminate="voiceGroupState(settings,group.rows.map(r => r.key)).indeterminate" @change="selectGroup(group.rows.map(r => r.key), $event)">全选</label>
            </div>
            <div v-show="!group.name || expandedGroups[group.key]" :id="`voice-group-${group.key}`" class="voice-group-content">
          <div v-for="rule in group.rows" :key="rule.key" class="voice-rule">
            <label><input v-model="settings.skills" type="checkbox" :value="rule.key"> {{ rule.label }}
              <small v-if="rule.key === 'sea-manticore-flight-slam'" class="voice-rule-note">听到“跳”后 1s 左右起跳</small>
              <small v-if="rule.key === 'sea-apocalypse-rotating-laser'" class="voice-rule-note">两个旋转方向均播报，共用冷却提醒</small>
            </label>
            <div class="voice-modes">
              <template v-if="voiceModes(rule).release && voiceModes(rule).warning">
              <label v-if="voiceModes(rule).release"><input v-model="settings.releaseSkills" type="checkbox" :value="rule.key" :disabled="!settings.skills.includes(rule.key)"> {{ ['sea-minotaur','classic-bomb-add'].includes(rule.key) ? '出现播报' : '技能释放' }}</label>
              <label v-if="voiceModes(rule).warning"><input v-model="settings.warningSkills" type="checkbox" :value="rule.key" :disabled="!settings.skills.includes(rule.key)"> {{ rule.key === 'classic-bomb-add' ? '全部消失播报' : rule.key === 'sea-minotaur' ? '即将出现' : ['sea-breath','sea-hp-flight'].includes(rule.key) ? '血量预警' : '冷却／血量预警' }}</label>
              </template>
            </div>
            <div class="voice-preview">
              <select v-if="rule.previews.length > 1" :value="previewIndex[rule.key] ?? 0" :aria-label="`${rule.name}提示内容`" @change="previewIndex[rule.key] = Number(($event.target as HTMLSelectElement).value)">
                <option v-for="(item,index) in rule.previews" :key="item.label" :value="index">{{ item.label }}</option>
              </select>
              <span v-else class="voice-preview-text">{{ rule.previews[0].text }}</span>
              <button class="secondary-button" :aria-label="`试听${rule.name}`" @click="speak(rule.previews[previewIndex[rule.key] ?? 0].text, true)">试听</button>
            </div>
          </div>
            </div>
          </section>
        </fieldset>
        <label class="voice-field">中文音色<select v-model="settings.voice"><option v-if="!voiceChoices.length" value="">暂无中文音色</option><option v-for="v in voiceChoices" :key="v.key" :value="v.key">{{ v.label }}</option></select></label>
        <p>四种 Qwen3-TTS 音色，默认 Vivian。提示语随程序保存，战斗中离线播放，无需 Azure 账号或安装 Windows 音色。新机制提示语需要随版本更新音频。</p>
        <details v-if="settings.nests.includes('sea-nightmare')" class="voice-mechanic-notes">
          <summary>查看播报规则</summary>
        <p>冰弹播报统一以“注意拉冰”开头。第一阶段：右向冰弹 → 注意拉冰，三连啄后前往右侧安全区；左向冰弹 → 注意拉冰，三连啄后前往左侧安全区。其他已识别阶段保留原方向规则，阶段未知不播报。按已识别的技能或已验证动作触发；阶段未知不猜测方向。</p>
        <p>均从技能释放时计时：地面激光35秒；召唤触手75秒，天启血量低于20%改为55秒，60%时另报“召唤触手已好”；六芒星75秒，低于20%改为55秒，42%时另报“六芒星已好”；连续追踪85秒。冰捆115秒，海龙三阶段血量到30%改为55秒，61%另报“冰捆已好”。跨过血量阈值时，按原释放时间重算；缩短后的提醒时间已到则立即提示一次。龙息50秒、冰眼55秒；狮蝎飞天85秒，旋风／砸地／黑洞55秒。三阶段62/52/42/32/22%提醒拉冰；66/56%提醒躲龙息消灭暗影；46/36/26/16/6%提醒上炮。地狱犬85/65/45/25%提醒“牛头怪即将出现”。血量按实体满血计算。“已好”是预估提醒，不是服务端冷却。</p>
        </details>
        <details v-if="settings.nests.includes('hard')" class="voice-mechanic-notes">
          <summary>黑龙硬核 · 相关播报规则</summary>
          <p>甩尾（734009／735009）释放后，一阶段55秒、二阶段65秒提醒“甩尾已好”。二阶段首次点名在进入阶段后55秒提醒，首次上升重力65秒提醒；后续点名以734017释放为起点，115秒提醒“点名已好”；上升重力以735010／735011释放为起点，起手播报“注意上升重力”，115秒提醒“上升重力已好”。点名目标的职业提示仍按实际识别的状态播报。</p>
          <p>“已好”是提前准备的预估提醒，不代表服务端冷却已结束，也不保证Boss马上释放。</p>
        </details>
        <details v-if="settings.nests.includes('sand-hard')" class="voice-mechanic-notes">
          <summary>沙龙硬核 · 相关播报规则</summary>
          <p>石头人三连砸、狮子三连冲首次在血量下降跨过97%时提醒“已好”；蝎子旋转仍为92%。后续均从对应技能释放后25秒提醒。首次读到的血量仅建立基线，不补播已经跨过的血线。</p>
          <p>东西安全区按实际起手指路：730009播报“注意砸地，前往9点”；730010播报“注意砸地，前往3点”。冷却预警仍提示“叹息之墙已好，前往点位”，未识别起手前不预猜方向。“已好”是预估提醒，不保证Boss马上释放。</p>
        </details>
        <label class="voice-field">音量 {{ Math.round(settings.volume * 100) }}%<input v-model.number="settings.volume" type="range" min="0" max="1" step="0.05"></label>
        <button class="secondary-button" @click="speak('语音播报试听，请注意战斗机制', true)">试听当前音色与音量</button>
        <p role="status">{{ message }}</p>
      </section>
    </div>
  </Teleport>
</template>
<style scoped>
.voice-entry-state{display:inline-flex;align-items:center;justify-content:center;min-width:20px;height:16px;margin-left:7px;padding:0 6px;border-radius:999px;background:rgba(94,134,168,.14);color:var(--frost-ink-2,#53616f);font-size:9.5px;font-weight:600;letter-spacing:.02em}
.voice-preview-text{flex:1;min-width:0;align-self:center;overflow-wrap:anywhere}
.voice-backdrop{position:fixed;inset:0;z-index:12000;background:#102b3766;display:grid;place-items:center;padding:24px}
.voice-dialog{background:var(--panel-bg,#fff);color:#23434b;border-radius:16px;padding:24px;width:min(960px,100%);max-height:88vh;overflow:auto;box-shadow:0 16px 70px #102b3740;box-sizing:border-box}
.voice-rule-note{display:block;margin:4px 0 0 22px;font-size:13px;line-height:1.5;color:#46636b}
.voice-boss-group{border:1px solid #d5e4e5;border-radius:10px;overflow:hidden;margin-top:10px}.voice-boss-heading{display:flex;align-items:center;gap:12px;padding:10px 12px;background:#eff7f7}.voice-boss-toggle{display:flex;align-items:center;gap:10px;flex:1;min-width:0;border:0;background:transparent;text-align:left;color:#23434b;padding:4px;cursor:pointer;font:inherit}.voice-boss-toggle:focus-visible{outline:2px solid #087f83;outline-offset:2px}.voice-count{font-size:12px;color:#46636b}.voice-group-all{display:flex;align-items:center;gap:5px;white-space:nowrap;font-size:13px}.voice-group-content{padding:0 10px}.voice-mechanic-notes{margin:14px 0;padding:10px 12px;border:1px solid #d5e4e5;border-radius:8px}.voice-mechanic-notes summary{cursor:pointer;color:#28525a;font-size:14px}
.voice-rule{display:grid!important;grid-template-columns:minmax(170px,1fr) minmax(135px,.8fr) minmax(0,1.5fr);border-bottom:1px solid #e4eeee;padding:10px 0!important}.voice-rule:last-child{border-bottom:0}.voice-modes{display:flex;flex-wrap:wrap;gap:6px 12px;font-size:13px}.voice-modes label{display:flex;align-items:center;gap:4px}.voice-preview{display:flex;gap:8px;min-width:0}.voice-preview select{flex:1;min-width:0;width:0;color:#23434b;border:1px solid #b8cdcf;border-radius:6px;background:var(--panel-bg,#fff);text-overflow:ellipsis}.voice-preview button{flex:none;white-space:nowrap}.voice-help{margin:0 0 8px}@media(max-width:700px){.voice-rule{grid-template-columns:1fr}.voice-backdrop{padding:10px}.voice-dialog{padding:16px}}
header,.voice-rule{display:flex;align-items:center;justify-content:space-between;gap:12px}h2{margin:0 0 16px}p{font-size:13px;line-height:1.7;color:#46636b}fieldset{border:1px solid #cadcde;border-radius:8px;margin:16px 0;padding:12px}fieldset>label{margin-right:20px}.voice-rule{padding:5px 0}.voice-field{display:flex;align-items:center;gap:16px;margin:16px 0}.voice-field select,.voice-field input{flex:1;min-width:0}select{padding:8px}
</style>
