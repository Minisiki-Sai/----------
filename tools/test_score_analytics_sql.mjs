import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { PGlite } from "../work/score_analytics_pg/package/dist/index.js";
const db=new PGlite();
await db.exec(`
create role anon; create role authenticated; create role service_role;
create table team_scores(id bigint primary key,row_fingerprint text,score_schema_version integer);
create table team_score_extended_details(team_score_id bigint primary key references team_scores(id) on delete cascade,score_detail jsonb,specialty_detail jsonb);
create table team_score_ranking_facts(team_score_id bigint primary key,job text,identity_key text,nest text,
 score_schema_version integer,completion_status text,recorded_at timestamptz);
create table team_score_skill_details(team_score_id bigint,skill_name text,high_value boolean,excluded_from_score boolean,
 actual_count integer,theory_count integer,actual_power double precision,theory_power double precision);
create table cloudbase_schema_history(version text primary key,description text,applied_at timestamptz default now());
create function team_score_is_rankable_second_job(text) returns boolean language sql immutable as $$ select $1 in('十字军','圣骑士','圣徒'); $$;
create function team_score_nest_filter_v1(text) returns text[] language sql immutable as $$ select case when $1='' then '{}'::text[] when left($1,1)='[' then array(select jsonb_array_elements_text($1::jsonb)) else array[$1] end; $$;
`);
const sql=readFileSync(new URL("../cloudbase/postgresql/045_score_analytics.sql",import.meta.url),"utf8");
await db.exec(sql);
async function seed(id,{version=20261017,nest="黑龙硬核",job="十字军",identity=`c:${id}`,status="cleared",hit=80,multiplier=70,seconds=120,casts=10,standard=60,role={}}={}){
 await db.query("insert into team_scores values($1,$2,$3)",[id,`${"a".repeat(64)}:${id}`,version]);
 await db.query("insert into team_score_ranking_facts values($1,$2,$3,$4,$5,$6,$7)",[id,job,identity,nest,version,status,`2026-10-${String(id).padStart(2,"0")}T10:00:00Z`]);
 await db.query("insert into team_score_skill_details values($1,'重击',true,false,$2,20,$3,100)",[id,casts,standard]);
 const detail={team_output_seconds:seconds,damage_overview:{realizations:[{skill:"重击EX",hit_boss_rate:hit,multiplier_boss_rate:multiplier}]}};
 await db.query("insert into team_score_extended_details values($1,$2,$3)",[id,detail,role]);
}
await seed(1,{version:20261010,hit:9999,multiplier:9999,casts:9999});
await seed(2,{hit:80,multiplier:70,identity:"c:one"});
await seed(3,{hit:100,multiplier:90,identity:"c:one"});
await seed(4,{hit:50,multiplier:40,identity:"c:two",casts:6,standard:40});
await seed(5,{nest:"海龙",hit:500});
await seed(6,{status:"not_cleared",hit:900});
await seed(7,{identity:null,hit:900});
const ranking=async(job="十字军",nest="黑龙硬核",version=[]) => (await db.query(
 "select get_database_skill_ranking_v2($1,$2,'cleared',null,null,$3) payload",[nest,version,job])).rows[0].payload;
let row=(await ranking()).rows[0];
assert.equal(row.recordCount,3);
assert.equal(row.characterCount,2);
assert.equal(row.metrics.hit.median,70); // median(character medians 90,50)
assert.equal(row.metrics.hit.p90,86);
assert.equal(row.metrics.multiplier.median,60);
assert.equal(row.metrics.castsPerMinute.median,4); // character values 5 and3
assert.equal(row.metrics.standardDamage.median,50);
assert.equal(row.hitCompletionMedian,70); // never count ratio (50%,30%)
assert.equal(row.powerCompletionMedian,60); // never damage completion
for(const metric of Object.values(row.metrics))for(const e of metric.examples||[]){
 assert.equal(e.nest,"黑龙硬核");assert.ok([2,3,4].includes(Number(e.historyId.split(":")[1])));
}
assert.equal((await ranking("十字军","黑龙硬核",[20261010])).rows.length,0);
assert.equal((await ranking("十字军",'["黑龙硬核","海龙"]')).rows[0].recordCount,4);
assert.equal((await db.query("select get_database_skill_ranking_v2('黑龙硬核','{}','',null,null,'十字军') payload")).rows[0].payload.rows[0].recordCount,4,"explicit all-completion filter is respected");
assert.equal((await db.query("select count(*) n from team_score_analytics_metrics where team_score_id=1 and metric in ('hit','multiplier')")).rows[0].n,0);
// Unknown stays unknown, and lack of team denominator does not become a 0/minute.
await seed(8,{job:"圣骑士",hit:null,multiplier:null,seconds:null,role:{tank_taunt_completion:90,tank_shell_completion:80,tank_attack_down_completion:0,effective_healing:0}});
row=(await ranking("圣骑士")).rows[0];
assert.equal(row.hitCompletionMedian,null);
assert.equal(row.metrics.hit,undefined);
assert.equal(row.metrics.castsPerMinute,undefined);
const roles=(await db.query("select get_database_role_ranking_v1('黑龙硬核','{}','cleared',null,null,'圣骑士') payload")).rows[0].payload.rows;
assert.equal(roles.find(r=>r.metric==="tank_taunt_completion").median,90);
assert.equal(roles.find(r=>r.metric==="tank_attack_down_completion").median,0);
assert.ok(!roles.some(r=>r.metric==="healing_rescue_completion"));
assert.ok(!roles.some(r=>r.metric==="effective_healing"),"non-healer zero placeholders are not healer performance");
await db.exec(`update team_score_extended_details set specialty_detail='{"healing_rescue_completion":100,"healing_rescue_detail":{"method":"danger-hp-recovery-v4","status":"no_demand"}}'::jsonb where team_score_id=8;`);
assert.equal((await db.query("select count(*) n from team_score_analytics_metrics where team_score_id=8 and metric='healing_rescue_completion'")).rows[0].n,0);
// Version replacement invalidates old metrics immediately.
await db.exec("update team_scores set score_schema_version=20261010 where id=3;update team_score_ranking_facts set score_schema_version=20261010 where team_score_id=3;");
assert.equal((await db.query("select count(*) n from team_score_analytics_metrics where team_score_id=3 and metric in ('hit','multiplier')")).rows[0].n,0);
const legacy=(await db.query("select get_database_skill_ranking_v1('黑龙硬核','{}','cleared',null,null,'十字军') payload")).rows[0].payload;
assert.equal(legacy.rows[0].recordCount,2);
// Migration is rerunnable and only rewrites derived facts; source rows remain.
await db.exec(sql);
assert.equal((await db.query("select count(*) n from team_scores")).rows[0].n,8);
assert.equal((await ranking()).rows[0].recordCount,2);
console.log("PASS: PostgreSQL execution, cutoff, filters, character percentiles, cases, missing values, roles, triggers, legacy RPC, idempotence");
await db.exec(`
alter table team_score_ranking_facts add column profile_id bigint, add column actual_dps double precision;
create table team_score_character_identities(team_score_id bigint primary key,character_name text);
create function mask_score_history_identity(jsonb) returns jsonb language sql as $$
 select case when $1->>'characterName'='隐藏角色' then $1||'{"characterName":"已隐藏","softwareHidden":true}'::jsonb else $1 end;
$$;
insert into team_score_character_identities values(2,'学习角色'),(4,'隐藏角色');
update team_score_ranking_facts set actual_dps=2500000 where team_score_id=2;
update team_score_extended_details set score_detail=score_detail||'{"instance_completion":{"server_elapsed_seconds":900}}'::jsonb where team_score_id=2;
insert into team_score_skill_details values(2,'破甲',false,false,5,5,20,30),(4,'破甲',false,false,5,5,20,30);
insert into team_score_skill_details values(2,'不计分',false,true,5,5,999999,999999);
`);
const migration046=readFileSync(new URL("../cloudbase/postgresql/046_score_analytics_presentation.sql",import.meta.url),"utf8");
await db.exec(migration046);
await db.exec("update team_score_extended_details set score_detail=score_detail where team_score_id=2;");
const newer=async()=>(await db.query("select get_database_skill_ranking_v3('黑龙硬核','{}','cleared',null,null,'十字军') p")).rows[0].p;
const current=await newer();
assert.equal(current.rows.length,2);
assert.equal(current.rows[0].skillName,"重击");
assert.ok(Math.abs(current.rows[0].metrics.standardShare.median-(75+100*40/60)/2)<1e-8);
assert.equal(current.rows[0].metrics.hit.median,65);
const examples=current.rows[0].metrics.hit.examples;
assert.equal(examples.find(r=>r.characterName==="学习角色").actualDps,2500000);
assert.equal(examples.find(r=>r.characterName==="学习角色").clearSeconds,900);
assert.equal(examples.find(r=>r.softwareHidden).characterName,"已隐藏");
assert.equal(examples.find(r=>r.softwareHidden).clearSeconds,null);
await db.exec(migration046);
assert.deepEqual((await newer()).rows,current.rows);
assert.equal((await db.query("select count(*) n from team_scores")).rows[0].n,8);
console.log("PASS: 046 shares, excluded skills, deterministic ordering, examples, privacy, real clear time, idempotence");
await db.close();
