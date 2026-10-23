import {api,sql,environment} from "./cloudbase_analytics_admin.mjs";
import {writeFile} from "node:fs/promises";
import assert from "node:assert/strict";
const report={at:new Date().toISOString(),queries:[],http:[]};
const unpack=r=>(r.Rows||[]).map(JSON.parse);
const query=async(name,statement)=>{
 const r=await sql(statement),value=unpack(r);
 report.queries.push({name,ms:r.ExecutionTimeMs,requestId:r.RequestId,rows:value});
 console.log(JSON.stringify({name,ms:r.ExecutionTimeMs,preview:JSON.stringify(value).slice(0,220)}));
 return value;
};
if(process.argv.includes("--refresh-rescue")){
 // Only refresh existing rescue facts after tightening the no-demand boundary.
 const ids=unpack(await sql("select team_score_id from public.team_score_analytics_metrics where metric='healing_rescue_completion' order by team_score_id;")).map(r=>Number(r[0]));
 for(let i=0;i<ids.length;i+=50){
  await sql(`with applied as materialized (
   select public.refresh_team_score_analytics(team_score_id,score_detail,specialty_detail)
   from public.team_score_extended_details where team_score_id in(${ids.slice(i,i+50).join(",")})
   for update
  ) select count(*) from applied;`);
 }
 console.log(`Rescue facts checked: ${ids.length}`);
}
if(process.argv.includes("--remove-nonhealer-facts")){
 const result=await sql("delete from public.team_score_analytics_metrics m where m.metric='effective_healing' and not exists(select 1 from public.team_score_analytics_metrics h where h.team_score_id=m.team_score_id and h.metric='healing_effective_realization');");
 console.log(JSON.stringify({removedDerivedNonhealerFields:result.AffectedRows}));
}
await query("source-and-derived-counts",`select
 (select count(*) from team_scores),(select count(*) from team_score_extended_details),
 (select count(*) from team_score_analytics_metrics),
 (select count(*) from team_score_analytics_metrics m join team_scores s on s.id=m.team_score_id where m.metric in('hit','multiplier') and s.score_schema_version<20261011);`);
await query("metric-populations","select metric,count(*) from team_score_analytics_metrics group by metric order by metric;");
for(const [name,job,nest] of [["crusader","十字军","黑龙硬核"],["all-black","","黑龙硬核"],["all-dragon","",""]]){
 await query(name,`select jsonb_array_length(p->'rows'), (p->'rows'->0->>'skillName'),(p->'rows'->0->'metrics')-'actualPower'-'theoryPower'-'countCompletion' from (select get_database_skill_ranking_v2('${nest}','{}','cleared',null,null,'${job}') p) q;`);
}
await query("legacy-contract","select get_database_skill_ranking_v1('黑龙硬核','{}','cleared',null,null,'十字军')=get_database_skill_ranking_v2('黑龙硬核','{}','cleared',null,null,'十字军');");
await query("role-samples","select get_database_role_ranking_v1('','{}','cleared',null,null,'圣骑士'),get_database_role_ranking_v1('','{}','cleared',null,null,'圣徒');");
if(process.argv.includes("--http")){
 const conf=await api("scf","GetFunction",{FunctionName:"team-score-sync",Namespace:environment});
 const token=conf.Environment?.Variables?.find(v=>v.Key==="TEAM_SCORE_UPLOAD_TOKEN")?.Value;
 assert.ok(token,"Upload authentication not available");
 const endpoint=`https://${environment}-1473474041.ap-shanghai.app.tcloudbase.com/team-score-sync`;
 for(const [section,job,nest] of [["skills","十字军","黑龙硬核"],["roles","圣骑士","黑龙硬核"],["professions","","黑龙硬核"],["skills","",""]]){
  const start=performance.now();
  const res=await fetch(endpoint,{method:"POST",headers:{"Content-Type":"application/json","x-upload-token":token},
   body:JSON.stringify({action:"database_profession_ranking",section,nest,job,completion_status:"cleared"}),
   signal:AbortSignal.timeout(60000)});
  const body=await res.json();
  assert.equal(res.status,200,JSON.stringify(body));
  assert.equal(body.ok,true);
  const summary={section,job,nest,ms:Math.round(performance.now()-start),version:body.service_version,rows:body.ranking?.rows?.length};
  report.http.push(summary);console.log(JSON.stringify(summary));
 }
}
await writeFile(new URL("../cloudbase/releases/analytics045/verification.json",import.meta.url),JSON.stringify(report,null,2));
