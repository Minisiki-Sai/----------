import {sql} from "./cloudbase_analytics_admin.mjs";
for(const query of [
 `select column_name,is_generated,generation_expression from information_schema.columns where table_schema='public' and table_name='team_score_extended_details' and column_name like 'history_%';`,
 `select count(*) records,
 count(*) filter(where e.history_completion_status is distinct from public.team_score_completion_status(e.score_detail)) completion_mismatch,
 count(*) filter(where e.history_output_seconds is distinct from public.team_score_output_seconds(e.score_detail)) duration_mismatch,
 count(*) filter(where e.history_actual_dps is distinct from
 case when jsonb_typeof(e.score_detail->'actual_dps')='number' then greatest(0::double precision,(e.score_detail->>'actual_dps')::double precision) else null end) dps_mismatch
 from public.team_score_extended_details e join public.team_scores s on s.id=e.team_score_id where s.job in('输出药剂','辅助药剂');`,
 `select s.job,count(*),count(h.team_score_id),count(e.history_completion_status)
 from score_public.team_scores s left join public.team_score_history_summaries h on h.team_score_id=s.id
 left join public.team_score_extended_details e on e.team_score_id=s.id
 where s.nest='梦魇海龙' and s.job in('输出药剂','辅助药剂') group by 1;`,
 `select pg_get_functiondef('public.team_score_is_rankable_second_job(text)'::regprocedure);`,
 `select count(*) from public.team_scores s left join public.team_score_ranking_facts f on f.team_score_id=s.id
 where s.job in('输出药剂','辅助药剂') and f.team_score_id is null;`,
 ])console.log(JSON.stringify(await sql(query)));
