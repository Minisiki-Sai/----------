// Uses the existing CloudBase CLI login; never writes or logs credentials.
import {createHash,createHmac} from "node:crypto";
import {readFileSync} from "node:fs";
import {homedir} from "node:os";
import {join} from "node:path";
export const environment="dragonnest-d0gy32zvzd63b6c52";
const hash=v=>createHash("sha256").update(v).digest("hex");
const hmac=(k,v)=>createHmac("sha256",k).update(v).digest();
export async function api(service,action,params){
  const c=JSON.parse(readFileSync(join(homedir(),".config",".cloudbase","auth.json"),"utf8")).credential;
  const time=Math.floor(Date.now()/1000),date=new Date(time*1000).toISOString().slice(0,10);
  const host=`${service}.tencentcloudapi.com`,body=JSON.stringify(params);
  const canonical=`POST\n/\n\ncontent-type:application/json\nhost:${host}\n\ncontent-type;host\n${hash(body)}`;
  const scope=`${date}/${service}/tc3_request`;
  const key=hmac(hmac(hmac(`TC3${c.tmpSecretKey}`,date),service),"tc3_request");
  const signature=createHmac("sha256",key).update(`TC3-HMAC-SHA256\n${time}\n${scope}\n${hash(canonical)}`).digest("hex");
  const res=await fetch(`https://${host}`,{method:"POST",headers:{
    "Content-Type":"application/json","X-TC-Action":action,"X-TC-Version":service==="tcb"?"2018-06-08":"2018-04-16",
    "X-TC-Timestamp":String(time),"X-TC-Region":"ap-shanghai","X-TC-Token":c.tmpToken,
    Authorization:`TC3-HMAC-SHA256 Credential=${c.tmpSecretId}/${scope}, SignedHeaders=content-type;host, Signature=${signature}`,
  },body,signal:AbortSignal.timeout(180000)});
  const result=(await res.json()).Response;
  if(result?.Error)throw new Error(`${result.Error.Code}: ${result.Error.Message}`);
  if(!result)throw new Error("Missing API response");
  return result;
}
export const sql=Sql=>api("tcb","ExecutePGSql",{EnvId:environment,Sql});
if(process.argv[2]==="inspect"){
  const result=await sql("select current_user, (select count(*) from public.team_scores) as scores, (select count(*) from public.team_score_extended_details) as details, (select max(version) from public.cloudbase_schema_history) as migration;");
  console.log(JSON.stringify(result));
}
if(process.argv[2]==="schema"){
  const result=await sql("select table_name,column_name,data_type from information_schema.columns where table_schema='public' and table_name in ('team_score_ranking_facts','team_score_skill_details','team_score_extended_details') order by table_name,ordinal_position;");
  console.log(JSON.stringify(result));
}
