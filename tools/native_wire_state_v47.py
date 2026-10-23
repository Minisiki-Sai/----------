"""Small offline network-state replica, not a game engine or skill oracle.

Inputs are explicitly bounded native streams and ordered actor facts. No game
code, rendering, physics, AI, random-number-driven object creation or guessed
ID counters run here. No CombatEvent is emitted and no production code imports
this experimental module. Skill certification is deliberately separate.
"""
from collections import Counter, OrderedDict
from dataclasses import dataclass
from hashlib import sha256
import math

from tools.audit_native_damage_v46 import parse_record as read_damage
from tools.audit_projectile_stream_v45 import parse_prefix as read_projectile
from tools.trace_native_object_creation_v46 import compact_prefix as read_compact


@dataclass(frozen=True, slots=True)
class ActorState:
    actor_id: int
    dispatch: str
    generation: int
    owner_id: int | None
    template_id: int | None
    proof: str
    order: tuple


@dataclass(frozen=True, slots=True)
class ObjectState:
    namespace: str
    reference: int
    generation: int
    actor_id: int
    actor_generation: int
    owner_id: int | None
    config_row: int | None
    action: int
    signal: int
    created_at: float
    expires_at: float
    order: tuple
    semantic_key: tuple
    lineage: tuple
    root_context_known: bool
    limitation: str
    proof: str


@dataclass(frozen=True, slots=True)
class DamageObservation:
    source_id: int
    target_id: int
    damage: int
    critical: bool
    namespace: str
    reference: int
    status: str
    object_generation: int | None = None
    owner_id: int | None = None
    config_row: int | None = None
    lineage: tuple = ()
    root_context_known: bool = False
    # Reading a template and resolving a handle is NOT a named-skill proof.
    skill_certified: bool = False


class NativeWireState:
    def __init__(self, scope, *, max_objects=4096, max_actors=512,
                 max_tombstones=8192, max_age=120.0, max_depth=8, shared_history_filter=None):
        if not scope:
            raise ValueError('explicit replay/connection scope required')
        if not math.isfinite(max_age) or max_age <= 0 or max_depth < 1:
            raise ValueError('finite positive state limits required')
        if min(max_objects,max_actors,max_tombstones)<1:
            raise ValueError('positive capacities required')
        self.scope=scope
        self.max_objects=max_objects;self.max_actors=max_actors
        self.max_tombstones=max_tombstones;self.max_age=max_age;self.max_depth=max_depth
        self.actors=OrderedDict();self.objects=OrderedDict();self.seen=OrderedDict()
        # Fixed memory, one-way membership: tombstone eviction must never
        # re-admit a forgotten replay. A Bloom false positive only rejects a
        # candidate; it never creates/renews an object or certifies a skill.
        if shared_history_filter is not None and (not isinstance(shared_history_filter,bytearray)
                or len(shared_history_filter)!=(1<<20)):
            raise ValueError('shared replay filter must be an explicit 1MiB bytearray')
        self.seen_filter=shared_history_filter if shared_history_filter is not None else bytearray(1<<20)
        self.actor_serial=self.object_serial=self.epoch=0
        self.last_order=(-1,-1);self.last_time=-math.inf
        self.stats=Counter()

    def _advance(self, order, timestamp):
        order=tuple(order)
        if (len(order)!=2 or any(not isinstance(v,int) for v in order)
                or min(order)<0 or order<self.last_order or not math.isfinite(timestamp)
                or timestamp<self.last_time):
            raise ValueError('monotonic finite physical order/time required')
        self.last_order=tuple(order);self.last_time=timestamp

    def reset_scene(self, proof):
        if not proof:
            raise ValueError('scene/reset evidence required')
        self.actors.clear();self.objects.clear();self.epoch+=1
        # Captured old registration retransmissions cannot resurrect objects.
        # Bounded evicted tombstones still require fresh current actor evidence.
        self.stats['scene_resets']+=1

    def observe_actor(self, actor_id, dispatch, order, timestamp, proof, *, owner_id=None, template_id=None):
        if dispatch not in {'player','entity'} or not proof or not 0<actor_id<0xffffffff:
            raise ValueError('explicit positive actor fact required')
        self._advance(order,timestamp)
        old=self.actors.get(actor_id)
        identity=(dispatch,owner_id,template_id,proof)
        if old and (old.dispatch,old.owner_id,old.template_id,old.proof)==identity:
            return old
        self.actor_serial+=1
        fact=ActorState(actor_id,dispatch,self.actor_serial,owner_id,template_id,proof,tuple(order))
        self.actors[actor_id]=fact;self.actors.move_to_end(actor_id)
        while len(self.actors)>self.max_actors:
            self.actors.popitem(last=False);self.stats['actor_evictions']+=1
        return fact

    def retire_actor(self, actor_id, order, timestamp, proof):
        if not proof:
            raise ValueError('retirement evidence required')
        self._advance(order,timestamp)
        self.actors.pop(actor_id,None)
        self.stats['actor_retirements']+=1

    def _remember(self, token):
        if token in self.seen:
            return False
        digest=sha256(repr(token).encode('utf-8')).digest()
        indexes=[int.from_bytes(digest[i:i+4],'little') & ((len(self.seen_filter)*8)-1) for i in (0,4,8,12)]
        if all(self.seen_filter[index>>3] & (1<<(index&7)) for index in indexes):
            self.stats['possible_old_registration_replays']+=1
            return False
        for index in indexes:self.seen_filter[index>>3] |= 1<<(index&7)
        self.seen[token]=None
        while len(self.seen)>self.max_tombstones:
            self.seen.popitem(last=False)
        return True

    def _lookup(self, namespace, reference, source_id, timestamp):
        obj=self.objects.get((namespace,reference))
        if obj is None:
            return None,'no_registered_reference'
        if obj.actor_id!=source_id:
            return None,'cross_source_reference'
        actor=self.actors.get(source_id)
        if actor is None or actor.generation!=obj.actor_generation:
            return None,'stale_actor_generation'
        if not obj.created_at <= timestamp <= obj.expires_at:
            return None,'outside_observed_lifetime'
        return obj,'object_reference_resolved'

    def _register(self, actor, reference, config, action, signal, order, timestamp, proof,
                  semantic_key, *, parent_namespace=None,parent_ref=None,root_known=False,limitation=''):
        if not proof:
            raise ValueError('registration evidence required')
        if not 0<reference<0xffffffff:
            self.stats['invalid_object_reference']+=1
            return None
        token=(self.scope,actor.actor_id,proof)
        exact_retransmission=token in self.seen
        if not self._remember(token):
            if not exact_retransmission:
                # A Bloom positive is uncertainty, not an exact duplicate.
                # In particular, rejecting a potentially conflicting new
                # registration must not leave an old config authoritative.
                self.objects.pop(('index',reference),None)
                self.stats['uncertain_replay_handle_invalidations']+=1
            self.stats['registration_retransmissions']+=1
            return None  # a retired/reused handle is not this old registration
        previous=self.objects.get(('index',reference))
        lineage=()
        if parent_ref is not None:
            parent,reason=self._lookup(parent_namespace,parent_ref,actor.actor_id,timestamp)
            if parent is not None and parent.order<tuple(order) and parent.reference!=reference:
                lineage=parent.lineage+((parent.namespace,parent.reference,parent.generation,parent.proof),)
                if len(lineage)>self.max_depth:
                    self.objects.pop(('index',reference),None)
                    self.stats['lineage_depth_rejections']+=1
                    return None
                root_known=parent.root_context_known
                limitation=parent.limitation
            else:
                root_known=False
                limitation='parent_'+(reason if parent is None else 'not_prior_distinct_object')
        if (previous and previous.actor_generation==actor.generation
                and previous.actor_id==actor.actor_id and previous.semantic_key==semantic_key):
            if previous.lineage!=lineage or previous.root_context_known!=root_known:
                self.objects.pop(('index',reference),None)
                self.stats['ambiguous_changed_parent_rejections']+=1
                return None
            self.stats['same_state_updates']+=1
            return previous  # never renew creation time from a repeated state
        self.object_serial+=1
        obj=ObjectState('index',reference,self.object_serial,actor.actor_id,actor.generation,
                        actor.owner_id,config,action,signal,timestamp,timestamp+self.max_age,
                        tuple(order),semantic_key,lineage,root_known,limitation,proof)
        self.objects[('index',reference)]=obj;self.objects.move_to_end(('index',reference))
        while len(self.objects)>self.max_objects:
            self.objects.popitem(last=False);self.stats['object_evictions']+=1
        self.stats['registrations']+=1
        return obj

    def projectile_stream(self, actor_id, stream, order, timestamp, proof):
        """a90370 stream; caller selects a proven outer envelope/dispatch path."""
        parsed=read_projectile(stream,0,len(stream))  # parse before mutation
        self._advance(order,timestamp)
        actor=self.actors.get(actor_id)
        if actor is None or not proof:
            self.stats['missing_caller_context']+=1
            return None
        root=parsed.reference_branch==0 and parsed.reference_id==actor.actor_id
        parent_ref=parent_ns=None
        limitation='' if root else 'unsupported_reference_context'
        if parsed.reference_branch==1 and parsed.reference_id==actor.actor_id:
            parent_ns='uid' if parsed.inventory_uid_branch else 'index'
            parent_ref=parsed.inventory_uid if parsed.inventory_uid_branch else parsed.indexed_item_reference
        semantic=(parsed.equipment_branch,parsed.object_index,parsed.config_row,
                  parsed.reference_branch,parsed.reference_id,parsed.inventory_uid_branch,
                  parsed.inventory_uid,parsed.indexed_item_reference,parsed.action_resource,
                  parsed.child_sequence,parsed.inline_definition_branch)
        if parsed.equipment_branch:
            # Native path selects an existing item; no config is inferred.
            limitation='existing_item_config_not_reconstructed'
        return self._register(actor,parsed.object_index,parsed.config_row,parsed.action_resource,
                              parsed.child_sequence,order,timestamp,proof,semantic,
                              parent_namespace=parent_ns,parent_ref=parent_ref,
                              root_known=root,limitation=limitation)

    def retire_object(self, namespace, reference, order, timestamp, proof):
        if not proof or namespace not in {'index','uid'}:
            raise ValueError('explicit reference retirement evidence required')
        self._advance(order,timestamp)
        self.objects.pop((namespace,reference),None)
        self.stats['object_retirements']+=1

    def registration_record(self, record, order, timestamp, proof):
        """Explicitly framed server record. Known actor class selects kind9."""
        if len(record)<7 or int.from_bytes(record[5:7],'little')+7!=len(record):
            raise ValueError('complete exact registration record required')
        actor_id=int.from_bytes(record[:4],'little');kind=record[4]
        actor=self.actors.get(actor_id)
        if actor is None:
            self._advance(order,timestamp);self.stats['missing_caller_context']+=1
            return None
        if (kind==9 and actor.dispatch=='player') or (kind==56 and actor.dispatch=='entity'):
            return self.projectile_stream(actor_id,record[7:],order,timestamp,proof)
        if kind!=9 or actor.dispatch!='entity':
            self._advance(order,timestamp);self.stats['unmodeled_dispatch']+=1
            return None
        parsed=read_compact(record)
        self._advance(order,timestamp)
        # Action/signal lookup is retained, but no table is guessed from ID.
        return self._register(actor,parsed.object_index,None,parsed.action_index_u32,
                              parsed.signal_key_u32,order,timestamp,proof,
                              ('compact',parsed.action_index_u32,parsed.signal_key_u32),
                              root_known=True,limitation='requires_entity_ACT_signal_resolution')

    def damage_record(self, record, order, timestamp):
        parsed=read_damage(record)
        self._advance(order,timestamp)
        ns='uid' if parsed.uid_branch else 'index'
        ref=parsed.weapon_uid if parsed.uid_branch else parsed.indexed_object_id
        obj,reason=self._lookup(ns,ref,parsed.source_id,timestamp)
        if parsed.source_kind!=1:
            obj,reason=None,'source_kind_not_actor'
        self.stats['damage_records']+=1
        self.stats[reason]+=1
        return DamageObservation(parsed.source_id,int.from_bytes(record[:4],'little'),
            parsed.damage_i64,bool(parsed.result_flags_i8&1),ns,ref,reason,
            obj.generation if obj else None,obj.owner_id if obj else None,
            obj.config_row if obj else None,obj.lineage if obj else (),
            obj.root_context_known if obj else False)

    def inventory_state(self):
        return dict(scope=self.scope,epoch=self.epoch,actors=len(self.actors),
                    objects=len(self.objects),tombstones=len(self.seen),
                    replay_filter_bytes=len(self.seen_filter),stats=dict(self.stats))
