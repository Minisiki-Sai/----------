import {sql} from "./cloudbase_analytics_admin.mjs";
import {readFile,writeFile} from "node:fs/promises";
const root=new URL("../outputs/verification/capture_privacy_ranking_20261020/",import.meta.url);
const definition=JSON.parse(await readFile(new URL("current-rpc.json",root),"utf8"))[0][0];
const jobs=JSON.parse(await readFile(new URL("jobs.json",root),"utf8"));
let query=definition.slice(definition.indexOf("with filtered as ("),definition.indexOf("from enriched as page;")+"from enriched as page;".length).replace(/\binto raw_rows\b/,"");
const values={
 safe_character_key:"''::text",safe_jobs:`array[${jobs.map(j=>`'${j}'`).join(",")}]`,
 safe_nests:"array['梦魇海龙']",safe_character_search:"''::text",safe_completion_status:"'cleared'::text",
 safe_score_schema_versions:"'{}'::integer[]",safe_sort_key:"'actualDps'::text",safe_sort_direction:"'desc'::text",
 safe_limit:"20",sort_category:"'number'::text",cursor_present:"false",cursor_null:"false",
 cursor_time:"null::timestamptz",cursor_recorded_at:"null::timestamptz",cursor_id:"null::bigint",
 cursor_number:"null::double precision",cursor_text:"null::text",
 p_min_score:"null::double precision",p_max_score:"null::double precision",
 p_recorded_from:"null::timestamptz",p_recorded_to:"null::timestamptz",
};
for(const [k,v] of Object.entries(values))query=query.replace(new RegExp(`\\b${k}\\b`,"g"),`(${v})`);
const migration=await readFile(new URL("../cloudbase/postgresql/048_capture_privacy_query_performance.sql",import.meta.url),"utf8");
const candidate=process.argv.includes("--candidate");
const prefix=candidate?migration.replace(/commit;\s*$/i,""):"begin;";
const result=await sql(`${prefix}set local statement_timeout='30s';set local role service_role;explain(analyze,buffers,format json) ${query} rollback;`);
await writeFile(new URL(candidate?"explain-candidate.json":"explain-before.json",root),JSON.stringify(result,null,2));
const row=JSON.parse(result.Rows[0])[0];
const plan=typeof row==="string"?JSON.parse(row):row;
console.log(JSON.stringify(plan).slice(0,600));
function visit(node,depth=0){
 console.log(`${" ".repeat(depth)}${node["Node Type"]} ${node["Relation Name"]||""} rows=${node["Actual Rows"]} loops=${node["Actual Loops"]} ms=${node["Actual Total Time"]} removed=${node["Rows Removed by Join Filter"]||node["Rows Removed by Filter"]||0} est=${node["Plan Rows"]}`);
 if(node.Filter||node["Hash Cond"])console.log(JSON.stringify({filter:node.Filter,hash:node["Hash Cond"]}));
 for(const p of node.Plans||[])visit(p,depth+1);
}
visit(plan[0].Plan);
console.log(JSON.stringify(await sql(`select s.job,count(*),count(*) filter(where f.team_score_id is null) missing_facts
from score_public.team_scores s left join score_public.team_score_ranking_facts f on f.team_score_id=s.id
where s.nest='梦魇海龙' group by 1 order by 1;`)));
