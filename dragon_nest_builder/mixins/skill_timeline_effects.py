from __future__ import annotations

from functools import lru_cache
import os


_EXACT_ROUND2 = os.environ.get('DN_SCORE_EXACT_ROUND2', '1') == '1'
_EXACT_ROUND3 = os.environ.get('DN_SCORE_EXACT_ROUND3', '1') == '1'

class SkillTimelineEffectsMixin:
    def _skill_burst_effect_signature_fields(
        self,
        effect: dict[str, Any],
    ) -> tuple[Any, ...]:
        """Build the shared exact fields used by all three effect caches."""

        attrs = effect.get("attrs", {})
        display_attrs = effect.get("display_attrs", attrs)
        cached = effect.get("_skill_burst_runtime_signature_fields")
        if (
            isinstance(cached, tuple)
            and len(cached) == 4
            and cached[0] is attrs
            and cached[1] is display_attrs
            and cached[3] == id(effect)
        ):
            return cached[2]
        attrs_signature = tuple(
            (repr(key), repr(value))
            for key, value in attrs.items()
        )
        display_signature = tuple(
            (repr(key), repr(value))
            for key, value in display_attrs.items()
        )
        result = (
            repr(effect.get("row")),
            repr(effect.get("provider")),
            repr(effect.get("skill")),
            repr(effect.get("level")),
            bool(effect.get("stackable")),
            repr(effect.get("cooldown")),
            repr(effect.get("duration")),
            repr(effect.get("usage_ratio", 100.0)),
            attrs_signature,
            display_signature,
        )
        # Runtime effects are freshly assembled for one state lookup. The
        # combined, merge and panel caches consume that same dictionary in
        # succession, so retain the shared normalization only on these private
        # rows; caller-owned configuration effects remain untouched.
        if effect.get("_skill_burst_runtime_effect"):
            effect["_skill_burst_runtime_signature_fields"] = (
                attrs,
                display_attrs,
                result,
                id(effect),
            )
        return result

    def _skill_burst_player_buff_clear_times(self) -> tuple[float, ...]:
        rows = getattr(self, "_skill_burst_player_buff_clear_times_override", ())
        if not isinstance(rows, (list, tuple, set, frozenset)):
            return ()
        # A score timeline asks for the same clear-time tuple from virtually
        # every candidate comparison.  The override is immutable for the
        # lifetime of one timeline proxy, so normalizing and sorting it again
        # hundreds of thousands of times is pure overhead.  Lists/sets remain
        # uncached because extension code may mutate them in place.
        if isinstance(rows, (tuple, frozenset)):
            cached = getattr(
                self, "_skill_burst_player_buff_clear_times_cache", None
            )
            if (
                isinstance(cached, tuple)
                and len(cached) == 2
                and cached[0] is rows
            ):
                return cached[1]
            result = tuple(
                sorted({max(0.0, as_number(value)) for value in rows})
            )
            self._skill_burst_player_buff_clear_times_cache = (rows, result)
            return result
        return tuple(sorted({max(0.0, as_number(value)) for value in rows}))

    def _skill_burst_panel_state_signature(
        self,
        effects: list[dict[str, Any]],
    ) -> tuple[Any, ...]:
        """Return the exact inputs that can affect active-effect deduplication.

        Application timestamps intentionally do not participate: the existing
        panel bundle is defined by the currently active mechanical effects and
        already ignores refresh time after deduplication.
        """

        if _EXACT_ROUND3:
            registry = getattr(self, '_skill_burst_merged_effect_token_registry', None)
            if not isinstance(registry, dict):
                registry = {}
                self._skill_burst_merged_effect_token_registry = registry
            namespace = registry.setdefault('panel_namespace', object())
            previous = getattr(self, '_skill_burst_panel_tokens_last', None)
            if (previous is not None and previous[0] is effects
                    and previous[2] is namespace):
                return previous[1]
            tokens = registry.setdefault('panel_tokens', {})
            rows = registry.setdefault('panel_immutable_rows', {})
            result_tokens = []
            immutable = True
            for effect in effects:
                remembered = rows.get(id(effect))
                if remembered is not None and remembered[0] is effect:
                    result_tokens.append(remembered[1])
                    continue
                fields = self._skill_burst_effect_signature_fields(effect)
                descriptor = (fields[1], fields[2], fields[4], fields[5],
                              fields[6], fields[8], fields[9])
                token = tokens.get(descriptor)
                if token is None:
                    token = registry.get('panel_serial', 0) + 1
                    registry['panel_serial'] = token
                    tokens[descriptor] = token
                    if len(tokens) > 32768:
                        tokens.pop(next(iter(tokens)))
                is_runtime = bool(effect.get('_skill_burst_runtime_effect'))
                immutable = immutable and is_runtime
                if is_runtime:
                    if len(rows) >= 32768:
                        rows.pop(next(iter(rows)))
                    rows[id(effect)] = (effect, token)
                result_tokens.append(token)
            # This registry is shared alongside the panel caches. Including
            # its namespace also prevents accidental reuse after replacement.
            result = ('exact-panel-tokens-v1', namespace, *result_tokens)
            # The active-effect list is read twice by the final-state and
            # panel consumers. Reuse that exact private list; never retain a
            # mutable configuration list as an identity-only cache key.
            self._skill_burst_panel_tokens_last = (effects, result, namespace) if immutable else None
            return result
        cached = getattr(
            self, "_skill_burst_panel_state_signature_last", None
        )
        if (
            isinstance(cached, tuple)
            and len(cached) == 2
            and cached[0] is effects
        ):
            return cached[1]
        result = tuple(
            (
                fields[1],
                fields[2],
                fields[4],
                fields[5],
                fields[6],
                fields[8],
                fields[9],
            )
            for fields in (
                self._skill_burst_effect_signature_fields(effect)
                for effect in effects
            )
        )
        # The same freshly assembled active-effect list is consumed first by
        # the periodic-state cache and then by the panel bundle.  Remembering
        # its exact signature avoids normalizing every field twice without
        # extending the lifetime of the list beyond the next call.
        self._skill_burst_panel_state_signature_last = (effects, result)
        return result

    def _skill_burst_merged_team_attrs_signature(
        self,
        effects: list[dict[str, Any]],
    ) -> tuple[int, ...]:
        """Return every field read by ``_merged_team_attrs``.

        This deliberately uses exact numeric values instead of rounded cache
        keys, so memoization cannot merge two states that previously produced
        different scoring output.
        """

        registry = getattr(
            self, "_skill_burst_merged_effect_token_registry", None
        )
        if not isinstance(registry, dict):
            registry = {}
            self._skill_burst_merged_effect_token_registry = registry
        namespace = registry.get("namespace")
        if namespace is None:
            namespace = object()
            registry["namespace"] = namespace
        base_tokens = registry.get("base_tokens")
        if base_tokens is None:
            base_tokens = {}
            registry["base_tokens"] = base_tokens
        role_tokens = registry.get("role_tokens")
        if role_tokens is None:
            role_tokens = {}
            registry["role_tokens"] = role_tokens
        cached = getattr(
            self, "_skill_burst_merged_team_attrs_signature_last", None
        )
        if (
            isinstance(cached, tuple)
            and len(cached) == 3
            and cached[0] is effects
            and cached[2] is namespace
        ):
            return cached[1]

        # Ordered immutable runtime rows recur across candidate replays. Keep
        # the exact order (replacement lanes are last-wins), strong references
        # (no recycled ids) and the same namespace as the merged-value cache.
        sequence_cache = None
        sequence_key = None
        if _EXACT_ROUND2:
            sequence_cache = registry.setdefault('ordered_runtime_states', {})
            sequence_key = tuple(map(id, effects))
            remembered = sequence_cache.get(sequence_key)
            if remembered is not None and all(a is b for a, b in zip(remembered[0], effects)):
                self._skill_burst_merged_team_attrs_signature_last = (effects, remembered[1], namespace)
                return remembered[1]

        effect_base_tokens: list[int] = []
        grouped_role_indexes: dict[tuple[str, str], list[int]] = {}
        import os
        immutable_tokens = (registry.setdefault('immutable_row_tokens', {})
                            if os.environ.get('DN_SCORE_EXACT_STATE_REUSE', '1') == '1' else None)
        for index, effect in enumerate(effects):
            if immutable_tokens is not None:
                remembered = immutable_tokens.get(id(effect))
                if remembered is not None and remembered[0] is effect and remembered[3] is namespace:
                    effect_base_tokens.append(remembered[1])
                    for candidate in remembered[2]:
                        grouped_role_indexes.setdefault(candidate, []).append(index)
                    continue
            # Every row marked as a runtime effect is immutable for the
            # lifetime of this timeline proxy.  The former cache still called
            # ``_skill_burst_effect_signature_fields`` before checking its
            # token, so a long exact support-order search rebuilt/validated
            # roughly three million per-effect signatures merely to discover
            # that their token was already known.  Read the immutable token
            # first; configuration rows that are not marked immutable keep the
            # full exact validation path below.
            cached_token = effect.get(
                "_skill_burst_runtime_merged_effect_base_token"
            )
            if (
                effect.get("_skill_burst_runtime_effect")
                and isinstance(cached_token, int)
                and cached_token > 0
                and effect.get(
                    "_skill_burst_runtime_merged_effect_token_namespace"
                ) is namespace
                and effect.get(
                    "_skill_burst_runtime_merged_effect_token_owner_id"
                ) == id(effect)
            ):
                base_token = cached_token
            else:
                fields = self._skill_burst_effect_signature_fields(effect)
                descriptor = (
                    fields[0],
                    fields[1],
                    fields[2],
                    fields[3],
                    fields[4],
                    fields[8],
                    fields[5],
                    fields[6],
                    fields[7],
                    repr(effect.get("stack_group")),
                    bool(effect.get("_skill_burst_runtime_effect")),
                )
                base_token = base_tokens.get(descriptor)
                if base_token is None:
                    base_token = int(registry.get("base_serial", 0)) + 1
                    registry["base_serial"] = base_token
                    base_tokens[descriptor] = base_token
                    while len(base_tokens) > 65536:
                        base_tokens.pop(next(iter(base_tokens)))
                if effect.get("_skill_burst_runtime_effect"):
                    effect[
                        "_skill_burst_runtime_merged_effect_base_token"
                    ] = base_token
                    effect[
                        "_skill_burst_runtime_merged_effect_token_namespace"
                    ] = namespace
                    effect[
                        "_skill_burst_runtime_merged_effect_token_owner_id"
                    ] = id(effect)

            effect_base_tokens.append(base_token)
            attrs = effect.get("attrs", {})
            candidates = effect.get(
                "_skill_burst_runtime_application_role_candidates_value"
            )
            if not (
                effect.get("_skill_burst_runtime_effect")
                and isinstance(candidates, tuple)
                and effect.get(
                    "_skill_burst_runtime_application_role_candidates_owner_id"
                ) == id(effect)
            ):
                relevant_attrs = tuple(
                    attr
                    for attr in (norm(raw_attr) for raw_attr in attrs)
                    if attr in {
                        "增伤%", "物理增伤%", "物攻%", "魔攻%",
                    }
                )
                candidates = ()
                if relevant_attrs:
                    skill = self._team_effect_skill_name(effect.get("skill"))
                    exclusive_group = (
                        self._skill_burst_exclusive_effect_group(skill)
                    )
                    if exclusive_group == "游侠物理增伤":
                        if any(
                            attr in {"增伤%", "物理增伤%"}
                            for attr in relevant_attrs
                        ):
                            candidates = (("physical_amp", "物理增伤%"),)
                    elif exclusive_group == "团队攻击力增益":
                        candidates = tuple(
                            ("attack_buff", attr)
                            for attr in relevant_attrs
                            if attr in {"物攻%", "魔攻%"}
                        )
                if effect.get("_skill_burst_runtime_effect"):
                    effect[
                        "_skill_burst_runtime_application_role_candidates_value"
                    ] = candidates
                    effect[
                        "_skill_burst_runtime_application_role_candidates_owner_id"
                    ] = id(effect)
            for candidate in candidates:
                grouped_role_indexes.setdefault(candidate, []).append(index)
            if immutable_tokens is not None and effect.get('_skill_burst_runtime_effect'):
                # The registry/namespace shares the exact lifetime of the
                # merge cache. Strong references prevent recycled object IDs;
                # copied rows still take the complete validation path above.
                if len(immutable_tokens) >= 65536:
                    immutable_tokens.pop(next(iter(immutable_tokens)))
                immutable_tokens[id(effect)] = (effect, base_token, candidates, namespace)

        # Merge-key construction used to traverse every active effect once to
        # derive application roles and then a second time to derive effect
        # tokens.  Roles are relevant only to the two client replacement
        # lanes below, so resolve those sparse indexes after the single token
        # traversal.  The branches exactly mirror
        # ``_skill_burst_effect_application_roles``.
        # Only two replacement lanes can own an application role.  Most
        # effects therefore have no role at all; allocating a list for every
        # active effect on every state was one of the hottest scoring paths.
        # Keep only the sparse rows that actually participate in those lanes.
        role_state_key = None
        role_states = None
        if _EXACT_ROUND3:
            role_states = registry.setdefault('resolved_role_states', {})
            # Base tokens include the lane-defining fields. Only explicit
            # application times on the sparse replacement lanes add context.
            role_state_key = (tuple(effect_base_tokens), tuple(
                (group, tuple((index, repr(effects[index].get('applied_at')))
                              for index in indexes))
                for group, indexes in grouped_role_indexes.items()))
            result = role_states.get(role_state_key)
            if result is not None:
                self._skill_burst_merged_team_attrs_signature_last = (effects, result, namespace)
                # Preserve round 2's cheaper object-sequence hit on subsequent
                # lookups too. Returning without filling it regresses repeated
                # candidate visits back to the per-effect token loop.
                if sequence_cache is not None and all(row.get('_skill_burst_runtime_effect') for row in effects):
                    if len(sequence_cache) >= 8192:
                        sequence_cache.pop(next(iter(sequence_cache)))
                    sequence_cache[sequence_key] = (tuple(effects), result)
                return result
        application_roles: dict[int, list[tuple[str, str, str]]] = {}
        for (group, attr), indexes in grouped_role_indexes.items():
            has_explicit_application = any(
                effects[index].get("applied_at") is not None
                for index in indexes
            )
            if group == "physical_amp" and has_explicit_application:
                winner = max(
                    indexes,
                    key=lambda index: (
                        as_number(effects[index].get("applied_at"), -1.0),
                        index,
                    ),
                )
                for index in indexes:
                    application_roles.setdefault(index, []).append((
                        group,
                        attr,
                        "latest" if index == winner else "overridden",
                    ))
            else:
                branch = "active" if has_explicit_application else "average"
                for index in indexes:
                    application_roles.setdefault(index, []).append(
                        (group, attr, branch)
                    )

        result_tokens: list[int] = []
        for index, base_token in enumerate(effect_base_tokens):
            sparse_roles = application_roles.get(index)
            role_key = (
                base_token,
                tuple(sorted(sparse_roles)) if sparse_roles else (),
            )
            role_token = role_tokens.get(role_key)
            if role_token is None:
                role_token = int(registry.get("role_serial", 0)) + 1
                registry["role_serial"] = role_token
                role_tokens[role_key] = role_token
                while len(role_tokens) > 65536:
                    role_tokens.pop(next(iter(role_tokens)))
            result_tokens.append(role_token)
        result = tuple(result_tokens)
        if role_states is not None:
            if len(role_states) >= 32768:
                role_states.pop(next(iter(role_states)))
            role_states[role_state_key] = result
        self._skill_burst_merged_team_attrs_signature_last = (
            effects, result, namespace
        )
        if sequence_cache is not None and all(row.get('_skill_burst_runtime_effect') for row in effects):
            if len(sequence_cache) >= 8192:
                sequence_cache.pop(next(iter(sequence_cache)))
            sequence_cache[sequence_key] = (tuple(effects), result)
        return result

    def _skill_burst_effect_application_roles(
        self,
        effects: list[dict[str, Any]],
    ) -> tuple[tuple[tuple[str, str, str], ...], ...]:
        """Reduce refresh times to the exact merge decisions they affect."""

        cached = getattr(
            self, "_skill_burst_effect_application_roles_last", None
        )
        if (
            isinstance(cached, tuple)
            and len(cached) == 2
            and cached[0] is effects
        ):
            return cached[1]

        grouped: dict[tuple[str, str], list[int]] = {}
        for index, effect in enumerate(effects):
            attrs = effect.get("attrs", {})
            cached_candidates = effect.get(
                "_skill_burst_application_role_candidates"
            )
            if (
                isinstance(cached_candidates, tuple)
                and len(cached_candidates) == 3
                and cached_candidates[0] is attrs
                and cached_candidates[2] == id(effect)
            ):
                candidates = cached_candidates[1]
            else:
                relevant_attrs = tuple(
                    attr
                    for attr in (norm(raw_attr) for raw_attr in attrs)
                    if attr in {
                        "增伤%", "物理增伤%", "物攻%", "魔攻%",
                    }
                )
                candidates: tuple[tuple[str, str], ...] = ()
                if relevant_attrs:
                    skill = self._team_effect_skill_name(effect.get("skill"))
                    exclusive_group = (
                        self._skill_burst_exclusive_effect_group(skill)
                    )
                    if exclusive_group == "游侠物理增伤":
                        if any(
                            attr in {"增伤%", "物理增伤%"}
                            for attr in relevant_attrs
                        ):
                            candidates = (("physical_amp", "物理增伤%"),)
                    elif exclusive_group == "团队攻击力增益":
                        candidates = tuple(
                            ("attack_buff", attr)
                            for attr in relevant_attrs
                            if attr in {"物攻%", "魔攻%"}
                        )
                if effect.get("_skill_burst_runtime_effect"):
                    effect["_skill_burst_application_role_candidates"] = (
                        attrs,
                        candidates,
                        id(effect),
                    )
            for candidate in candidates:
                grouped.setdefault(candidate, []).append(index)

        roles: list[list[tuple[str, str, str]]] = [
            [] for _effect in effects
        ]
        for (group, attr), indexes in grouped.items():
            has_explicit_application = any(
                effects[index].get("applied_at") is not None
                for index in indexes
            )
            if group == "physical_amp" and has_explicit_application:
                winner = max(
                    indexes,
                    key=lambda index: (
                        as_number(effects[index].get("applied_at"), -1.0),
                        index,
                    ),
                )
                for index in indexes:
                    roles[index].append((
                        group,
                        attr,
                        "latest" if index == winner else "overridden",
                    ))
            else:
                branch = "active" if has_explicit_application else "average"
                for index in indexes:
                    roles[index].append((group, attr, branch))
        result = tuple(tuple(sorted(row)) for row in roles)
        self._skill_burst_effect_application_roles_last = (effects, result)
        return result

    def _skill_burst_combined_effect_state_signature(
        self,
        effects: list[dict[str, Any]],
    ) -> tuple[Any, ...]:
        """Combine the exact merge and panel signatures in one traversal.

        Periodic rescoring previously built two independent signatures before
        it could check its state cache.  Their tuple equality is equivalent to
        equality of the union below, so this reduces normalization work while
        preserving the cache boundary exactly.
        """

        cached = getattr(
            self, "_skill_burst_combined_effect_state_signature_last", None
        )
        if (
            isinstance(cached, tuple)
            and len(cached) == 2
            and cached[0] is effects
        ):
            return cached[1]
        application_roles = self._skill_burst_effect_application_roles(effects)
        result = tuple(
            (*fields, application_roles[index])
            for index, fields in enumerate(
                self._skill_burst_effect_signature_fields(effect)
                for effect in effects
            )
        )
        self._skill_burst_combined_effect_state_signature_last = (
            effects,
            result,
        )
        return result

    def _skill_burst_cached_merged_team_attrs(
        self,
        effects: list[dict[str, Any]],
    ) -> dict[str, float]:
        identity_cache = getattr(
            self,
            "_skill_burst_merged_team_attrs_identity_cache",
            None,
        )
        if not isinstance(identity_cache, dict):
            identity_cache = {}
            self._skill_burst_merged_team_attrs_identity_cache = identity_cache
        identity_key = id(effects)
        identity_cached = identity_cache.get(identity_key)
        if (
            isinstance(identity_cached, tuple)
            and len(identity_cached) == 2
            and identity_cached[0] is effects
        ):
            return dict(identity_cached[1])
        signature = self._skill_burst_merged_team_attrs_signature(effects)
        cache = getattr(self, "_skill_burst_merged_team_attrs_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._skill_burst_merged_team_attrs_cache = cache
        cached = cache.get(signature)
        if cached is not None:
            merged = dict(cached)
        else:
            merged = self._merged_team_attrs(effects)
            cache[signature] = dict(merged)
        # A long eight-player score axis commonly owns 8k-13k exact mechanical
        # states.  The old 8192-entry FIFO evicted first-pass states immediately
        # before the exemption replan could reuse them.
        # Several same-job B/H score axes reuse these exact merged states.
        # Two long axes already fill about 31k entries; evicting at 32k
        # forces later axes to recompute the same pure merge. The token
        # registry is shared with this cache, so a larger bound is safe.
        while len(cache) > 65536:
            cache.pop(next(iter(cache)))
        if len(identity_cache) >= 16384:
            identity_cache.pop(next(iter(identity_cache)))
        identity_cache[identity_key] = (effects, dict(merged))
        return merged

    def _skill_burst_clear_immune_effect(self, effect: dict[str, Any]) -> bool:
        return player_buff_is_clear_immune(
            skill_id=effect.get("skill_id"),
            skill_name=effect.get("skill") or effect.get("source"),
            permanent=bool(
                effect.get("permanent") or effect.get("buff_clear_immune")
            ),
        )

    def _skill_burst_debuff_attr_keys(self) -> set[str]:
        """Return attrs applied to the enemy rather than removable player Buffs."""

        return {
            *self._team_resist_attr_keys(),
            *self._team_amp_attr_keys(),
            *self._team_freeze_attr_keys(),
            "减防御%",
            "减物理防御%",
            "减魔法防御%",
            "减致命抵抗%",
        }

    def _skill_burst_attrs_after_player_buff_clear(
        self,
        effect: dict[str, Any],
        attrs: dict[str, Any],
        applied_at: float,
        current: float,
    ) -> dict[str, Any]:
        """Remove timing attrs only from cleared burst/acceleration windows."""

        skill_name = (
            effect.get("skill") or effect.get("source") or effect.get("name")
        )
        if (
            not attrs
            or not player_buff_clear_affects_timeline_skill(skill_name)
            or player_buff_survives_at(
                applied_at=applied_at,
                seconds=current,
                clear_times=self._skill_burst_player_buff_clear_times(),
                skill_id=effect.get("skill_id"),
                skill_name=skill_name,
                permanent=bool(
                    effect.get("permanent") or effect.get("buff_clear_immune")
                ),
            )
        ):
            return dict(attrs)
        debuff_keys = self._skill_burst_debuff_attr_keys()
        return {
            key: value
            for key, value in attrs.items()
            if norm(key) in debuff_keys
        }

    def _skill_burst_clip_clearable_buff_intervals(
        self,
        intervals: Iterable[tuple[float, float]],
        effect: dict[str, Any] | None = None,
    ) -> tuple[tuple[float, float], ...]:
        source = effect if isinstance(effect, dict) else {}
        skill_name = (
            source.get("skill") or source.get("source") or source.get("name")
        )
        return clip_player_buff_intervals_at_clears(
            intervals,
            (
                self._skill_burst_player_buff_clear_times()
                if player_buff_clear_affects_timeline_skill(skill_name)
                else ()
            ),
            skill_id=source.get("skill_id"),
            skill_name=skill_name,
            permanent=bool(
                source.get("permanent") or source.get("buff_clear_immune")
            ),
        )

    def _skill_burst_clip_display_player_buffs(
        self,
        effects: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Clip only burst/acceleration lanes; keep ordinary Buffs and Debuffs."""

        clipped: list[dict[str, Any]] = []
        for effect in effects:
            if not isinstance(effect, dict):
                continue
            category = norm(effect.get("category"))
            if category not in {
                "team_buff",
                "team_personal",
                "personal",
                "speed",
            }:
                clipped.append(effect)
                continue
            start = max(0.0, as_number(effect.get("start")))
            end = max(start, as_number(effect.get("end")))
            for clipped_start, clipped_end in self._skill_burst_clip_clearable_buff_intervals(
                ((start, end),), effect
            ):
                clipped.append({
                    **effect,
                    "start": clipped_start,
                    "end": clipped_end,
                })
        return clipped

    @staticmethod
    def _skill_burst_resist_attr_for_element(element: str) -> str:
        return {
            "火": "减火抗%",
            "水": "减水抗%",
            "光": "减光抗%",
            "暗": "减暗抗%",
        }.get(norm(element), "")
    def _skill_burst_relevant_resist_attrs(self, metrics: Iterable[dict[str, Any]]) -> set[str]:
        elements = {
            norm(metric.get("element"))
            for metric in metrics
            if norm(metric.get("element")) in {"火", "水", "光", "暗"}
        }
        if not elements:
            element_attr = norm(self._job_profile().get("element_attr"))
            fallback = {
                "火攻%": "火",
                "水攻%": "水",
                "光攻%": "光",
                "暗攻%": "暗",
            }.get(element_attr)
            if fallback:
                elements.add(fallback)
        return {
            attr
            for attr in (self._skill_burst_resist_attr_for_element(element) for element in elements)
            if attr
        }

    def _skill_burst_resist_damage_weights(self, metrics: Iterable[dict[str, Any]]) -> dict[str, float]:
        """Estimate owner damage shares, not mere presence of an element.

        One low-level fire filler cannot make fire resistance reduction buff
        the owner's entire ice rotation. Build once per axis, using the same
        score-per-cycle units as the existing support opportunity estimate.
        Unattributed/neutral damage stays in the denominator.
        """
        totals: dict[str, float] = {}
        total = 0.0
        for metric in metrics:
            score = max(0.0, as_number(metric.get("effective_score")))
            interval = max(0.05, as_number(metric.get("base_cooldown")), as_number(metric.get("cast_time")))
            weight = score / interval
            total += weight
            attr = self._skill_burst_resist_attr_for_element(metric.get("element"))
            if attr:
                totals[attr] = totals.get(attr, 0.0) + weight
        return {attr: value / total for attr, value in totals.items()} if total > 0 else {}
    @staticmethod
    def _skill_burst_skill_key(skill: Any) -> str:
        text = skill if isinstance(skill, str) else norm(skill)
        return BuildEditor._skill_burst_skill_key_cached(text)
    @staticmethod
    @lru_cache(maxsize=8192)
    def _skill_burst_skill_key_cached(text: str) -> str:
        text = re.sub(r"\s*EX$", "", text, flags=re.IGNORECASE).strip()
        return text
    @classmethod
    def _skill_burst_exclusive_effect_group(cls, skill: Any) -> str:
        skill_name = skill if isinstance(skill, str) else norm(skill)
        return cls._skill_burst_exclusive_effect_group_cached(skill_name)

    @staticmethod
    @lru_cache(maxsize=8192)
    def _skill_burst_exclusive_effect_group_cached(skill_name: str) -> str:
        skill_name = skill_name.strip()
        skill_key = BuildEditor._skill_burst_skill_key(skill_name)
        if skill_name in TEAM_EXCLUSIVE_PHYSICAL_AMP_SKILLS or skill_key in TEAM_EXCLUSIVE_PHYSICAL_AMP_SKILLS:
            return "游侠物理增伤"
        if skill_name in TEAM_EXCLUSIVE_ATTACK_BUFF_SKILLS or skill_key in TEAM_EXCLUSIVE_ATTACK_BUFF_SKILLS:
            return "团队攻击力增益"
        return ""
    def _skill_burst_learned_ex_node_level(self, skill: Any) -> int:
        """EX eligibility is a passive tree node, never its base cast level.

        Combat levels intentionally canonicalize EX to the base cast for damage
        pricing. Using that lookup here taught sibling professions each other's
        EX effects (e.g. Elestra received Saleana's Firewall debuff).
        """
        name = re.sub(r"\s+", "", norm(skill)).casefold()
        if not name.endswith("ex"):
            return 0
        tree = self.store.skill_build_tree(self.second_job.currentText(), SKILL_BUILD_LEVEL_CAP)
        for sid, item in tree.get("skills", {}).items():
            if re.sub(r"\s+", "", norm(item.get("name"))).casefold() == name:
                return max(0, int(as_number(self._skill_build_level_for_id(sid))))
        return 0

    def _skill_burst_manual_effect_name_for_metric(self, skill: Any) -> str:
        """Return the learned manual effect attached to a castable base skill.

        EX nodes do not represent a second cast. When an EX node is learned, its
        debuff/buff belongs to the canonical base skill and shares that skill's
        action, cooldown and damage event.
        """
        skill_name = norm(skill)
        skill_key = self._skill_burst_skill_key(skill_name)
        candidates = [
            name
            for name in TEAM_MANUAL_DEBUFFS
            if self._skill_burst_skill_key(name) == skill_key
        ]
        candidates.sort(
            key=lambda name: (
                0 if re.search(r"\s*EX$", norm(name), flags=re.IGNORECASE) else 1,
                norm(name),
            )
        )
        for name in candidates:
            if re.search(r"\s*EX$", norm(name), flags=re.IGNORECASE):
                if self._skill_burst_learned_ex_node_level(name) > 0:
                    return name
        if skill_name in TEAM_MANUAL_DEBUFFS and not re.search(r"\s*EX$", skill_name, re.IGNORECASE):
            return skill_name
        if skill_key in TEAM_MANUAL_DEBUFFS:
            return skill_key
        return ""
    @staticmethod
    def _skill_burst_action_resist_values(action: dict[str, Any]) -> dict[str, float]:
        attrs = {norm(key): max(0.0, as_number(value)) for key, value in action.get("attrs", {}).items()}
        allocated = {
            norm(key): max(0.0, as_number(value))
            for key, value in action.get("resist_attrs", {}).items()
            if max(0.0, as_number(value)) > 1e-9
        }
        if not allocated:
            return {}
        fallback_raw = max([attrs.get(key, 0.0) for key in BuildEditor._team_resist_attr_keys()] or [0.0])
        out: dict[str, float] = {}
        for attr, value in allocated.items():
            raw_value = attrs.get(attr, 0.0)
            if raw_value <= 1e-9:
                raw_value = fallback_raw
            out[attr] = max(value, raw_value)
        return {attr: value for attr, value in out.items() if value > 1e-9}
    @classmethod
    def _skill_burst_resist_priority(cls, action: dict[str, Any]) -> float:
        values = cls._skill_burst_action_resist_values(action)
        weights = action.get("owner_resist_damage_weights")
        if isinstance(weights, dict):
            return sum(value * max(0.0, as_number(weights.get(attr))) for attr, value in values.items())
        return max(values.values()) if values else 0.0
    @classmethod
    def _skill_burst_collapse_exclusive_amp_effects(
        cls,
        effects: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Resolve the ranger physical-amplification replacement lane."""
        exclusive: list[tuple[int, dict[str, Any]]] = []
        others: list[dict[str, Any]] = []
        for order, row in enumerate(effects):
            category = norm(row.get("category"))
            effect = row.get("team_effect", {})
            raw_skill = effect.get("skill") if isinstance(effect, dict) else ""
            skill = cls._team_effect_skill_name(raw_skill) or norm(row.get("source"))
            if (
                category in {"amp", "team_amp"}
                and cls._skill_burst_exclusive_effect_group(skill) == "游侠物理增伤"
            ):
                exclusive.append((order, row))
            else:
                others.append(row)
        if not exclusive:
            return effects

        boundaries = sorted({
            max(0.0, as_number(row.get(key)))
            for _order, row in exclusive
            for key in ("start", "end")
        })
        rows_by_order = {order: row for order, row in exclusive}
        inputs = [
            EffectInput(
                key="物理增伤%",
                value=1.0,
                skill_name=(
                    cls._team_effect_skill_name(
                        (row.get("team_effect") or {}).get("skill")
                    )
                    or norm(row.get("source"))
                ),
                source_id=norm(row.get("source")),
                target_id="boss",
                instance_id=f"physical-amp:{order}",
                start=max(0.0, as_number(row.get("start"))),
                end=max(
                    max(0.0, as_number(row.get("start"))),
                    as_number(row.get("end")),
                ),
                stack_group="client:ranger-physical-amp",
                group_mode="replace",
                metadata={"order": order},
            )
            for order, row in exclusive
        ]
        collapsed: list[dict[str, Any]] = []
        for start, end in zip(boundaries, boundaries[1:]):
            if end <= start + 1e-6:
                continue
            resolution = EFFECT_STACKING_ENGINE.resolve_active(
                inputs, (start + end) / 2.0
            )
            selected = next(
                (
                    item
                    for item in resolution.effective
                    if item.rule.stack_group == "client:ranger-physical-amp"
                ),
                None,
            )
            if selected is None:
                continue
            winner = rows_by_order.get(
                int(selected.effect.metadata.get("order", -1))
            )
            if winner is None:
                continue
            current = dict(winner)
            effect = current.get("team_effect", {})
            raw_skill = effect.get("skill") if isinstance(effect, dict) else ""
            source = cls._team_effect_skill_name(raw_skill) or norm(current.get("source"))
            attrs_text = norm(current.get("attrs_text"))
            if not attrs_text and isinstance(effect, dict):
                attrs = effect.get("display_attrs") or effect.get("effective_attrs") or effect.get("attrs") or {}
                attrs_text = attr_summary(attrs, limit=999)
            current.update({
                "start": start,
                "end": end,
                "source": source,
                "name": "｜".join(filter(None, ["当前物理增伤", source, attrs_text])),
                "detail": "；".join(filter(None, ["同组后施放覆盖前一个", attrs_text])),
            })
            if (
                collapsed
                and norm(collapsed[-1].get("source")) == source
                and norm(collapsed[-1].get("category")) == norm(current.get("category"))
                and norm(collapsed[-1].get("attrs_text")) == norm(current.get("attrs_text"))
                and abs(as_number(collapsed[-1].get("end")) - start) <= 1e-6
            ):
                collapsed[-1]["end"] = end
            else:
                collapsed.append(current)
        return [*others, *collapsed]
    @staticmethod
    def _skill_burst_collapse_team_resist_effects(
        effects: list[dict[str, Any]],
        relevant_attrs: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Resolve team resistance windows with the shared client rules."""
        resist_effects = [row for row in effects if norm(row.get("category")) == "team_resist"]
        if not resist_effects:
            return effects
        others = [row for row in effects if norm(row.get("category")) != "team_resist"]
        element_attrs = {
            "光": "减光抗%",
            "暗": "减暗抗%",
            "水": "减水抗%",
            "火": "减火抗%",
        }
        boundaries = sorted({
            max(0.0, as_number(row.get(key)))
            for row in resist_effects
            for key in ("start", "end")
        })

        def resist_attrs(row: dict[str, Any]) -> dict[str, float]:
            effect = row.get("team_effect", {})
            source = (
                effect.get("display_attrs") or effect.get("effective_attrs") or effect.get("attrs") or {}
                if isinstance(effect, dict)
                else {}
            )
            if not source:
                source = row.get("display_attrs") or row.get("attrs") or {}
            return {
                norm(key): max(0.0, as_number(value))
                for key, value in source.items()
                if norm(key).startswith("减")
                and norm(key).endswith("抗%")
                and "致命" not in norm(key)
                and as_number(value) > 0
            }

        # Build every application before slicing the display timeline.  The
        # engine can then retain admission failures across later slices, so a
        # weak client-group-36 effect rejected under a stronger effect does not
        # incorrectly reappear after the stronger effect expires.
        values_by_id = {id(row): resist_attrs(row) for row in resist_effects}
        available_attrs = {
            attr
            for row in resist_effects
            for attr in values_by_id[id(row)]
            if attr in element_attrs.values()
        }
        if any("减全抗%" in values_by_id[id(row)] for row in resist_effects):
            available_attrs.update(element_attrs.values())
        target_attrs = (
            set(relevant_attrs) & set(element_attrs.values())
            if relevant_attrs is not None
            else available_attrs
        )
        if not target_attrs:
            return others

        inputs: list[EffectInput] = []
        for row_index, row in enumerate(resist_effects):
            start = max(0.0, as_number(row.get("start")))
            end = max(start, as_number(row.get("end")))
            team_effect = row.get("team_effect", {})
            if not isinstance(team_effect, dict):
                team_effect = {}
            skill = BuildEditor._team_effect_skill_name(
                team_effect.get("skill")
                or row.get("skill")
                or row.get("source")
                or row.get("name")
            )
            source = norm(team_effect.get("provider")) or norm(row.get("source"))
            for attr, value in values_by_id[id(row)].items():
                hit_assumed = effect_rule(EffectInput(
                    key=attr, value=value, skill_name=skill,
                )).group_mode == "exclusive_admission"
                inputs.append(
                    EffectInput(
                        key=attr,
                        value=value,
                        skill_name=skill,
                        source_id=source,
                        target_id="boss",
                        instance_id=f"team:{row_index}:{attr}",
                        start=start,
                        end=end,
                        stack_group=(
                            "theory:hit-assumed-resist" if hit_assumed
                            else norm(team_effect.get("stack_group"))
                        ),
                        group_mode="max" if hit_assumed else "",
                        stackable=bool(row.get("stackable"))
                        or bool(team_effect.get("stackable")),
                        metadata={"row_index": row_index},
                    )
                )

        collapsed: list[dict[str, Any]] = []
        for start, end in zip(boundaries, boundaries[1:]):
            if end <= start + 1e-6:
                continue
            resolution = EFFECT_STACKING_ENGINE.resolve_active(
                inputs, (start + end) / 2.0
            )
            for lane_order, (element, attr) in enumerate(element_attrs.items()):
                if attr not in target_attrs:
                    continue
                total = max(0.0, as_number(resolution.values.get(attr)))
                if total <= 0:
                    continue
                selected_indexes = list(
                    dict.fromkeys(
                        int(item.effect.metadata.get("row_index", -1))
                        for item in resolution.effective
                        if item.rule.key in {attr, "减全抗%"}
                    )
                )
                winners = [
                    resist_effects[index]
                    for index in selected_indexes
                    if 0 <= index < len(resist_effects)
                ]
                if not winners:
                    continue
                sources = unique([
                    BuildEditor._team_effect_skill_name(
                        (row.get("team_effect") or {}).get("skill")
                    )
                    or norm(row.get("source"))
                    for row in winners
                ])
                source_text = "+".join(source for source in sources if source)
                attrs_text = f"{attr}+{total:g}"
                current = {
                    "name": "｜".join(filter(None, [f"当前减{element}抗", attrs_text, f"来源：{source_text}" if source_text else ""])),
                    "start": start,
                    "end": end,
                    "color": SKILL_BURST_EFFECT_COLORS["team_resist"],
                    "detail": "；".join(filter(None, [attrs_text, f"来源：{source_text}" if source_text else ""])),
                    "category": "team_resist",
                    "source": "当前减抗",
                    "lane_key": attr,
                    "lane_label": f"{element}减抗",
                    "lane_order": lane_order,
                    "attrs": {attr: total},
                    "display_attrs": {attr: total},
                    "attrs_text": attrs_text,
                }
                previous = next(
                    (
                        row for row in reversed(collapsed)
                        if norm(row.get("lane_key")) == attr
                        and abs(as_number(row.get("end")) - start) <= 1e-6
                    ),
                    None,
                )
                if (
                    previous is not None
                    and norm(previous.get("attrs_text")) == attrs_text
                    and norm(previous.get("detail")) == norm(current.get("detail"))
                ):
                    previous["end"] = end
                else:
                    collapsed.append(current)
        return [*others, *collapsed]
    def _skill_burst_set_team_panel_multipliers(
        self,
        merged: dict[str, Any],
        active_effects: list[dict[str, Any]],
        base_replacement: float,
    ) -> None:
        # Local timeline buffs and synchronized team buffs must use the same
        # panel calculation.  In particular, a provider's own intermittent
        # team buff is intentionally absent from its external windows because
        # the local cast owns the timing.  Excluding local effects here made
        # direct skills and summon snapshots miss that buff entirely.
        # Timeline ranking asks for the same active-effect state several times
        # while comparing candidates and rescoring periodic summons.  Computing
        # the four panel variants independently repeats effect deduplication,
        # calculator work, and signature normalization.  Cache the complete
        # result bundle on the task-local runtime.
        # Every multiplier below evaluates the effects as an instantaneous
        # active set.  Cooldown, duration and the absolute application time
        # decide whether an effect reaches this method, but cannot change the
        # panel value once it is active.  Use the same deduplicated identity as
        # the practical-panel helpers so equivalent refreshes share one bundle.
        bundle_cache = getattr(self, "_skill_burst_team_panel_bundle_cache", None)
        if not isinstance(bundle_cache, dict):
            bundle_cache = {}
            self._skill_burst_team_panel_bundle_cache = bundle_cache
        # Check the raw active state before running the comparatively expensive
        # duplicate-provider resolver.  The signature contains every field
        # that resolver reads, while deliberately excluding timestamps that do
        # not alter an instantaneous panel value.
        panel_state_signature = self._skill_burst_panel_state_signature(
            active_effects
        )
        state_cache_key = ("active-panel-v1", panel_state_signature)
        cached_bundle = bundle_cache.get(state_cache_key)
        panel_effects: list[dict[str, Any]] | None = None
        if cached_bundle is None:
            panel_effects = self._dedup_team_effects(active_effects)
            effect_signature = tuple(
                sorted(
                    (
                        norm(effect.get("provider")),
                        self._skill_burst_skill_key(
                            self._team_effect_skill_name(effect.get("skill"))
                        ),
                        tuple(
                            sorted(
                                (norm(key), round(as_number(value), 8))
                                for key, value in effect.get("attrs", {}).items()
                                if abs(as_number(value)) > 1e-9
                            )
                        ),
                    )
                    for effect in panel_effects
                )
            )
            canonical_cache_key = ("dedup-panel-v1", effect_signature)
            cached_bundle = bundle_cache.get(canonical_cache_key)
        if cached_bundle is None:
            assert panel_effects is not None
            practical_signature = tuple(
                sorted(
                    (
                        norm(effect.get("provider")),
                        self._team_effect_skill_name(effect.get("skill")),
                        tuple(
                            sorted(
                                (norm(key), round(as_number(value), 6))
                                for key, value in effect.get("attrs", {}).items()
                            )
                        ),
                    )
                    for effect in panel_effects
                )
            )
            missing = object()
            previous_prepared = getattr(
                self, "_team_practical_prepared_effect_state", missing
            )
            self._team_practical_prepared_effect_state = (
                panel_effects,
                practical_signature,
            )
            try:
                if panel_effects:
                    panel_multiplier = self._team_practical_panel_multiplier(panel_effects)
                else:
                    panel_multiplier = 1.0
                physical_attack, magic_attack = self._team_practical_raw_attack_multipliers(panel_effects)
                direct_physical, direct_magic = self._team_practical_direct_attack_multipliers(panel_effects)
                if norm(self.second_job.currentText()) in {"圣骑士", "十字军"}:
                    physical, magic = self._team_practical_attack_panel_multipliers(panel_effects)
                else:
                    physical = magic = 1.0
            finally:
                if previous_prepared is missing:
                    self.__dict__.pop(
                        "_team_practical_prepared_effect_state", None
                    )
                else:
                    self._team_practical_prepared_effect_state = (
                        previous_prepared
                    )
            cached_bundle = (
                panel_multiplier,
                physical_attack,
                magic_attack,
                direct_physical,
                direct_magic,
                physical,
                magic,
            )
            bundle_cache[canonical_cache_key] = cached_bundle
        bundle_cache[state_cache_key] = cached_bundle
        while len(bundle_cache) > 32768:
            bundle_cache.pop(next(iter(bundle_cache)))
        (
            panel_multiplier,
            physical_attack,
            magic_attack,
            direct_physical,
            direct_magic,
            physical,
            magic,
        ) = cached_bundle
        overall = base_replacement * panel_multiplier
        if active_effects or abs(base_replacement - 1.0) > 1e-9:
            merged[SKILL_BURST_TEAM_PANEL_MULTIPLIER] = overall
        merged[SKILL_BURST_TEAM_PHYSICAL_ATTACK_MULTIPLIER] = physical_attack
        merged[SKILL_BURST_TEAM_MAGIC_ATTACK_MULTIPLIER] = magic_attack
        merged[SKILL_BURST_TEAM_DIRECT_PHYSICAL_ATTACK_MULTIPLIER] = direct_physical
        merged[SKILL_BURST_TEAM_DIRECT_MAGIC_ATTACK_MULTIPLIER] = direct_magic
        if norm(self.second_job.currentText()) not in {"圣骑士", "十字军"}:
            return
        merged[SKILL_BURST_TEAM_PHYSICAL_PANEL_MULTIPLIER] = base_replacement * physical
        merged[SKILL_BURST_TEAM_MAGIC_PANEL_MULTIPLIER] = base_replacement * magic
    def _skill_burst_external_window_index(
        self,
        external_windows: list[dict[str, Any]],
    ) -> tuple[
        dict[int, tuple[tuple[float, float, dict[str, Any]], ...]],
        tuple[tuple[float, float, float, str], ...],
        tuple[float, ...],
    ]:
        source_key = (id(external_windows), len(external_windows))
        cached = getattr(self, "_skill_burst_external_window_index_cache", None)
        if isinstance(cached, tuple) and len(cached) == 4 and cached[0] == source_key:
            return cached[1], cached[2], cached[3]

        bucket_seconds = 5.0
        buckets: dict[int, list[tuple[float, float, dict[str, Any]]]] = defaultdict(list)
        cdr_rows: list[tuple[float, float, float, str]] = []
        for window_row in external_windows:
            if not isinstance(window_row, dict):
                continue
            start = max(0.0, as_number(window_row.get("start")))
            end = max(start, as_number(window_row.get("end")))
            effect = window_row.get("team_effect", {})
            if end <= start + 1e-9 or not isinstance(effect, dict):
                continue
            first_bucket = int(start // bucket_seconds)
            last_bucket = int(max(start, end - 1e-9) // bucket_seconds)
            entry = (start, end, effect)
            for bucket in range(first_bucket, last_bucket + 1):
                buckets[bucket].append(entry)
            cdr = max(0.0, as_number(effect.get("attrs", {}).get("冷却缩减%")))
            if cdr > 0:
                cdr_rows.append(
                    (start, end, cdr, self._skill_burst_cdr_source_key(effect))
                )
        cdr_rows.sort(key=lambda row: row[1])
        result_buckets = {
            bucket: tuple(rows) for bucket, rows in buckets.items()
        }
        result_cdr = tuple(cdr_rows)
        result_ends = tuple(row[1] for row in result_cdr)
        self._skill_burst_external_window_index_cache = (
            source_key,
            result_buckets,
            result_cdr,
            result_ends,
        )
        return result_buckets, result_cdr, result_ends
    def _skill_burst_cdr_source_key(
        self,
        effect: dict[str, Any] | None,
        fallback: str = "",
    ) -> str:
        skill = self._skill_burst_skill_key((effect or {}).get("skill"))
        return skill or norm(fallback) or "未命名减CD"
    def _skill_burst_special_timing_is_pinned(
        self,
        action: dict[str, Any] | None,
    ) -> bool:
        """Return whether an external schedule already owns this CDR buff."""
        if not isinstance(action, dict) or not action.get("special_cdr"):
            return False
        skill = self._skill_burst_skill_key(action.get("skill"))
        return skill in getattr(
            self,
            "_skill_burst_pinned_special_skill_keys",
            set(),
        )
    def _skill_burst_active_cdr_total(
        self,
        effects: list[dict[str, Any]],
    ) -> float:
        # Recasting the same buff refreshes it; overlapping copies do not stack.
        # Different CDR skills still add together up to the global 70% cap.
        by_source: dict[str, float] = {}
        for index, effect in enumerate(effects):
            value = max(0.0, as_number(effect.get("attrs", {}).get("冷却缩减%")))
            if value <= 0:
                continue
            source = self._skill_burst_cdr_source_key(effect, f"effect-{index}")
            by_source[source] = max(by_source.get(source, 0.0), value)
        return min(70.0, sum(by_source.values()))
    def _skill_burst_pinned_local_cdr_windows(
        self,
        actions: list[dict[str, Any]],
    ) -> tuple[tuple[float, float, float, str], ...]:
        """Expose future owner-cast CDR windows to cooldown integration."""
        cached = getattr(
            self, "_skill_burst_pinned_local_cdr_windows_last", None
        )
        if (
            isinstance(cached, tuple)
            and len(cached) == 2
            and cached[0] is actions
        ):
            return cached[1]
        windows: list[tuple[float, float, float, str]] = []
        for index, action in enumerate(actions):
            if not (
                action.get("special_cdr")
                and action.get("pinned_cast_schedule")
            ):
                continue
            cdr = max(
                0.0,
                as_number(action.get("attrs", {}).get("冷却缩减%")),
            )
            duration = max(0.0, as_number(action.get("duration")))
            if cdr <= 0 or duration <= 0:
                continue
            source = self._skill_burst_cdr_source_key(
                action,
                f"local-planned-{index}",
            )
            for cast_start, cast_end in action.get("scheduled_casts", ()):
                effect_start = max(0.0, as_number(cast_end))
                if effect_start <= max(0.0, as_number(cast_start)) + 1e-9:
                    continue
                windows.append(
                    (effect_start, effect_start + duration, cdr, source)
                )
        result = tuple(
            sorted(windows, key=lambda row: (row[0], row[1], row[3]))
        )
        self._skill_burst_pinned_local_cdr_windows_last = (actions, result)
        return result

    def _skill_burst_cdr_action_indexes(
        self,
        actions: list[dict[str, Any]],
    ) -> tuple[int, ...]:
        """Return local actions that can contribute future CDR work."""

        cached = getattr(self, "_skill_burst_cdr_action_indexes_last", None)
        if (
            isinstance(cached, tuple)
            and len(cached) == 2
            and cached[0] is actions
        ):
            return cached[1]
        result = tuple(
            index
            for index, action in enumerate(actions)
            if not action.get("special_cdr")
            and max(
                0.0,
                as_number(action.get("attrs", {}).get("冷却缩减%")),
            )
            > 0.0
        )
        self._skill_burst_cdr_action_indexes_last = (actions, result)
        return result
    def _skill_burst_active_attrs(
        self,
        actions: list[dict[str, Any]],
        expires_at: dict[int, float],
        current: float,
        rotation_planning: bool = False,
    ) -> dict[str, float]:
        stable_rotation = bool(
            rotation_planning
            and getattr(self, "_skill_burst_stable_external_rotation", False)
        )
        attrs_cache = getattr(self, "_skill_burst_active_attrs_cache", None)
        if not isinstance(attrs_cache, dict):
            attrs_cache = {}
            self._skill_burst_active_attrs_cache = attrs_cache
        pinned_local_cdr_windows = self._skill_burst_pinned_local_cdr_windows(
            actions
        )
        external_windows = getattr(self, "_skill_burst_external_team_effect_windows", [])
        external_rows = external_windows if isinstance(external_windows, list) else []
        cache_key = (
            # The same timestamp/expiry shape can be evaluated against a
            # different support-action list (team preview, comparison axis,
            # or an edited manual axis).  Without the context identities the
            # previous result can leak into the new axis, e.g. a non-freeze
            # result suppressing an active freeze layer.  Lists stay stable
            # throughout one axis calculation, so their identities preserve
            # the hot cache path without hashing every effect on every tick.
            id(actions),
            len(actions),
            id(external_rows),
            len(external_rows),
            stable_rotation,
            round(max(0.0, as_number(current)), 6),
            self._skill_burst_player_buff_clear_times(),
            tuple(
                sorted(
                    (int(index), round(as_number(expires), 6))
                    for index, expires in expires_at.items()
                    if as_number(expires) > as_number(current) + 1e-6
                )
            ),
            tuple(
                (
                    round(start, 6),
                    round(end, 6),
                    round(value, 6),
                    source,
                )
                for start, end, value, source in pinned_local_cdr_windows
            ),
        )
        cached_attrs = attrs_cache.get(cache_key)
        if cached_attrs is not None:
            return dict(cached_attrs)
        # These source lists are immutable within an axis. Keep references,
        # rather than just ids, so another axis cannot inherit recycled ids.
        # Reuse private effect rows across decision times/replay proposals;
        # their cached merge tokens and panel signatures are then reusable too.
        runtime_owner = getattr(self, "_skill_burst_active_runtime_effects", None)
        if not (
            isinstance(runtime_owner, tuple)
            and runtime_owner[0] is actions
            and runtime_owner[1] is external_rows
        ):
            runtime_owner = (actions, external_rows, {})
            self._skill_burst_active_runtime_effects = runtime_owner
        runtime_effects = runtime_owner[2]
        if len(runtime_effects) > 16384:
            runtime_effects.clear()
        active_effects: list[dict[str, Any]] = []
        own_objective_effects: list[dict[str, Any]] = []
        for index, action in enumerate(actions):
            if expires_at.get(index, 0.0) <= current + 1e-6:
                continue
            duration = max(0.0, as_number(action.get("duration")))
            effective_duration = duration + team_effect_refresh_tail_for_attrs(
                action.get("skill", ""),
                action.get("attrs", {}),
            )
            action_attrs = action.get("attrs", {})
            if self._skill_burst_special_timing_is_pinned(action):
                # The pinned burst/acceleration mode already owns these timing
                # modifiers. Keeping them on the local support action applies
                # the fixed external window a second time. A CDR skill planned
                # by the current character is not pinned and must keep its
                # timing attrs after its own cast finishes.
                action_attrs = {
                    key: value
                    for key, value in action_attrs.items()
                    if norm(key) not in {"冷却缩减%", "动作速度%", "攻速%"}
                }
            # ``expires_at`` is the authoritative active-state input.  Some
            # callers (manual/team comparison and imported axes) carry only
            # that end time and omit the descriptive duration.  Treat those
            # rows as active from the start of the inspected window instead
            # of deriving an impossible future application time at ``end``.
            applied_at = (
                max(
                    0.0,
                    as_number(expires_at.get(index)) - effective_duration,
                )
                if effective_duration > 1e-9
                else 0.0
            )
            if applied_at > current + 1e-6:
                continue
            action_attrs = self._skill_burst_attrs_after_player_buff_clear(
                action,
                action_attrs,
                applied_at,
                current,
            )
            if not action_attrs:
                continue
            runtime_key = (
                False, index, applied_at,
                repr(action.get("order", index)), repr(action.get("skill", "")),
                tuple((repr(key), repr(value)) for key, value in action_attrs.items()),
            )
            runtime_effect = runtime_effects.get(runtime_key)
            if runtime_effect is None:
                runtime_effect = {
                    "_skill_burst_runtime_effect": True,
                    "row": action.get("order", index),
                    "provider": "",
                    "skill": action.get("skill", ""),
                    "level": "",
                    "attrs": action_attrs,
                    "cooldown": 0,
                    "duration": 0,
                    "usage_ratio": 100,
                    "coverage": 1,
                    "applied_at": applied_at,
                    "effective_attrs": action_attrs,
                }
                runtime_effects[runtime_key] = runtime_effect
            active_effects.append(runtime_effect)
            if action.get("team_objective_effect"):
                team_attrs = self._skill_burst_attrs_after_player_buff_clear(
                    action, action.get("team_objective_attrs", {}), applied_at, current
                )
                if team_attrs:
                    own_objective_effects.append({
                        **runtime_effect, "attrs": dict(team_attrs),
                        "effective_attrs": dict(team_attrs),
                        "provider": norm(getattr(self, "_skill_burst_current_provider_override", "")),
                    })
        _external_buckets, external_cdr_rows, external_cdr_ends = (
            self._skill_burst_external_window_index(external_rows)
        )
        active_external_indexes = (
            self._skill_burst_active_timeline_window_indexes(
                external_rows,
                current,
                "external_live_attr",
            )
        )
        # Resistance admission depends only on the active external-window set.
        # Building every input and resolving all 48 team effects on every skill
        # decision was O(timeline ticks * team effects).  Index immutable inputs
        # once and cache the exact rejected set for each interval state.
        admission_owner = getattr(
            self,
            "_skill_burst_external_admission_input_cache",
            None,
        )
        source_key = (id(external_rows), len(external_rows))
        if not (
            isinstance(admission_owner, tuple)
            and len(admission_owner) == 3
            and admission_owner[0] == source_key
        ):
            inputs_by_index: dict[int, tuple[EffectInput, ...]] = {}
            for external_index, external_row in enumerate(external_rows):
                effect = external_row.get("team_effect", {})
                if not isinstance(effect, dict):
                    continue
                start = max(0.0, as_number(external_row.get("start")))
                end = max(start, as_number(external_row.get("end")))
                rows: list[EffectInput] = []
                for raw_key, raw_value in effect.get("attrs", {}).items():
                    key = norm(raw_key)
                    if not key.startswith("减") or not key.endswith("抗%"):
                        continue
                    rows.append(EffectInput(
                        key=key,
                        value=max(0.0, as_number(raw_value)),
                        skill_name=self._team_effect_skill_name(
                            effect.get("skill")
                        ),
                        source_id=norm(effect.get("provider")),
                        target_id="boss",
                        instance_id=f"external:{external_index}:{key}",
                        start=start,
                        end=end,
                        stack_group=norm(effect.get("stack_group")),
                        stackable=bool(effect.get("stackable")),
                        metadata={"external_index": external_index, "key": key},
                    ))
                if rows:
                    inputs_by_index[external_index] = tuple(rows)
            admission_owner = (source_key, inputs_by_index, {})
            self._skill_burst_external_admission_input_cache = admission_owner
        inputs_by_index, rejected_by_state = admission_owner[1], admission_owner[2]
        rejected_external_attrs = rejected_by_state.get(active_external_indexes)
        if rejected_external_attrs is None:
            admission_inputs = tuple(
                row
                for external_index in active_external_indexes
                for row in inputs_by_index.get(external_index, ())
            )
            admission_resolution = EFFECT_STACKING_ENGINE.resolve_active(
                admission_inputs,
                current,
            )
            rejected_external_attrs = frozenset({
                (
                    int(row.effect.metadata.get("external_index", -1)),
                    norm(row.effect.metadata.get("key")),
                )
                for row in admission_resolution.rejected
            })
            rejected_by_state[active_external_indexes] = rejected_external_attrs
        current_provider = norm(
            getattr(self, "_skill_burst_current_provider_override", "")
        )
        active_effects.extend(self._skill_burst_external_runtime_effects(
            external_rows, active_external_indexes, rejected_external_attrs,
            current, stable_rotation, current_provider, runtime_effects,
        ))
        if (
            not active_effects
            and not external_rows
            and not pinned_local_cdr_windows
            and not any(
                expires_at.get(index, 0.0) > current + 1e-6
                and self._skill_burst_action_has_freeze(action)
                for index, action in enumerate(actions)
            )
        ):
            attrs_cache[cache_key] = {}
            return {}
        base_replacement = max(
            0.0,
            as_number(getattr(self, "_skill_burst_team_base_replacement_multiplier", 1.0), 1.0),
        )
        holy_ground_recent = self._skill_burst_holy_ground_recent(
            active_effects,
            current,
        )
        freeze_sources: dict[str, float] = {}
        for index, action in enumerate(actions):
            if (
                expires_at.get(index, 0.0) <= current + 1e-6
                or not self._skill_burst_action_has_freeze(action)
            ):
                continue
            source = self._skill_burst_freeze_source(action)
            freeze_sources[source] = max(
                freeze_sources.get(source, 0.0),
                self._skill_burst_freeze_layers(action),
            )
        # Both exact consumers below already own complete state signatures:
        # the merge signature covers every field read by the attribute merge,
        # while the panel signature covers provider deduplication and panel
        # calculation.  The old final-cache key normalized all effects a third
        # time before calling those consumers.  Reuse their lossless keys so a
        # cache hit/miss decision stays exact without another O(effect-count)
        # normalization pass.
        merged_state_signature = (
            self._skill_burst_merged_team_attrs_signature(active_effects)
        )
        panel_state_signature = self._skill_burst_panel_state_signature(
            active_effects
        )
        effect_signature = (
            round(base_replacement, 8),
            holy_ground_recent,
            tuple(sorted(freeze_sources.items())),
            merged_state_signature,
            panel_state_signature,
        )
        effect_cache = getattr(self, "_skill_burst_active_effect_attrs_cache", None)
        if not isinstance(effect_cache, dict):
            effect_cache = {}
            self._skill_burst_active_effect_attrs_cache = effect_cache
        cached_merged = effect_cache.get(effect_signature)
        if cached_merged is not None:
            merged = dict(cached_merged)
        else:
            merged = self._skill_burst_cached_merged_team_attrs(active_effects)
            active_crit_down = self._skill_burst_active_crit_resist_reduction(active_effects)
            if active_crit_down > 0:
                merged["减致命抵抗%"] = active_crit_down
            for effect in active_effects:
                for key, value in effect.get("attrs", {}).items():
                    marker = norm(key)
                    if marker.startswith("__") and abs(as_number(value)) > 1e-9:
                        merged[marker] = max(
                            as_number(merged.get(marker)), as_number(value)
                        )
            active_cdr = self._skill_burst_active_cdr_total(active_effects)
            if active_cdr > 0:
                merged["冷却缩减%"] = min(70.0, active_cdr)
            self._skill_burst_set_team_panel_multipliers(
                merged, active_effects, base_replacement
            )
            if freeze_sources:
                merged["冰冻层"] = min(2.0, sum(freeze_sources.values()))
                for source, layers in freeze_sources.items():
                    merged[f"__冰冻来源__{source}"] = layers
            if holy_ground_recent:
                merged[SKILL_BURST_HOLY_GROUND_REQUIREMENT_MARKER] = 1.0
            effect_cache[effect_signature] = dict(merged)
            while len(effect_cache) > 32768:
                effect_cache.pop(next(iter(effect_cache)))

        cdr_windows: list[tuple[float, float, float, str]] = []
        for index in self._skill_burst_cdr_action_indexes(actions):
            action = actions[index]
            end = max(current, as_number(expires_at.get(index, current)))
            duration = max(0.0, as_number(action.get("duration")))
            effective_duration = duration + team_effect_refresh_tail_for_attrs(
                action.get("skill", ""), action.get("attrs", {})
            )
            applied_at = (
                max(0.0, end - effective_duration)
                if effective_duration > 1e-9
                else 0.0
            )
            current_attrs = self._skill_burst_attrs_after_player_buff_clear(
                action,
                action.get("attrs", {}),
                applied_at,
                current,
            )
            value = max(0.0, as_number(current_attrs.get("冷却缩减%")))
            if end <= current + 1e-6 or action.get("special_cdr") or value <= 0:
                continue
            for start, clipped_end in self._skill_burst_clip_clearable_buff_intervals(
                ((applied_at, end),), action
            ):
                if clipped_end <= current + 1e-6:
                    continue
                cdr_windows.append((
                    max(current, start),
                    clipped_end,
                    value,
                    self._skill_burst_cdr_source_key(action, f"local-{index}"),
                ))
        for start, end, value, source in (() if _EXACT_ROUND3 else pinned_local_cdr_windows):
            for clipped_start, clipped_end in self._skill_burst_clip_clearable_buff_intervals(
                ((start, end),), {"skill": source}
            ):
                if clipped_end <= current + 1e-6:
                    continue
                cdr_windows.append((clipped_start, clipped_end, value, source))
        for start, end, value, source in (() if _EXACT_ROUND3 else external_cdr_rows[
            bisect_right(external_cdr_ends, current + 1e-6) :
        ]):
            for clipped_start, clipped_end in self._skill_burst_clip_clearable_buff_intervals(
                ((start, end),), {"skill": source}
            ):
                if clipped_end <= current + 1e-6:
                    continue
                cdr_windows.append((clipped_start, clipped_end, value, source))
        if _EXACT_ROUND3:
            cdr_windows.extend(self._skill_burst_prepared_future_cdr_windows(
                pinned_local_cdr_windows, external_cdr_rows, current))
        if cdr_windows:
            normalized_cdr = tuple(
                (float(row[0]), float(row[1]), float(row[2]), norm(row[3]))
                for row in cdr_windows
            )
            merged["__cdr_windows__"] = normalized_cdr
            merged["__cdr_signature__"] = tuple(
                (round(start, 6), round(end, 6), round(value, 6), source)
                for start, end, value, source in normalized_cdr
            )
        # Keep source-aware local team effects with this exact state. A numeric
        # token survives dict copies in lookahead without mixing personal stats
        # into the team's baseline or leaking speculative state to other branches.
        states = getattr(self, "_skill_burst_local_objective_states", None)
        if states is None:
            states = ({}, {})
            self._skill_burst_local_objective_states = states
        state_key = tuple(
            (row["skill"], row["provider"], row["applied_at"],
             tuple(sorted(row["attrs"].items())))
            for row in own_objective_effects
        )
        token = states[0].get(state_key)
        if token is None:
            token = len(states[1]) + 1
            states[0][state_key] = token
            states[1][token] = tuple(own_objective_effects)
        merged["__local_team_objective_state__"] = float(token)
        attrs_cache[cache_key] = dict(merged)
        if len(attrs_cache) > 32768:
            attrs_cache.pop(next(iter(attrs_cache)))
        return dict(merged)
    def _skill_burst_prepared_future_cdr_windows(self, pinned, external, current):
        """Exact fixed future windows; local cast/expiry windows stay dynamic."""
        clears = self._skill_burst_player_buff_clear_times()
        owner = getattr(self, '_skill_burst_future_cdr_prepared', None)
        if owner is None or owner[:3] != (pinned, external, clears):
            prepared = []
            for start, end, value, source in (*pinned, *external):
                for left, right in self._skill_burst_clip_clearable_buff_intervals(
                        ((start, end),), {'skill': source}):
                    # Legacy first rejects external rows by their uncut end,
                    # then by the clipped end. Clipping never extends a row.
                    prepared.append((left, right, value, source))
            ends = tuple(sorted({row[1] for row in prepared}))
            owner = (pinned, external, clears, tuple(prepared), ends, {})
            self._skill_burst_future_cdr_prepared = owner
        epoch = bisect_right(owner[4], current + 1e-6)
        cached = owner[5].get(epoch)
        if cached is None:
            cached = tuple(row for row in owner[3] if row[1] > current + 1e-6)
            owner[5][epoch] = cached
        return cached

    def _skill_burst_external_runtime_effects(
        self, external_rows, active_indexes, rejected_attrs, current,
        stable_rotation, current_provider, runtime_effects,
    ):
        """Reuse only immutable external-state preparation, not cast snapshots.

        Window membership already uses current + 1e-6. Buff clearing has a
        separate current + 1e-9 boundary: both must stay in the key. Local
        action dictionaries (Judgement, Avatar, etc.) are NOT stored here.
        """
        cache = None
        if _EXACT_ROUND2:
            clears = self._skill_burst_player_buff_clear_times()
            owner = getattr(self, '_skill_burst_external_prepared_states', None)
            if not (owner is not None and owner[0] is external_rows
                    and owner[1] == len(external_rows) and owner[2] == clears):
                owner = (external_rows, len(external_rows), clears, {})
                self._skill_burst_external_prepared_states = owner
            cache = owner[3]
            key = (active_indexes, rejected_attrs, stable_rotation, current_provider,
                   bisect_right(clears, float(current) + 1e-9))
            if key in cache:
                return cache[key]
        active = []
        for external_index in active_indexes:
            external_row = external_rows[external_index]
            start = max(0.0, as_number(external_row.get('start')))
            end = max(start, as_number(external_row.get('end')))
            if not (start <= current + 1e-6 < end):
                continue
            effect = external_row.get('team_effect', {})
            if not isinstance(effect, dict):
                continue
            planning_effect = effect
            if stable_rotation and norm(effect.get('provider')) != current_provider:
                timing_attrs = {k: v for k, v in effect.get('attrs', {}).items()
                                if norm(k) in {'冷却缩减%', '动作速度%'}}
                if not timing_attrs:
                    continue
                planning_effect = {**effect, 'attrs': timing_attrs, 'effective_attrs': timing_attrs}
            planning_attrs = self._skill_burst_attrs_after_player_buff_clear(
                planning_effect, planning_effect.get('attrs', {}), start, current,
            )
            planning_attrs = {k: v for k, v in planning_attrs.items()
                              if (external_index, norm(k)) not in rejected_attrs}
            if not planning_attrs:
                continue
            runtime_key = (True, external_index, start,
                           tuple((repr(k), repr(v)) for k, v in planning_attrs.items()))
            runtime_effect = runtime_effects.get(runtime_key)
            if runtime_effect is None:
                runtime_effect = {
                    **planning_effect, 'attrs': planning_attrs,
                    '_skill_burst_runtime_effect': True, 'cooldown': 0.0,
                    'duration': 1.0, 'coverage': 1.0, 'applied_at': start,
                    'effective_attrs': planning_attrs,
                }
                runtime_effects[runtime_key] = runtime_effect
            active.append(runtime_effect)
        if cache is not None:
            if len(cache) >= 4096:
                cache.pop(next(iter(cache)))
            cache[key] = tuple(active)
        return active

    def _skill_burst_element_multiplier(self, element: str, active_attrs: dict[str, Any]) -> float:
        element_text = norm(element)
        if element_text == '不适用':
            return 1.0
        if element_text not in {"火", "水", "光", "暗"}:
            return max(0.0, 1 + (as_number(getattr(self, '_skill_burst_base_summary', {}).get('无属性%'))
                                + display_value(active_attrs, '无属性%')) / 100)
        summary = getattr(self, "_skill_burst_base_summary", {})
        attack_percent = as_number(summary.get(f"{element_text}攻%"))
        base_resist = as_number(summary.get(f"{element_text}抗%", summary.get(f"{element_text}有效抗%")))
        resist_down = max(
            0.0,
            display_value(active_attrs, self._skill_burst_resist_attr_for_element(element_text))
            + display_value(active_attrs, "减全抗%"),
        )
        effective_resist = base_resist - resist_down
        return max(0.0, (1 + attack_percent / 100) * (1 - effective_resist / 100))
    @staticmethod
    @lru_cache(maxsize=2048)
    def _skill_burst_periodic_skill_scope_keys(names: tuple[Any, ...]) -> frozenset[str]:
        return frozenset(key for name in names if (key := BuildEditor._skill_burst_skill_key(name)))

    @staticmethod
    @lru_cache(maxsize=256)
    def _skill_burst_periodic_element_scope_keys(names: tuple[Any, ...]) -> frozenset[str]:
        return frozenset(key for name in names if (key := norm(name)))

    def _skill_burst_metric_score(
        self,
        metric: dict[str, Any],
        active_attrs: dict[str, Any] | None = None,
        seconds: float | None = None,
    ) -> float:
        timeline_scores = metric.get("timeline_attack_scores", {})
        split_paladin_score = (
            norm(self.second_job.currentText()) in {"圣骑士", "十字军"}
            and isinstance(timeline_scores, dict)
            and sum(max(0.0, as_number(value)) for value in timeline_scores.values()) > 0
        )
        if split_paladin_score:
            attrs = active_attrs or {}
            overall = max(0.0, as_number(attrs.get(SKILL_BURST_TEAM_PANEL_MULTIPLIER), 1.0))
            physical_multiplier = max(
                0.0,
                as_number(attrs.get(SKILL_BURST_TEAM_PHYSICAL_PANEL_MULTIPLIER), overall),
            )
            magic_multiplier = max(
                0.0,
                as_number(attrs.get(SKILL_BURST_TEAM_MAGIC_PANEL_MULTIPLIER), overall),
            )
            raw_score = (
                max(0.0, as_number(timeline_scores.get("物理"))) * physical_multiplier
                + max(0.0, as_number(timeline_scores.get("魔法"))) * magic_multiplier
            )
        else:
            raw_score = as_number(metric.get("raw_score"))
        if raw_score <= 0:
            return max(0.0, as_number(metric.get("effective_score")))
        if norm(self.second_job.currentText()) == "火舞":
            base_multiplier = max(0.0, as_number(metric.get("base_multiplier")))
            base_bonus = max(0.0, as_number(metric.get("burning_hand_base_bonus")))
            current_bonus = self._skill_burning_hand_bonus_for_mode(
                metric.get("skill", ""),
                self._skill_burning_hand_mode_at(max(0.0, as_number(seconds))),
            )
            baseline_total = base_multiplier + base_bonus
            current_total = base_multiplier + current_bonus
            if baseline_total > 1e-9:
                raw_score *= current_total / baseline_total
        if (
            norm(self.second_job.currentText()) in {"重炮手", "机械大师"}
            and self._skill_burst_skill_key(metric.get("skill")) == "重磅炸弹"
            and active_attrs is not None
        ):
            duck_count = min(
                3.0,
                max(0.0, display_value(active_attrs, SKILL_BURST_HEAVY_DUCK_COUNT_MARKER)),
            )
            raw_score *= duck_count / 3.0
        if (
            norm(self.second_job.currentText()) == "圣徒"
            and self._skill_burst_skill_key(metric.get("skill")) == "冲击之手"
            and active_attrs is not None
        ):
            relic_count = max(
                0.0,
                display_value(active_attrs, SKILL_BURST_HOLY_RELIC_COUNT_MARKER),
            )
            # The skill table's default multiplier is three relics. Restore the
            # per-relic value, then scale it by the actual active relic count.
            raw_score *= relic_count / 3.0
        raw_score *= self._skill_burst_brand_damage_factor(
            metric.get("skill", ""),
            active_attrs,
        )
        fury_cost, fury_boost = self._skill_burst_fury_consumer(metric.get("skill", ""))
        if fury_cost > 0 and display_value(active_attrs or {}, SKILL_BURST_FURY_MARKER) < fury_cost:
            # 数据表保存的是消耗怒气后的完整面板；怒气不足时还原到未强化面板。
            raw_score /= max(1.0, fury_boost)
        metric_skill_key = self._skill_burst_skill_key(metric.get("skill"))
        metric_element = norm(metric.get("element"))
        for action in getattr(self, "_skill_burst_periodic_buffs", []):
            if action.get("active_multiplier", 1.0) == 1.0 and action.get("inactive_multiplier", 1.0) == 1.0:
                continue
            marker = norm(action.get("buff_marker"))
            is_active = bool(marker and display_value(active_attrs or {}, marker) > 0)
            multiplier = max(
                0.0,
                as_number(action.get("active_multiplier" if is_active else "inactive_multiplier"), 1.0),
            )
            # Many periodic buffs work through attrs rather than raw damage.
            # A factor of exactly one cannot affect this score for any scope.
            if multiplier == 1.0:
                continue
            # Cache only normalization of immutable *values*, never live Buff
            # activation, multipliers, HP, timestamps, or a whole rotation.
            # Taking tuples also observes in-place edits to the source lists.
            try:
                skill_scope = self._skill_burst_periodic_skill_scope_keys(tuple(action.get("skill_scope", [])))
                element_scope = self._skill_burst_periodic_element_scope_keys(tuple(action.get("element_scope", [])))
            except TypeError:
                # Keep compatibility with unusual unhashable scope entries.
                skill_scope = {self._skill_burst_skill_key(skill) for skill in action.get("skill_scope", [])
                               if self._skill_burst_skill_key(skill)}
                element_scope = {norm(element) for element in action.get("element_scope", []) if norm(element)}
            if skill_scope and metric_skill_key not in skill_scope:
                continue
            if element_scope and metric_element not in element_scope:
                continue
            raw_score *= multiplier
        raw_score *= self._skill_burst_damage_multiplier(
            metric.get("skill", ""),
            seconds,
            active_attrs,
        )
        if not split_paladin_score:
            raw_score *= max(
                0.0,
                as_number((active_attrs or {}).get(SKILL_BURST_TEAM_PANEL_MULTIPLIER), 1.0),
            )
        boss_hp_segments = getattr(self, "_skill_burst_boss_hp_segments", None)
        if boss_hp_segments is not None:
            hp_percent = boss_hp_percent_at(
                boss_hp_segments,
                max(0.0, as_number(seconds)),
            )
            # The calculator and skill table retain long-axis averages for
            # ordinary simulations. Live scoring divides those averages out
            # and applies the real Boss HP at this exact damage timestamp.
            raw_score *= conditional_panel_correction(
                active_attrs or {}, hp_percent
            )
            if display_value(
                active_attrs or {}, "__爆发buff__九天之舞2阶"
            ) > 0:
                raw_score /= 1.225
            if self._skill_burst_skill_key(metric.get("skill")) == PUNISHMENT_SKILL:
                raw_score *= punishment_correction(
                    max(1, int(as_number(metric.get("level"), 1))),
                    hp_percent,
                )
        return max(0.0, raw_score)
    def _skill_burst_snapshot_metric_panel(
        self,
        metric: dict[str, Any],
        active_attrs: dict[str, Any] | None,
    ) -> None:
        attrs = active_attrs or {}
        overall = max(0.0, as_number(attrs.get(SKILL_BURST_TEAM_PANEL_MULTIPLIER), 1.0))
        metric["raw_score"] = max(0.0, as_number(metric.get("raw_score")) * overall)
        timeline_scores = metric.get("timeline_attack_scores", {})
        if norm(self.second_job.currentText()) not in {"圣骑士", "十字军"} or not isinstance(timeline_scores, dict):
            return
        physical_multiplier = max(
            0.0,
            as_number(attrs.get(SKILL_BURST_TEAM_PHYSICAL_PANEL_MULTIPLIER), overall),
        )
        magic_multiplier = max(
            0.0,
            as_number(attrs.get(SKILL_BURST_TEAM_MAGIC_PANEL_MULTIPLIER), overall),
        )
        snapped = {
            "物理": max(0.0, as_number(timeline_scores.get("物理"))) * physical_multiplier,
            "魔法": max(0.0, as_number(timeline_scores.get("魔法"))) * magic_multiplier,
        }
        metric["timeline_attack_scores"] = {channel: value for channel, value in snapped.items() if value > 0}
        metric["timeline_raw_score"] = sum(snapped.values())
    def _skill_burst_periodic_snapshot_state(
        self,
        metric: dict[str, Any],
        active_attrs: dict[str, Any] | None,
        direct_attack_only: bool = False,
    ) -> dict[str, float]:
        """Capture only attack and elemental attack at summon time."""
        attrs = active_attrs or {}
        channels = metric.get("channel_scores", {})
        if (
            norm(self.second_job.currentText()) in {"圣骑士", "十字军"}
            and isinstance(metric.get("timeline_attack_scores"), dict)
        ):
            channels = metric.get("timeline_attack_scores", {})
        if not isinstance(channels, dict):
            channels = {}
        physical_weight = max(0.0, as_number(channels.get("物理")))
        magic_weight = max(0.0, as_number(channels.get("魔法")))
        total_weight = physical_weight + magic_weight
        physical_key = (
            SKILL_BURST_TEAM_DIRECT_PHYSICAL_ATTACK_MULTIPLIER
            if direct_attack_only else SKILL_BURST_TEAM_PHYSICAL_ATTACK_MULTIPLIER
        )
        magic_key = (
            SKILL_BURST_TEAM_DIRECT_MAGIC_ATTACK_MULTIPLIER
            if direct_attack_only else SKILL_BURST_TEAM_MAGIC_ATTACK_MULTIPLIER
        )
        # Holy Punishment is a player-centered periodic attack, not a summon.
        # Its cast snapshots the Paladin's complete physical panel, including
        # STR conversion from Avatar. Summons intentionally keep using the raw
        # attack-only keys because they have no four stats of their own.
        if (
            not direct_attack_only
            and self._skill_burst_is_refreshable_periodic_damage(metric.get("skill", ""))
            and norm(self.second_job.currentText()) in {"圣骑士", "十字军"}
        ):
            physical_key = SKILL_BURST_TEAM_PHYSICAL_PANEL_MULTIPLIER
            magic_key = SKILL_BURST_TEAM_MAGIC_PANEL_MULTIPLIER
        physical_multiplier = max(0.0, as_number(
            attrs.get(physical_key, attrs.get(SKILL_BURST_TEAM_PHYSICAL_ATTACK_MULTIPLIER)),
            1.0,
        ))
        magic_multiplier = max(0.0, as_number(
            attrs.get(magic_key, attrs.get(SKILL_BURST_TEAM_MAGIC_ATTACK_MULTIPLIER)),
            1.0,
        ))
        if direct_attack_only:
            # Local timeline buffs are not part of the external-team panel
            # multipliers. Summons have no four stats, so capture their direct
            # physical/magic attack percentages explicitly.
            physical_percent = display_value(attrs, "物攻%")
            magic_percent = display_value(attrs, "魔攻%")
            if abs(physical_percent) > 1e-9:
                physical_multiplier = max(0.0, 1 + physical_percent / 100)
            if abs(magic_percent) > 1e-9:
                magic_multiplier = max(0.0, 1 + magic_percent / 100)
        if total_weight > 1e-9:
            attack_multiplier = (
                physical_weight * physical_multiplier + magic_weight * magic_multiplier
            ) / total_weight
        else:
            attack_multiplier = (
                magic_multiplier
                if self._job_profile().get("attack_attr") == "魔攻"
                else physical_multiplier
            )

        element = norm(metric.get("element"))
        element_bonus = (
            display_value(attrs, f"{element}攻%")
            if element in {"火", "水", "光", "暗"}
            else 0.0 if element == '不适用' else display_value(attrs, '无属性%')
        )
        return {
            "attack": max(0.0, attack_multiplier),
            "element_bonus": element_bonus,
        }
    def _skill_burst_periodic_attack_element_layers(
        self,
        metric: dict[str, Any],
        snapshot_state: dict[str, float],
        live_attrs: dict[str, Any] | None,
        dynamic_attack: bool,
    ) -> tuple[float, float]:
        """Return summon snapshot and live-refresh multipliers without debuffs."""
        attrs = live_attrs or {}
        snapshot_attack = max(0.0, as_number(snapshot_state.get("attack"), 1.0))
        snapshot_bonus = as_number(snapshot_state.get("element_bonus"))
        element = norm(metric.get("element"))
        summary = getattr(self, "_skill_burst_base_summary", {}) or {}
        base_element_attack = (
            as_number(summary.get(f"{element}攻%"))
            if element in {"火", "水", "光", "暗"}
            else 0.0 if element == '不适用' else as_number(summary.get('无属性%'))
        )
        base_element_layer = max(0.01, 1 + base_element_attack / 100)
        snapshot_element_layer = max(
            0.01,
            1 + (base_element_attack + snapshot_bonus) / 100,
        )
        snapshot_multiplier = snapshot_attack * snapshot_element_layer / base_element_layer
        if not dynamic_attack:
            return max(0.0, snapshot_multiplier), 1.0

        live_state = self._skill_burst_periodic_snapshot_state(
            metric,
            attrs,
            direct_attack_only=True,
        )
        live_attack = max(0.0, as_number(live_state.get("attack"), 1.0))
        live_bonus = as_number(live_state.get("element_bonus"))
        combined_element_layer = max(
            0.0,
            1 + (base_element_attack + snapshot_bonus + live_bonus) / 100,
        )
        dynamic_multiplier = live_attack * combined_element_layer / snapshot_element_layer
        return max(0.0, snapshot_multiplier), max(0.0, dynamic_multiplier)
    def _skill_burst_full_metric_score(
        self,
        metric: dict[str, Any],
        active_attrs: dict[str, Any] | None = None,
        seconds: float | None = None,
    ) -> float:
        score = self._skill_burst_metric_score(metric, active_attrs, seconds)
        if as_number(metric.get("raw_score")) <= 0:
            return score
        return score * self._skill_burst_element_multiplier(
            norm(metric.get("element")),
            active_attrs or {},
        )
    def _skill_burst_periodic_realtime_debuff_multiplier(
        self,
        metric: dict[str, Any],
        active_attrs: dict[str, Any] | None,
    ) -> float:
        attrs = active_attrs or {}
        element = norm(metric.get("element"))
        resistance_attrs: dict[str, float] = {}
        resist_attr = self._skill_burst_resist_attr_for_element(element)
        if resist_attr:
            resistance_attrs[resist_attr] = display_value(attrs, resist_attr)
        resistance_attrs["减全抗%"] = display_value(attrs, "减全抗%")
        base_element_multiplier = self._skill_burst_element_multiplier(
            element,
            {},
        )
        active_element_multiplier = self._skill_burst_element_multiplier(
            element,
            resistance_attrs,
        )
        resistance_multiplier = (
            active_element_multiplier / base_element_multiplier
            if base_element_multiplier > 1e-9
            else 1.0
        )
        if SKILL_BURST_TEAM_PANEL_MULTIPLIER not in attrs:
            # Compatibility path for isolated/synthetic timelines that do not
            # carry a calculator-backed panel ratio. Periodic scoring removes
            # resistance attributes from score_attrs, so settle the live
            # resistance layer here exactly once just like the panel path.
            channels = self._skill_burst_scaled_channel_scores(metric, 1.0)
            amp_percent = amplification_for_channel(
                attrs,
                "双攻",
                physical_weight=channels.get("物理", 0.0),
                magical_weight=channels.get("魔法", 0.0),
            )
            multiplier = 1 + amp_percent / 100
            freeze_layers = min(2.0, max(0.0, display_value(attrs, "冰冻层")))
            if freeze_layers > 0:
                multiplier *= 1 + freeze_layers * self._team_freeze_coefficient() / 2

            crit_down = max(0.0, display_value(attrs, "减致命抵抗%"))
            if crit_down > 0:
                summary = getattr(self, "_skill_burst_base_summary", {}) or {}
                crit_percent = min(90.0, max(0.0, as_number(summary.get("crit_percent"))))
                crit_damage_percent = min(
                    100.0,
                    max(0.0, as_number(summary.get("crit_damage_percent"))),
                )
                base_resist = min(
                    90.0,
                    max(0.0, as_number(summary.get("crit_resist_percent"))),
                )
                before_rate = (crit_percent / 100) * max(0.0, 1 - base_resist / 100)
                after_resist = base_resist * (1 - min(100.0, crit_down) / 100)
                after_rate = (crit_percent / 100) * max(0.0, 1 - after_resist / 100)
                crit_damage = 2 + crit_damage_percent / 100
                before = 1 + before_rate * (crit_damage - 1)
                after = 1 + after_rate * (crit_damage - 1)
                multiplier *= after / max(0.01, before)

            channels = self._skill_burst_scaled_channel_scores(metric, 1.0)
            defense_down = defense_reduction_for_attack(
                attrs,
                "",
                physical_weight=channels.get("物理", 0.0),
                magical_weight=channels.get("魔法", 0.0),
            )
            monster_defense = max(
                0.0,
                as_number(self.build.get("monster_defense", 0)),
            )
            if defense_down > 0 and monster_defense > 0:
                before = max(
                    0.0,
                    1 - BuildCalculator.defense_percent(monster_defense) / 100,
                )
                after_defense = monster_defense * (1 - defense_down / 100)
                after = max(
                    0.0,
                    1 - BuildCalculator.defense_percent(after_defense) / 100,
                )
                multiplier *= after / max(0.01, before)
            return max(0.0, multiplier * resistance_multiplier)
        panel_multiplier = max(
            0.0,
            as_number(attrs.get(SKILL_BURST_TEAM_PANEL_MULTIPLIER), 1.0),
        )
        snapshot_state = self._skill_burst_periodic_snapshot_state(
            metric,
            attrs,
            direct_attack_only=False,
        )
        attack_multiplier = max(0.01, as_number(snapshot_state.get("attack"), 1.0))
        element_bonus = as_number(snapshot_state.get("element_bonus"))
        summary = getattr(self, "_skill_burst_base_summary", {}) or {}
        base_element_attack = (
            as_number(summary.get(f"{element}攻%"))
            if element in {"火", "水", "光", "暗"}
            else 0.0 if element == '不适用' else as_number(summary.get('无属性%'))
        )
        base_element_layer = max(0.01, 1 + base_element_attack / 100)
        active_element_layer = max(
            0.01,
            1 + (base_element_attack + element_bonus) / 100,
        )
        element_multiplier = active_element_layer / base_element_layer
        if element not in {'火','水','光','暗'}:
            # Neutral was deliberately excluded from the generic panel ratio.
            element_multiplier = 1.0

        # The panel ratio contains ordinary/type-specific amplification,
        # freeze, defense/critical-resist reduction, final damage and special
        # conditional multipliers. It deliberately excludes elemental
        # resistance reduction, so add that live layer separately. Attack and
        # elemental attack are removed because summons settle those two layers
        # through their snapshot/dynamic rules.
        return max(
            0.0,
            panel_multiplier
            / max(0.01, attack_multiplier * element_multiplier)
            * resistance_multiplier,
        )


def bind_host_globals(host_globals: dict[str, object]) -> None:
    protected = {'bind_host_globals', 'SkillTimelineEffectsMixin'}
    for name, value in host_globals.items():
        if name.startswith('__') or name in protected:
            continue
        globals()[name] = value


# Extracted from skill_timeline_engine.py; algorithm bodies are unchanged.
