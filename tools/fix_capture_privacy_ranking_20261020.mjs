import assert from "node:assert/strict";
import {readFile,writeFile} from "node:fs/promises";
import {sql} from "./cloudbase_analytics_admin.mjs";
const root=new URL("../outputs/verification/capture_privacy_ranking_20261020/",import.meta.url);
const jobs=JSON.parse(await readFile(new URL("jobs.json",root),"utf8"));
assert.equal(jobs.length,34);
const esc=v=>"'"+v.replaceAll("'","''")+"'";
const source=await readFile(new URL("../cloudbase/postgresql/048_capture_privacy_query_performance.sql",import.meta.url),"utf8");
function query(nest,cursor="",facets=false){
 return `public.get_score_history_page_v3('',array[${jobs.map(esc).join(",")}],${esc(nest)},null,null,null,null,'','actualDps','desc',${esc(cursor)},20,${facets},'cleared','{}')`;
}
function unpack(r){
 const first=JSON.parse(r.Rows.at(-1))[0];
 return typeof first==="string"?JSON.parse(first):first;
}
const measurements=[];
async function run(nest,candidate,cursor="",facets=false){
 const t=performance.now();
 const patch=candidate?source.replace(/commit;\s*$/i,""):"begin;";
 const result=await sql(`${patch}
 set local statement_timeout='30s';
 set local role service_role;
 select ${query(nest,cursor,facets)};
 rollback;`);
 const payload=unpack(result);
 const item={nest,candidate,cursor:Boolean(cursor),facets,ms:Math.round(performance.now()-t),sqlMs:result.ExecutionTimeMs,items:payload.items.length};
 measurements.push(item);
 console.log(JSON.stringify(item));
 return payload;
}
if(process.argv[2]==="--apply"){
 const tests=JSON.parse(await readFile(new URL("comparison.json",root),"utf8"));
 assert.equal(tests.passed,true);
 const current=await sql(`select schemaname,viewname,definition from pg_views where schemaname='score_public'
 and viewname in('team_scores','team_score_ranking_facts','team_score_capture_files') order by viewname;`);
 await writeFile(new URL("views-before.json",root),JSON.stringify(current,null,2));
 const result=await sql(source);
 await writeFile(new URL("deployment.json",root),JSON.stringify(result,null,2));
 console.log(JSON.stringify({applied:"048",requestId:result.RequestId}));
}else{
 // Exact result equality, not only a page-size check; no hidden data is exposed
 // during comparison because each candidate is tested inside a rolled-back txn.
 for(const nest of ["梦魇海龙","黑龙硬核","黑龙经典"]){
  const old=await run(nest,false);
  const next=await run(nest,true);
  assert.deepEqual(next,old,`${nest}: page results changed`);
  if(old.nextCursor){
   assert.deepEqual(await run(nest,true,old.nextCursor),await run(nest,false,old.nextCursor));
  }
 }
 for(let repeat=0;repeat<2;repeat++){
  assert.deepEqual(await run("梦魇海龙",true),await run("梦魇海龙",false));
 }
 assert.deepEqual(await run("梦魇海龙",true,"",true),await run("梦魇海龙",false,"",true));
 await writeFile(new URL("comparison.json",root),JSON.stringify({passed:true,measurements},null,2));
 console.log(JSON.stringify({passed:true,measurements:measurements.length}));
}
