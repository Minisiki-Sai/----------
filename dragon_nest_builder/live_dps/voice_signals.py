"""Bounded immediate notifications from the ordered decoder, not combat snapshots."""
from collections import deque
from threading import Lock
import time

CLAW = frozenset({34849, 734017, 735017})
ACTION_ONLY = frozenset({325430, 325431, 325423, 50411, 50460, 50408, 50409, 50407, 50456, 50457, 50458, 50459, 325456})
MECHANICS = frozenset({325450, 325447, 50318, 50317, 50311, 50313})
CLASSIC_ACTIONS = frozenset({34715,34714,34738,34743,34734,34737,34771,34780,34781,34773,34798,34801,34850,34852,34853,34854,34856,34857,34858,34859,34879,34880,34860,34864,34849,34848,34876,34877})
# Additional starts only update visual shared cooldowns; they add no speech.
CLASSIC_ACTIONS = CLASSIC_ACTIONS | {34739}
CLASSIC_THRESHOLDS = {'503611':[78,52], '503621':[78,58,28], '503652':[49],
                      '503661':[74], '503681':[78,72,42,32,25],
                      '503682':[78,72,42,32,25], '503696':[78,72,42,32,25]}
HARD_ACTIONS = frozenset({733027,733036,734019,734020,733038,733039,734006,735006,734009,735009,735010,735011,734017,734008,735008,732001,732002,732003,732004,732005})
HARD_TEMPLATES = frozenset({4180001,4181001,4181005,4181006,4181007,4181008,4181009})
HARD_THRESHOLDS = {'4181001':[81], '4181009':[]}
SAND_ACTIONS = frozenset({729101,729102,729105,730021,730020,729085,730022,729058,729098,729100,729099,730001,730002,730003,730004,730025,730009,730010,729063,729064,729065,729066})
SAND_ACTIONS = SAND_ACTIONS | {729083}
SAND_TEMPLATES = frozenset({4150064,4150059,4150057,4150058,4150050,4160001})
SAND_THRESHOLDS = {'4150059':[97], '4150057':[92], '4150058':[97], '4150050':[86], '4160001':[26]}


class VoiceSignalFeed:
    def __init__(self):
        self._lock = Lock()
        self._rows = deque(maxlen=128)
        self._sequence = 0
        self._recent = {}
        self._health = {}

    def publish(self, row):
        state = self._health.get(row['source'])
        if state and state[0] == 0 and row.get('kind') != 'boss_hp_state':
            return
        # Keep the wire ID in the event, but deduplicate the shared laser mechanic.
        mechanic = 50408 if row['skill'] == 50409 else 50457 if row['skill'] in (50458,50459) else row['skill']
        key = (row["source"], mechanic, row.get("templateId"), row.get('kind'), row.get('threshold'), row.get('healthBand'),
               tuple(p["id"] for p in row.get("recipients", ())))
        stamp = row["timestamp"]
        with self._lock:
            previous = self._recent.get(key)
            if row.get('kind') != 'boss_hp_state' and previous is not None and 0 <= stamp - previous < 10000:
                return
            self._recent[key] = stamp
            if len(self._recent) > 512:
                self._recent = {k: t for k, t in self._recent.items() if stamp - t < 60000}
            self._sequence += 1
            self._rows.append(dict(row, sequence=self._sequence, observedAt=time.time(),
                                   publishedMonotonic=time.perf_counter()))

    def snapshot(self):
        with self._lock:
            return list(self._rows)

    def health(self, event):
        if getattr(event, "part_id", 0):
            return  # Part destruction is not Boss death or a main-HP threshold.
        template = str(event.target_id).rsplit('@', 1)[-1]
        # Death must reach the voice channel for EVERY boss, not just the two
        # templates with percentage-triggered mechanics. No HP denominator needed.
        previous_state = self._health.get(event.target_id)
        if previous_state and event.timestamp_ms < previous_state[2]:
            return
        if previous_state and previous_state[0] == 0:
            return  # Late HP/status packets cannot revive this encounter entity.
        if event.current_hp <= 0 and template.isdigit():
            if previous_state and previous_state[0] == 0:
                return
            self._health[event.target_id] = (0, set(), event.timestamp_ms)
            self.publish(dict(source=event.target_id, skill=0,
                timestamp=event.timestamp_ms, templateId=int(template),
                hpPercent=0, kind='boss_hp_state', healthBand='dead'))
            return
        # Share the audited entity denominator with statistics; never use a
        # stage damage budget or a separately hard-coded voice maximum.
        from .nest_hp_reference import profiles
        maximum = next((p['entities'].get(template, {}).get('full_hp')
                        for p in profiles() if p['label'] == '梦魇海龙'), None) if template in {'4235447','4237023','4237012'} else None
        if template in CLASSIC_THRESHOLDS:
            maximum = next((p['entities'].get(template, {}).get('full_hp')
                            for p in profiles() if p['label'] == '黑龙经典'), None)
        if template in HARD_THRESHOLDS:
            maximum = next((p['entities'].get(template, {}).get('full_hp')
                            for p in profiles() if p['label'] == '黑龙硬核'), None)
        if template in SAND_THRESHOLDS:
            maximum = next((p['entities'].get(template, {}).get('full_hp')
                            for p in profiles() if p['label'] == '沙龙硬核'), None)
        if maximum is None or not 0 <= event.current_hp <= maximum:
            return
        percent = event.current_hp * 100 / maximum
        key = event.target_id
        previous, seen, stamp = self._health.get(key, (None, set(), -1))
        if event.timestamp_ms < stamp:
            return
        band = (percent < 20 if template == '4235447' else percent <= 30)
        old_band = None if previous is None else (previous < 20 if template == '4235447' else previous <= 30)
        if template in CLASSIC_THRESHOLDS or template in HARD_THRESHOLDS or template in SAND_THRESHOLDS:
            # Bound live HP traffic to integer bands, with an explicit strict-49
            # boundary for the Punisher's safe-output announcements.
            band = (int(percent), percent < 49)
            old_band = None if previous is None else (int(previous), previous < 49)
        base = dict(source=key, skill=0, timestamp=event.timestamp_ms, templateId=int(template), hpPercent=percent)
        if previous is None or band != old_band or (percent == 0 and previous != 0):
            self.publish(dict(base, kind='boss_hp_state', healthBand=f'{band}:{percent == 0}'))
        thresholds = ([60,42] if template == '4235447' else [85,65,45,25] if template == '4237012'
                      else [66,62,61,56,52,46,42,36,32,26,22,16,6])
        thresholds = CLASSIC_THRESHOLDS.get(template, thresholds)
        thresholds = HARD_THRESHOLDS.get(template, thresholds)
        thresholds = SAND_THRESHOLDS.get(template, thresholds)
        # First observation establishes a baseline: never replay already passed mechanics.
        if previous is not None and percent > 0:
            for threshold in thresholds:
                if previous > threshold >= percent and threshold not in seen:
                    seen.add(threshold)
                    self.publish(dict(base, kind='boss_hp_threshold', threshold=threshold))
        else:
            seen.update(t for t in thresholds if t >= percent)
        self._health[key] = (percent, seen, event.timestamp_ms)
        if len(self._health) > 128:
            self._health = {k:v for k,v in self._health.items() if event.timestamp_ms-v[2] < 600000}


def publish_evidence(host, evidence):
    feed = getattr(host, "voice_signal_feed", None)
    if feed is None:
        return
    for item in evidence:
        skill = item.skill_index
        kind = item.evidence_kind
        recipients = []
        if skill in CLAW and kind == "status":
            target = item.affected_target_id
            try:
                actor = int(target, 16)
            except (ValueError, TypeError):
                continue
            context = host.actor_jobs.get(actor, ("", 0))
            job = context[0]
            if not job or job in {"未知", "未知职业"} or job.startswith("Boss"):
                continue
            recipients = [{"id": target, "job": job}]
        elif skill in SAND_ACTIONS and item.template_id in SAND_TEMPLATES and kind in {"protocol_action", "structured_action"}:
            pass
        elif skill in HARD_ACTIONS and item.template_id in HARD_TEMPLATES and kind in {"protocol_action", "structured_action"}:
            pass
        elif skill in CLASSIC_ACTIONS and item.template_id in {503611,503621,503652,503661,503681,503682,503696} and kind in {"protocol_action", "structured_action"}:
            pass
        elif skill in ACTION_ONLY and kind in {"protocol_action", "structured_action"}:
            pass
        elif skill in MECHANICS and kind in {"protocol_action", "structured_action"}:
            pass
        else:
            continue
        feed.publish({"skill": skill, "source": item.source_id,
                      "timestamp": item.timestamp_ms, "templateId": item.template_id,
                      "evidenceKind": kind, "recipients": recipients})
