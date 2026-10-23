import assert from "node:assert/strict";
import {readFile,writeFile} from "node:fs/promises";
import {sql} from "./cloudbase_analytics_admin.mjs";
const root=new URL("../outputs/verification/capture_privacy_20261020/",import.meta.url);
const hashes=JSON.parse(await readFile(new URL("captures.json",root),"utf8")).map(r=>r[0]);
assert.equal(hashes.length,33);
assert.equal(new Set(hashes).size,33);
assert.ok(hashes.every(h=>/^[0-9a-f]{64}$/.test(h)));
const literals=hashes.map(h=>`'${h}'`).join(",");
const inScope=`capture_hash in (${literals})`;
const rows=r=>(r.Rows||[]).map(JSON.parse);
const read=async q=>rows(await sql(q));
const source=await readFile(new URL("../cloudbase/postgresql/047_capture_scoped_privacy.sql",import.meta.url),"utf8");
const definitions=JSON.parse(await readFile(new URL("functions.json",root),"utf8"));
const mode=process.argv[2]||"--test";
assert.ok(["--test","--apply"].includes(mode));
// Refuse to overwrite a newer, concurrently deployed function definition.
for(const batch of [definitions.filter(f=>f[1].startsWith("get_")||f[1]==="refresh_team_score_benchmark")]){
 const current=await read(`select p.oid::text,pg_get_functiondef(p.oid) from pg_proc p where p.oid=any(array[${batch.map(f=>f[0]).join(",")}]::oid[])`);
 for(const [oid,def] of current){
  assert.equal(def,batch.find(f=>f[0]===oid)[3],`Concurrent RPC change: ${oid}; re-audit before applying`);
 }
}
const baseline=await read(`select jsonb_build_object(
 'scores',(select count(*) from public.team_scores),
 'scopeRows',(select count(*) from public.team_scores where ${inScope}),
 'scopeCaptures',(select count(distinct capture_hash) from public.team_scores where ${inScope}),
 'otherNestsInScope',(select count(*) from public.team_scores where ${inScope} and nest<>'梦魇海龙'),
 'scopeFiles',(select count(*) from public.team_score_capture_files where ${inScope}),
 'otherCharon',(select count(*) from public.team_scores s join public.team_score_character_identities i on i.team_score_id=s.id where i.character_name='Charon' and s.nest='梦魇海龙' and not(s.${inScope})),
 'otherNestHistory', public.get_character_score_history(
 (select character_key from public.team_score_character_identities where character_name='冷艳高傲绝世幻' limit 1),'黑龙硬核',0,20)
 );`);
const base=typeof baseline[0][0]==="string"?JSON.parse(baseline[0][0]):baseline[0][0];
assert.equal(base.scopeCaptures,33);
assert.equal(base.otherNestsInScope,0);
assert.equal(base.otherCharon,16);
await writeFile(new URL(`${mode.slice(2)}-baseline.json`,root),JSON.stringify(base,null,2));
const rawSnapshot=await read(`select s.id,s.row_fingerprint,s.capture_hash,s.nest,s.job,i.character_name from public.team_scores s left join public.team_score_character_identities i on i.team_score_id=s.id where s.${inScope} order by s.capture_hash,s.sample_ordinal;`);
await writeFile(new URL(`${mode.slice(2)}-affected-records.json`,root),JSON.stringify(rawSnapshot,null,2));
const otherHistory=JSON.stringify(base.otherNestHistory).replaceAll("'","''");
const insert=`insert into score_privacy.hidden_captures(capture_hash,reason)
select h,'User-requested privacy: 33 shared Nightmare Sea Dragon captures, 2026-10-20'
from unnest(array[${literals}]) h
on conflict(capture_hash) do nothing;`;
const assertions=`
do $verify$
declare r record; v jsonb; n integer;
begin
 if (select count(*) from public.team_scores where ${inScope}) <> ${base.scopeRows}
  then raise exception 'Source scores changed'; end if;
 if (select count(*) from public.team_score_capture_files where ${inScope}) <> ${base.scopeFiles}
  then raise exception 'Source capture files changed'; end if;
 if exists(select 1 from score_public.team_scores where ${inScope})
  or exists(select 1 from score_public.team_score_ranking_facts where ${inScope})
  or exists(select 1 from score_public.team_score_capture_files where ${inScope})
  then raise exception 'Hidden captures still public'; end if;
 for r in select s.row_fingerprint,s.capture_hash,s.job,i.character_key
  from public.team_scores s left join public.team_score_character_identities i on i.team_score_id=s.id where s.${inScope} order by s.id limit 3
 loop
  v:=public.get_profession_score_history_detail(r.job,r.row_fingerprint);
  if v is not null and v<>'{}'::jsonb then raise exception 'Profession detail leaked'; end if;
  if r.character_key is not null then
   v:=public.get_character_score_history_detail(r.character_key,r.row_fingerprint);
   if v is not null and v<>'{}'::jsonb then raise exception 'Character detail leaked'; end if;
  end if;
  if public.get_team_score_roster(r.row_fingerprint)<>'[]'::jsonb
   then raise exception 'Roster leaked'; end if;
  if public.get_score_damage_overview_v1(r.row_fingerprint) is not null
   or public.get_team_boss_debuffs_v1(r.row_fingerprint) is not null
   then raise exception 'Archive leaked'; end if;
 end loop;
 for r in select unnest(array[${literals}]) h loop
  select count(*) into n from public.get_team_score_capture_file(r.h);
  if n<>0 then raise exception 'Capture file metadata leaked'; end if;
 end loop;
 if (select count(*) from score_public.team_scores s join public.team_score_character_identities i on i.team_score_id=s.id
  where i.character_name='Charon' and s.nest='梦魇海龙')<>16
  then raise exception 'Charon other 16 records changed'; end if;
 v:=public.get_character_score_history(
 (select character_key from public.team_score_character_identities where character_name='冷艳高傲绝世幻' limit 1),'梦魇海龙',0,50);
 if (v->>'total')::integer <> 0 or jsonb_array_length(v->'items')<>0 then raise exception 'Target history leaked'; end if;
 v:=public.get_character_score_history(
 (select character_key from public.team_score_character_identities where character_name='冷艳高傲绝世幻' limit 1),'黑龙硬核',0,20);
 -- The global nest facet must lose the now-private nest; other-nest rows stay exact.
 if (v-'nests') <> ('${otherHistory}'::jsonb-'nests') then raise exception 'Other nest history changed'; end if;
end $verify$;
`;
// Exercise all ranking implementations, including compatibility routes.
const smokeCalls=definitions.filter(f=>f[1].startsWith("get_database_")&&!f[1].includes("team_captures")).map(f=>{
 const nargs=f[2].split(",").length;
 let args=["'梦魇海龙'","'{}'::integer[]"];
 if(nargs>2)args.push("''","null::timestamptz","null::timestamptz");
 if(nargs>5)args.push(f[2].includes("p_profile_id")?"null::bigint":"'圣骑士'");
 if(nargs>6)args.push("''");
 return [f[1],`perform public.${f[1]}(${args.join(",")});`];
});
const benchmarkRefresh=`do $refresh$ declare k record; begin
 for k in select distinct nest,job from public.team_scores where ${inScope} loop
  perform public.refresh_team_score_benchmark(k.nest,k.job);
 end loop;
end $refresh$;`;
const suite=process.argv[3]||"core";
assert.ok(suite==="core"||suite==="rankings"||suite==="benchmarks"||smokeCalls.some(([name])=>name===suite));
const selectedSmoke=smokeCalls.filter(([name])=>suite==="rankings"||suite===name).map(([,call])=>call).join("\n");
const targetKey=(await read("select character_key from public.team_score_character_identities where character_name='冷艳高傲绝世幻' limit 1"))[0][0];
assert.match(targetKey,/^[0-9a-f]{64}$/);
const roleCheck=`set local role service_role;
select public.get_character_score_history(
 '${targetKey}','梦魇海龙',0,20);`;
const main=source.replace(/commit;\s*$/i,"")+insert+
 (suite==="core"?(mode==="--apply"?benchmarkRefresh:"")+assertions+roleCheck: selectedSmoke?`do $smoke$ begin ${selectedSmoke} end $smoke$;`:benchmarkRefresh)+
 (mode==="--apply"?"commit;":"rollback;");
if(mode==="--apply"){
 assert.equal(suite,"core","Production apply must execute scope assertions");
 await writeFile(new URL("restore-visibility.sql",root),
  `-- Recovery only; not executed. Restores public visibility of exactly these 33 captures.\nbegin;\n`+
  `delete from score_privacy.hidden_captures where ${inScope};\n`+benchmarkRefresh+"\ncommit;\n");
}
console.log(JSON.stringify({mode,suite,scopeRows:base.scopeRows,scopeCaptures:base.scopeCaptures,scopeFiles:base.scopeFiles,stage:"SQL transaction and assertions"}));
const result=await sql(main);
await writeFile(new URL(`${mode.slice(2)}-${suite}-result.json`,root),JSON.stringify(result,null,2));
console.log(JSON.stringify({mode,suite,passed:true,requestId:result.RequestId}));
