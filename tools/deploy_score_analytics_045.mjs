import {api,sql,environment} from "./cloudbase_analytics_admin.mjs";
import {readFile,writeFile,mkdir} from "node:fs/promises";
import {createHash} from "node:crypto";
const root=new URL("../cloudbase/releases/analytics045/",import.meta.url);
const rows=r=>(r.Rows||[]).map(JSON.parse);
await mkdir(root,{recursive:true});
if(process.argv.includes("--backup")){
 const functions=await sql("select pg_get_functiondef('public.get_database_skill_ranking_v1(text,integer[],text,timestamptz,timestamptz,text)'::regprocedure);");
 await writeFile(new URL("previous-skill-rpc.sql",root),rows(functions)[0][0]);
 const code=await api("scf","GetFunctionAddress",{FunctionName:"team-score-sync",Namespace:environment});
 if(!code.Url)throw new Error("Function backup URL missing; do not deploy");
 const response=await fetch(code.Url);
 if(!response.ok)throw new Error(`Backup download HTTP ${response.status}`);
 const buffer=Buffer.from(await response.arrayBuffer());
 await writeFile(new URL("previous-team-score-sync.zip",root),buffer);
 console.log(JSON.stringify({backup:"saved",bytes:buffer.length,sha256:createHash("sha256").update(buffer).digest("hex"),requestId:code.RequestId}));
}
if(process.argv.includes("--apply")){
 const source=await readFile(new URL("../cloudbase/postgresql/045_score_analytics.sql",import.meta.url),"utf8");
 const result=await sql(source);
 await writeFile(new URL("migration-result.json",root),JSON.stringify(result,null,2));
 console.log(JSON.stringify({migration:"045",requestId:result.RequestId,ms:result.ExecutionTimeMs}));
}
if(process.argv.includes("--backfill")){
 let progress={last:0,processed:0};
 try{progress=JSON.parse(await readFile(new URL("backfill-progress.json",root),"utf8"));}catch{}
 const bound=Number(rows(await sql("select coalesce(max(team_score_id),0) from public.team_score_extended_details;"))[0][0]);
 while(progress.last<bound){
  // Lock the exact source rows while deriving metrics, so concurrent rescores
  // cannot be overwritten by a stale backfill snapshot.
  const response=await sql(`with source as materialized (
   select team_score_id,score_detail,specialty_detail from public.team_score_extended_details
   where team_score_id>${progress.last} and team_score_id<=${bound}
   order by team_score_id limit 100 for update
  ), applied as materialized (
   select team_score_id,public.refresh_team_score_analytics(team_score_id,score_detail,specialty_detail) done from source
  ) select count(*),coalesce(max(team_score_id),${bound}) from applied;`);
  const [count,last]=rows(response)[0].map(Number);
  progress={last,processed:progress.processed+count};
  await writeFile(new URL("backfill-progress.json",root),JSON.stringify({...progress,bound,updatedAt:new Date().toISOString()}));
  if(progress.processed%1000===0||!count||last===bound)console.log(JSON.stringify({...progress,bound,batchMs:response.ExecutionTimeMs}));
  if(!count)break;
  await new Promise(r=>setTimeout(r,100));
 }
 await sql("analyze public.team_score_analytics_metrics;");
 console.log("Backfill complete");
}
if(process.argv.includes("--function")){
 const path=process.argv[process.argv.indexOf("--function")+1];
 if(!path)throw new Error("Supply validated deployment zip path");
 const result=await api("scf","UpdateFunctionCode",{FunctionName:"team-score-sync",Namespace:environment,
  CodeSource:"ZipFile",ZipFile:(await readFile(path)).toString("base64")});
 await writeFile(new URL("function-result.json",root),JSON.stringify(result,null,2));
 console.log(JSON.stringify({function:"team-score-sync",requestId:result.RequestId}));
}
