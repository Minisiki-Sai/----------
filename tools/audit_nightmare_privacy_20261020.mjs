import {sql} from "./cloudbase_analytics_admin.mjs";
const queries = [
  ["scope", `with captures as (select distinct s.capture_hash from team_scores s join team_score_character_identities i on i.team_score_id=s.id where i.character_name='冷艳高傲绝世幻' and s.nest='梦魇海龙'), names as(select distinct i.character_name from team_scores s join team_score_character_identities i on i.team_score_id=s.id where s.capture_hash in(select capture_hash from captures)) select i.character_name,count(*) as all_nightmare_rows,count(distinct s.capture_hash) as captures,count(*) filter(where s.capture_hash in(select capture_hash from captures)) as shared_rows from team_scores s join team_score_character_identities i on i.team_score_id=s.id where i.character_name in(select character_name from names) and s.nest='梦魇海龙' group by 1 order by 1;`],
  ["target", `select i.character_name,s.nest,count(*),count(distinct s.capture_hash),min(s.recorded_at),max(s.recorded_at) from team_scores s join team_score_character_identities i on i.team_score_id=s.id where i.character_name like '%冷艳高傲绝世幻%' group by 1,2;`],
  ["teammates", `with captures as (select distinct s.capture_hash from team_scores s join team_score_character_identities i on i.team_score_id=s.id where i.character_name='冷艳高傲绝世幻' and s.nest='梦魇海龙') select i.character_name,s.job,count(*),count(distinct s.capture_hash) from team_scores s left join team_score_character_identities i on i.team_score_id=s.id where s.capture_hash in(select capture_hash from captures) and s.nest='梦魇海龙' group by 1,2 order by 1,2;`],
  ["read-functions", `select proname from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='public' and (proname like 'get_%score%' or proname like '%ranking%' or proname like '%capture%') order by 1;`],
];
for (const [name,query] of queries) {
  const result=await sql(query);
  console.log(JSON.stringify({name,rows:result.Rows?.map(JSON.parse),error:result.Error}));
}
