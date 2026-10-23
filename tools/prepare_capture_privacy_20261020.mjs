import {sql} from "./cloudbase_analytics_admin.mjs";
import {mkdir,writeFile} from "node:fs/promises";
const root=new URL("../outputs/verification/capture_privacy_20261020/",import.meta.url);
await mkdir(root,{recursive:true});
const queries={
 captures:`select distinct s.capture_hash from public.team_scores s join public.team_score_character_identities i on i.team_score_id=s.id where i.character_name='冷艳高傲绝世幻' and s.nest='梦魇海龙' order by 1`,
 functions:`select p.oid::text, p.proname, pg_get_function_identity_arguments(p.oid),pg_get_functiondef(p.oid) from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='public' and p.prokind='f' and (p.prosrc like '%team_score%' or p.proname like '%capture%') order by 2,3`,
 tables:`select table_name,string_agg(column_name,',' order by ordinal_position) from information_schema.columns where table_schema='public' and (table_name like '%score%' or table_name like '%debuff%') group by 1 order by 1`,
};
for(const [name,q] of Object.entries(queries)){
 const r=await sql(q);const rows=(r.Rows||[]).map(JSON.parse);
 await writeFile(new URL(`${name}.json`,root),JSON.stringify(rows,null,2));
 console.log(JSON.stringify({name,count:rows.length,values:name==="functions"?rows.map(r=>[r[1],r[2]]):rows}));
}
