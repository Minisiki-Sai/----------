import {api,sql,environment} from "./cloudbase_analytics_admin.mjs";
import {readFile,writeFile,mkdir,access} from "node:fs/promises";
import assert from "node:assert/strict";
const root=new URL("../cloudbase/releases/analytics046/",import.meta.url);
await mkdir(root,{recursive:true});
const rows=r=>(r.Rows||[]).map(JSON.parse);
if(process.argv.includes("--apply")){
 let backedUp=false;
 try{await access(new URL("previous-team-score-sync.zip",root));backedUp=true;}catch{}
 if(!backedUp){
  const backup=await api("scf","GetFunctionAddress",{FunctionName:"team-score-sync",Namespace:environment});
  assert.ok(backup.Url);
  const response=await fetch(backup.Url);assert.ok(response.ok);
  await writeFile(new URL("previous-team-score-sync.zip",root),Buffer.from(await response.arrayBuffer()));
 }
 const source=await readFile(new URL("../cloudbase/postgresql/046_score_analytics_presentation.sql",import.meta.url),"utf8");
 const migration=await sql(source);
 await writeFile(new URL("migration.json",root),JSON.stringify(migration,null,2));
 console.log(JSON.stringify({migration:"046",requestId:migration.RequestId}));
}
if(process.argv.includes("--function")){
 const path=process.argv[process.argv.indexOf("--function")+1];assert.ok(path);
 const result=await api("scf","UpdateFunctionCode",{FunctionName:"team-score-sync",Namespace:environment,
  CodeSource:"ZipFile",ZipFile:(await readFile(path)).toString("base64")});
 await writeFile(new URL("function.json",root),JSON.stringify(result,null,2));
 console.log(JSON.stringify({function:"team-score-sync",requestId:result.RequestId}));
}
if(process.argv.includes("--backfill")){
 let last=0,processed=0;
 const bound=Number(rows(await sql("select coalesce(max(team_score_id),0) from team_score_extended_details;"))[0][0]);
 while(last<bound){
  const r=await sql(`with source as materialized (
   select team_score_id,score_detail from team_score_extended_details
   where team_score_id>${last} and team_score_id<=${bound} order by team_score_id limit 100 for update
  ), applied as (
   insert into team_score_server_clear_times
   select team_score_id,(score_detail#>>'{instance_completion,server_elapsed_seconds}')::double precision
   from source where jsonb_typeof(score_detail#>'{instance_completion,server_elapsed_seconds}')='number'
    and (score_detail#>>'{instance_completion,server_elapsed_seconds}')::double precision>=0
   on conflict(team_score_id) do update set seconds=excluded.seconds returning team_score_id
  ) select count(*),coalesce(max(team_score_id),${bound}) from source;`);
  const [count,end]=rows(r)[0].map(Number);last=end;processed+=count;
  if(processed%1000===0||last===bound||!count)console.log(JSON.stringify({backfill:processed,last,bound}));
  if(!count)break;
 }
 await sql("analyze public.team_score_server_clear_times;");
}
if(process.argv.includes("--verify")){
 const result=await sql(`select get_database_skill_ranking_v3('黑龙硬核','{}','cleared',null,null,'十字军');`);
 const ranking=rows(result)[0][0];const parsed=typeof ranking==="string"?JSON.parse(ranking):ranking;
 assert.ok(parsed.rows.length);
 assert.ok(parsed.rows[0].metrics.standardShare.median>0);
 const examples=parsed.rows.flatMap(r=>Object.values(r.metrics).flatMap(m=>m.examples||[]));
 assert.ok(examples.some(e=>e.characterName));
 assert.ok(examples.some(e=>e.actualDps>0));
 for(let i=1;i<parsed.rows.length;i++)assert.ok((parsed.rows[i-1].metrics.standardShare.median??-1)>=(parsed.rows[i].metrics.standardShare.median??-1));
 const conf=await api("scf","GetFunction",{FunctionName:"team-score-sync",Namespace:environment});
 const token=conf.Environment?.Variables?.find(v=>v.Key==="TEAM_SCORE_UPLOAD_TOKEN")?.Value;assert.ok(token);
 const endpoint=`https://${environment}-1473474041.ap-shanghai.app.tcloudbase.com/team-score-sync`;
 const results=[];
 for(const [section,job,nest] of [["skills","十字军","黑龙硬核"],["roles","圣骑士","黑龙硬核"],["skills","",""]]){
  const t=performance.now();
  const response=await fetch(endpoint,{method:"POST",headers:{"Content-Type":"application/json","x-upload-token":token},
   body:JSON.stringify({action:"database_profession_ranking",section,job,nest,completion_status:"cleared"}),signal:AbortSignal.timeout(60000)});
  const body=await response.json();assert.equal(response.status,200,JSON.stringify(body));assert.equal(body.ok,true);
  assert.equal(body.service_version,"cloudbase-2.19.1-score-analytics");
  if(section==="skills")assert.ok(body.ranking.rows[0].metrics.standardShare);
  if(section==="roles")assert.ok(body.ranking.rows.every(r=>typeof r.metric==="string"));
  results.push({section,job,nest,ms:Math.round(performance.now()-t),rows:body.ranking.rows.length,version:body.service_version});
  console.log(JSON.stringify(results.at(-1)));
 }
 await writeFile(new URL("verification.json",root),JSON.stringify({sqlMs:result.ExecutionTimeMs,results},null,2));
}
