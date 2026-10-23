import assert from "node:assert/strict";
import {readFile,writeFile} from "node:fs/promises";
import {sql,api,environment} from "./cloudbase_analytics_admin.mjs";
const root=new URL("../outputs/verification/capture_privacy_20261020/",import.meta.url);
const hashes=JSON.parse(await readFile(new URL("captures.json",root),"utf8")).map(r=>r[0]);
const records=JSON.parse(await readFile(new URL("apply-affected-records.json",root),"utf8"));
assert.equal(hashes.length,33);
assert.equal(records.length,263);
const literal=hashes.map(h=>`'${h}'`).join(",");
const rows=r=>(r.Rows||[]).map(JSON.parse);
const checks=[];
const unguarded=rows(await sql(`select p.proname from pg_proc p
join pg_namespace n on n.oid=p.pronamespace where n.nspname='public'
and p.proname like 'get_%' and p.prosrc ~ '\\mpublic\\.(team_scores|team_score_ranking_facts|team_score_capture_files)\\M';`));
assert.deepEqual(unguarded,[],"A read RPC bypasses capture privacy; re-audit/reapply migration 047");
checks.push({check:"all score read RPCs use filtered driving relations",unguarded:0});
const summary=rows(await sql(`select
 (select count(*) from score_privacy.hidden_captures where capture_hash in(${literal})),
 (select count(*) from public.team_scores where capture_hash in(${literal})),
 (select count(*) from score_public.team_scores where capture_hash in(${literal})),
 (select count(*) from score_public.team_score_ranking_facts where capture_hash in(${literal}));`))[0].map(Number);
assert.deepEqual(summary,[33,263,0,0]);
checks.push({check:"private retained / public excluded",summary});
// Verify every hidden row, not only the sample used in the rollback dry run.
for(let start=0;start<records.length;start+=20){
 const ids=records.slice(start,start+20).map(r=>r[0]);
 assert.ok(ids.every(id=>/^\d+$/.test(String(id))));
 const result=rows(await sql(`select count(*),
 count(*) filter(where public.get_profession_score_history_detail(s.job,s.row_fingerprint) is not null),
 count(*) filter(where i.character_key is not null and public.get_character_score_history_detail(i.character_key,s.row_fingerprint) is not null),
 count(*) filter(where public.get_team_score_roster(s.row_fingerprint)<>'[]'::jsonb),
 count(*) filter(where public.get_score_damage_overview_v1(s.row_fingerprint) is not null
 or public.get_team_boss_debuffs_v1(s.row_fingerprint) is not null)
 from public.team_scores s left join public.team_score_character_identities i on i.team_score_id=s.id
 where s.id in(${ids.join(",")});`))[0].map(Number);
 assert.deepEqual(result,[ids.length,0,0,0,0]);
 console.log(JSON.stringify({checkedDetails:Math.min(start+20,records.length),total:records.length}));
}
checks.push({check:"all detail, roster, damage and boss archive RPCs",rows:records.length,leaks:0});
const conf=await api("scf","GetFunction",{FunctionName:"team-score-sync",Namespace:environment});
const token=conf.Environment?.Variables?.find(v=>v.Key==="TEAM_SCORE_UPLOAD_TOKEN")?.Value;
assert.ok(token,"API authentication unavailable");
const endpoint=`https://${environment}-1473474041.ap-shanghai.app.tcloudbase.com/team-score-sync`;
async function call(payload){
 const t=performance.now();
 const res=await fetch(endpoint,{method:"POST",headers:{"Content-Type":"application/json","x-upload-token":token},
  body:JSON.stringify(payload),signal:AbortSignal.timeout(60000)});
 const data=await res.json();
 assert.equal(res.status,200,`${payload.action}: HTTP ${res.status}`);
 assert.equal(data.ok,true,`${payload.action}: ${data.error}`);
 const text=JSON.stringify(data);
 assert.ok(!hashes.some(h=>text.includes(h)),`${payload.action}: hidden capture leaked`);
 checks.push({action:payload.action,section:payload.section,character:payload.character_name,
  nest:payload.nest,jobs:payload.jobs?.length,nextPage:Boolean(payload.cursor),
  ms:Math.round(performance.now()-t),version:data.service_version});
 console.log(JSON.stringify(checks.at(-1)));
 return data;
}
for(const name of [...new Set(records.map(r=>r[5]).filter(Boolean))]){
 const data=await call({action:"character_history",character_name:name,nest:"梦魇海龙",limit:50});
 assert.equal(data.history.total,name==="Charon"?16:0);
}
for(const action of ["character_history_overlay","score_history_query"]){
 const data=await call({action,character_name:"冷艳高傲绝世幻",nest:"梦魇海龙",include_facets:true,limit:50});
 assert.equal(data.history.items.length,0);
}
for(const r of records.filter(r=>r[5]==="冷艳高傲绝世幻").slice(0,2)){
 for(const action of ["character_history_detail","profession_history_detail"]){
  const data=await call({action,character_name:r[5],job:r[4],history_id:r[1]});
  assert.ok(data.detail==null||Object.keys(data.detail).length===0);
 }
}
for(const section of ["professions","skills","support","roles","teams","distribution","team-captures"]){
 await call({action:"database_profession_ranking",section,nest:"梦魇海龙",job:"圣骑士",character_name:"冷艳高傲绝世幻"});
}
// Match the actual-ranking UI's full scope, including both pharmacist roles.
// A 32-job subset misses the expensive fallback that caused the 047 regression.
const rankingJobs=JSON.parse(await readFile(new URL("../capture_privacy_ranking_20261020/jobs.json",root),"utf8"));
assert.equal(rankingJobs.length,34);
for(const nest of ["梦魇海龙","梦魇海龙","梦魇海龙","黑龙硬核","黑龙经典"]){
 const request={action:"score_history_query",jobs:rankingJobs,nest,completion_status:"cleared",
  sort_key:"actualDps",sort_direction:"desc",limit:20,include_facets:false};
 const page=await call(request);
 assert.ok(page.history.items.length>0,`${nest}: ranking unexpectedly empty`);
 if(page.history.nextCursor){
  const next=await call({...request,cursor:page.history.nextCursor});
  assert.ok(next.history.items.length>0,`${nest}: next page unexpectedly empty`);
  const previousIds=new Set(page.history.items.map(i=>i.historyId));
  assert.ok(next.history.items.every(i=>!previousIds.has(i.historyId)),`${nest}: repeated first page`);
 }
}
const other=await call({action:"character_history",character_name:"冷艳高傲绝世幻",nest:"黑龙硬核",limit:20});
const baseline=JSON.parse(await readFile(new URL("apply-baseline.json",root),"utf8"));
assert.equal(other.history.total,baseline.otherNestHistory.total);
assert.deepEqual(other.history.items.map(i=>i.historyId),baseline.otherNestHistory.items.map(i=>i.historyId));
await writeFile(new URL("verification.json",root),JSON.stringify({verifiedAt:new Date().toISOString(),checks},null,2));
console.log(JSON.stringify({passed:true,hiddenCaptures:33,retainedRows:263,charonOtherCaptures:16}));
