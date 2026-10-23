import {sql,api,environment} from "./cloudbase_analytics_admin.mjs";
import {mkdir,writeFile} from "node:fs/promises";
const queries = [
 ["facts", `select s.job,m.metric,count(*) from team_score_analytics_metrics m
 join team_scores s on s.id=m.team_score_id where s.job in('毁灭者','圣骑士','圣徒','药剂师')
 and m.skill_key='' group by s.job,m.metric order by 1,2;`],
 ["source-keys", `select s.job,k.key,count(*) from team_score_extended_details e
 join team_scores s on s.id=e.team_score_id cross join lateral jsonb_object_keys(e.specialty_detail) k(key)
 where s.job in('毁灭者','圣骑士','圣徒','药剂师') and (k.key like 'tank%' or k.key like 'healing%' or k.key like '%shell%' or k.key like '%taunt%')
 group by s.job,k.key order by 1,2;`],
 ["destroyer-role-rpc", `select get_database_role_ranking_v1('','{}','cleared',null,null,'毁灭者');`],
];
for (const [name,q] of queries) {
 const result=await sql(q);
 console.log(JSON.stringify({name,rows:result.Rows?.map(JSON.parse),ms:result.ExecutionTimeMs,error:result.Error}));
}
const conf=await api("scf","GetFunction",{FunctionName:"team-score-sync",Namespace:environment});
const token=conf.Environment?.Variables?.find(v=>v.Key==="TEAM_SCORE_UPLOAD_TOKEN")?.Value;
if(!token)throw Error("No API authentication available");
const response=await fetch(`https://${environment}-1473474041.ap-shanghai.app.tcloudbase.com/team-score-sync`,{
 method:"POST",headers:{"Content-Type":"application/json","x-upload-token":token},
 body:JSON.stringify({action:"database_profession_ranking",section:"roles",job:"毁灭者",completion_status:"cleared"}),
 signal:AbortSignal.timeout(60000),
});
const data=await response.json();
if(response.status!==200||!data.ok||data.ranking?.section!=="roles")throw Error("Roles HTTP contract mismatch");
console.log(JSON.stringify({name:"cloud-http",version:data.service_version,rows:data.ranking.rows}));
const output=new URL("../outputs/verification/comparison_roles_20261020/",import.meta.url);
await mkdir(output,{recursive:true});
await writeFile(new URL("destroyer_roles.json",output),JSON.stringify(data.ranking,null,2));
