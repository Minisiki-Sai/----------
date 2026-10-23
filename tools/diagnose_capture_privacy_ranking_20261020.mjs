import {sql,api,environment} from "./cloudbase_analytics_admin.mjs";
import {mkdir,writeFile,readFile} from "node:fs/promises";
const root=new URL("../outputs/verification/capture_privacy_ranking_20261020/",import.meta.url);
await mkdir(root,{recursive:true});
const rows=r=>(r.Rows||[]).map(JSON.parse);
const jobs=[...new Set([...rows(await sql("select distinct job from public.team_scores where public.team_score_is_rankable_second_job(job) order by 1;")).map(r=>r[0]),"输出药剂","辅助药剂"])];
const definitions=rows(await sql("select pg_get_functiondef('public.get_score_history_page_v3(text,text[],text,double precision,double precision,timestamptz,timestamptz,text,text,text,text,integer,boolean,text,integer[])'::regprocedure);"));
await writeFile(new URL("current-rpc.json",root),JSON.stringify(definitions,null,2));
await writeFile(new URL("jobs.json",root),JSON.stringify(jobs));
const payload={action:"score_history_query",jobs,nest:"梦魇海龙",completion_status:"cleared",sort_key:"actualDps",sort_direction:"desc",limit:20,include_facets:false};
if(process.argv[2]==="--http"){
 const conf=await api("scf","GetFunction",{FunctionName:"team-score-sync",Namespace:environment});
 const token=conf.Environment.Variables.find(v=>v.Key==="TEAM_SCORE_UPLOAD_TOKEN").Value;
 const t=performance.now();
 const res=await fetch(`https://${environment}-1473474041.ap-shanghai.app.tcloudbase.com/team-score-sync`,{method:"POST",
 headers:{"Content-Type":"application/json","x-upload-token":token},body:JSON.stringify(payload),signal:AbortSignal.timeout(60000)});
 const data=await res.json();
 await writeFile(new URL("http-before.json",root),JSON.stringify({ms:performance.now()-t,status:res.status,payload,data},null,2));
 console.log(JSON.stringify({status:res.status,ms:Math.round(performance.now()-t),error:data.error,items:data.history?.items?.length,jobs:jobs.length}));
}else{
 const candidate=process.argv[2]==="--candidate";
 const views=candidate?["team_scores","team_score_ranking_facts","team_score_capture_files"].map(t=>
 `create or replace view score_public.${t} as select t.* from public.${t} t
 where t.capture_hash not in(select h.capture_hash from score_privacy.hidden_captures h);`).join("\n"):"";
 const query=`public.get_score_history_page_v3('',array[${jobs.map(j=>`'${j.replaceAll("'","''")}'`).join(",")}],'梦魇海龙',null,null,null,null,'','actualDps','desc','',20,false,'cleared','{}')`;
 const t=performance.now();
 try{
 const result=await sql(`begin;set local statement_timeout='15s';${views}
 ${process.argv.includes("--no-jit")?"set local jit=off;":""}
 set local role service_role;select ${query};rollback;`);
 await writeFile(new URL(candidate?"candidate.json":"baseline.json",root),JSON.stringify({ms:performance.now()-t,result},null,2));
 console.log(JSON.stringify({candidate,ms:Math.round(performance.now()-t),success:true}));
 }catch(e){console.log(JSON.stringify({candidate,ms:Math.round(performance.now()-t),error:e.message}));}
}
