import fs from 'node:fs';
import path from 'node:path';
import { SeaReminderClock, hpAnnouncement, normalizeVoiceSettings, seaMechanicText } from '../src/utils/voiceAnnouncements.ts';
const dir=path.resolve('../outputs/verification/voice_latency_1019');
const settings=normalizeVoiceSettings({enabled:true});
const reports=[];
for(const file of fs.readdirSync(dir).filter(f=>f.startsWith('1019')&&f.endsWith('.json'))) {
  const data=JSON.parse(fs.readFileSync(path.join(dir,file),'utf8'));
  const rows=data.signals.slice().sort((a:any,b:any)=>a.timestamp-b.timestamp);
  const clock=new SeaReminderClock();let i=0;const batches=[];let total=0;
  for(let t=0;t<=(rows.at(-1)?.timestamp||0)+120000;t+=100) {
    const hp=[];
    while(i<rows.length&&rows[i].timestamp<=t) {
      const row=rows[i++];
      if(row.kind==='boss_hp_state'||row.kind==='boss_hp_threshold') {
        clock.health(row.templateId||0,row.hpPercent,row.timestamp,row.source);
        if(row.kind==='boss_hp_threshold') {
          const text=hpAnnouncement(row.templateId,row.threshold,'梦魇海龙',settings);
          if(text)hp.push(text);
        }
      }else if(seaMechanicText(row.skill))clock.started(row.skill,row.timestamp,row.source);
    }
    const texts=[...new Set([...hp,...clock.due(t)])];
    total+=texts.length;
    if(texts.length>1)batches.push({seconds:t/1000,texts});
  }
  reports.push({file,total,batches});
}
fs.writeFileSync(path.join(dir,'batches.json'),JSON.stringify(reports,null,2));
console.log(JSON.stringify(reports.filter(r=>r.batches.length)));
