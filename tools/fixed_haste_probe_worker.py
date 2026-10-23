"""Temporary process-local pinning adapter used ONLY by offline cost probes."""
from dragon_nest_builder import timeline_parallel as pool

_original_calculate=pool.calculate_extreme_timeline_task

def calculate_task(task):
    from dragon_nest_builder.timeline_engine import _timeline_static_descriptor
    if pool._WORKER_EDITOR_TYPE is None:
        pool._initialize_timeline_worker()
    owner_type=pool._WORKER_EDITOR_TYPE
    original=owner_type._update_skill_burst_sim_preview
    casts={skill:rows for (provider,skill),rows in (task.cast_schedule or {}).items()
           if provider==task.provider_override and skill in {'幽灵之魂','怨恨之像'}}
    applied=[0]
    def pinned_preview(self,*args,**kwargs):
        if casts:
            applied[0]+=1
            self._skill_burst_pinned_support_cast_schedule={
                **getattr(self,'_skill_burst_pinned_support_cast_schedule',{}),**casts}
            for flag in ('_skill_burst_disable_new_scheme_1_refinement',
                         '_skill_burst_disable_conservative_1_6',
                         '_skill_burst_disable_local_optimizer',
                         '_skill_burst_disable_hybrid_optimizer'):
                setattr(self,flag,True)
        return original(self,*args,**kwargs)
    owner_type._update_skill_burst_sim_preview=pinned_preview
    _timeline_static_descriptor.cache_clear()
    try:
        result=_original_calculate(task)
        assert not casts or applied[0], 'Own support pinning was not applied'
        result['_diagnostic_own_support_pins']=casts
        return result
    finally:
        owner_type._update_skill_burst_sim_preview=original
        _timeline_static_descriptor.cache_clear()
        # No bound diagnostic method may leak to later ordinary score jobs.
        pool._WORKER_OWNERS.clear()
