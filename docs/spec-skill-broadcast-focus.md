# Boss 技能播报来源规格（focus 模型）

> 状态：**已实现**，六个抓包全量回放通过（见 §12）。三个决策点已按用户确认落地：
> 场景 Boss 与目标 Boss 平铺两行且都参与倒计时 / focusTarget 取 15 秒伤害占比最高 /
> focus 为空返回 None 不过滤。
> 适用：梦魇海龙 / 黑龙经典 / 黑龙硬核 / 沙龙经典 / 沙龙硬核。

## 0. 一句话

播报来源 = **本机当前攻击的 Boss** ∪ **副本声明的场景 Boss**；集合外一律不播。

## 1. 为什么要换掉现在的分边黑名单

现状（black-list 模型）：先判断"这个副本是不是分边打法"，再算出"另一侧是谁"，
把它放进 `splitGroup.others`，前端按 `others` 剔除技能行。

问题在于**它必须先回答"另一侧是谁"**，于是被迫引入：
`split_peer_boss_templates` 的两道闸门、`_current_target_groups` 分组表、
副本登记校验、`lastSideTemplate` 前端状态。而黑名单对一类情况天生无解：
**Boss 死了但服务器没发血量归零包**（天启停在 0.017%）——它不在"另一侧"名单里，
所以永远不会被剔除，只能靠 20s/180s 静默去猜。

focus 模型反过来：只定"谁该播"，其余自动不播。不需要回答"另一侧是谁"。

## 2. 术语

| 术语 | 定义 |
|---|---|
| 登记 Boss `registered` | `nest_boss_templates(nestKey)` 返回的模板集合 |
| 目标维度 `focusTarget` | 本机最近正在攻击、且属于 `registered` 的那一只 |
| 场景维度 `scene` | 副本静态声明的、不可选中、持续放技能的 Boss |
| `focus` | `{focusTarget} ∪ scene`，白名单 |
| `others` | `registered − focus`，黑名单（前端仍按它剔除） |
| dominant target | 15s 窗口内伤害占比/命中数最大的目标，用于抗 AOE 抖动 |

## 3. 数据来源（全部已存在，不新增解码）

- `recentAttackTargets`：聚合器维护，15s TTL。**不要用 `lastAttackTargets`（永久表）**
- `_overlay_target_damage` / `_overlay_target_hits`：dominant 加权用
- `bossHealth` + `endedBossIds`：本机无有效目标时的回落与排除
- `nestKey`：`state.nestKey` → `infer_nest_from_snapshot()` → 技能号身份
- 技能号身份：`skill_template_index_v576.json`（`325450 → 4237034` 等）

## 4. 判定算法

```
1. nestKey := state.nestKey
             or infer_nest_from_snapshot(snapshot).nestKey      # 攻击目标反推
             or decoder.detected_nest_key                       # 技能号身份反推
2. registered := nest_boss_templates(nestKey)
   if not registered:  return None               # 不启用过滤，宁可都播
3. focusTarget := dominant(                        # 见 §4.1
       targets in recentAttackTargets 自本机,
       window=15s, must be in registered,
       must not be in endedBossIds)
4. scene := scene_templates(nestKey)               # profile 新增字段
5. focus := {focusTarget} ∪ scene
   if focus == ∅:  return None                    # 关键：空集合必须不过滤
6. others := registered − focus
   return { current: focusTarget or 0, focus: [...], others: [...] }
```

### 4.1 dominant target 的选择

按 `15s 窗口内伤害占比最高` 选，同权重时取"最后命中的那只"。
目标必须是 `registered` 成员，且不在 `endedBossIds`。
用占比而不是"最后一次命中"，是因为 AOE 命中的最后一个单位常是杂兵/召唤物。

### 4.2 刻意**不做**回落

初版设想在"本机没有目标"时回落到队友目标 / 血量行，实施时推演后取消了：

> 分边副本里我在打 A、队友在打 B，按票数回落有一半概率把"我在打谁"猜成对侧，
> 从而把真正该播的那一侧整段吞掉。

因此 `focusTarget` 选不出来就返回 `None`，前端不做过滤——**宁可多播，不可漏播**。
代价是跑机制/加血期间可能多播别人的 Boss，但不会造成残留（旧 Boss 的行不会重现）。

## 5. 前端改动（已实现）

1. `filterCurrentGroupRows(rows, hint)` 仍按 `others` 剔除，但**状态类行永远保留**：
   `boss_hp_state` / `boss_hp_threshold` / `monster_spawn` 是事实，不是技能播报。
   不保护会让另一侧 Boss 的血量、小怪存活状态凭空消失，影响按百分比或召唤物
   触发的机制（回放实测：沙龙硬核 t=99s、t=493s 各有一条出场行被误吞）。
2. `applyBossLifecycle` 里的"上一次是哪一侧 → `forgetTemplates`"整段删除，
   改为集合差集：
   ```
   gone = prevFocus − curFocus
   if gone: reminders/classic/hard/sand.forgetTemplates(gone); visualClock.forgetTemplates(gone)
   ```
   因为 `focus` 是逐帧算出来的集合，"谁掉出集合就 forget 谁"比"猜侧切换"更稳，
   同时天然修掉"换边后旧 Boss 仍在提示"。
3. `splitBossOnly` / `splitBossOnlyCurrent` 两个开关保留，语义改为"是否启用 focus 过滤"。
4. 字段：后端 `capture_voice_events` 同时输出 `focusGroup` 与 `splitGroup`（同值），
   前端读 `focusGroup ?? splitGroup`，老后端仍可用。

## 6. 场景 Boss 的建模

- 存放：`boss_hp_profiles_v576.json` 每个 profile 增加 `scene_template_ids: [...]`
  （梦魇海龙：`[4237034]`，即 P2 空中海龙）。
- `nest_boss_templates()` 拆成 `nest_boss_templates()`（常规）+ `scene_boss_templates()`（场景）。
- **静态声明是安全的**：P1 阶段它根本不发技能行，没有内容可播。
- 离场判定：`retired_sources()` 对场景 Boss **豁免**。原因见 §7.2。
- 呈现：与目标 Boss **平铺两行、都参与倒计时**（决策点 1 选 B）。倒计时行本就是
  扁平行表，场景 Boss 进白名单后自然并列，无需新增 UI 结构。

## 7. 与"中途开统计"逻辑的关系（重点）

### 7.1 不冲突，是同一个根信号

中途开统计的现有链路（`status_decoder._observe_skill_identity`）：

```
技能号 0e1600 → resolve_skill_template / catalog_template
              → 只认 Boss 或副本 Boss 模板
              → seed_monster_entities(actor → template)   补实体表
              → resolve_nest_template(template)           补 nestKey
```

它和 focus 模型用的是**同一类证据**：中途统计用"Boss 放了什么技能"补实体，
focus 用"本机打了谁"定播报。两者都指向"这只 Boss 是否存在、我是谁"，
不存在互斥。事实上 focus 模型比现在的分边模型更适配中途统计——
分边模型依赖 `bossHealth` 分组，而中途统计下血量行往往残缺；focus 只看攻击目标。

### 7.2 三个衔接要求（必须满足，否则会退化）

**R1. seed 必须发生在 publish 之前。**
场景 Boss 的技能行要带 `templateId` 才能按模板归属。`_observe_skill_identity`
当前挂在 `_feed_events_before_hammer_inputs()` 最前面，已在同一包内先 seed 后发布，
满足要求。**实现时不得改变这个顺序。**

**R2. 第一条技能行的 `templateId` 可能为 0。**
若 seed 未命中（技能号不在索引里、或不在 `monster_templates` 里），该行
`templateId = 0`。实测这不需要额外兜底：过滤是"模板号 in others 才吞"，
`0` 永远不在 `others` 里，所以这类行天然保留。且 `_observe_skill_identity`
通常在同一包内先 seed，后续行就带上模板号了。

**R3. 场景 Boss 的离场豁免。**
`retired_sources()` 的 `last_active = max(血量时间, 该 source 任意技能行时间)`，
即"还在放技能就不算离场"，本来就能保住空中海龙。但它的血量停在 0.9%，
若两次机制间隔 > 180s，会命中第二档（≤5% + 安静 180s）被短暂判离场又"复活"，
悬浮窗闪一下。因此新增 `exempt_templates` 参数，由
`live_capture_service._scene_templates_for(decoder)` 传入当前副本的场景 Boss。

### 7.3 已验证的数据支撑

- `skill_template_index_v576.json`：`325450 → {4237034: 31票}`、`325447 → {4237034: 21票}`。
  冰眼/龙息的技能号能稳定反推出空中海龙模板，且投票高度一致。
- `4237034` 确在 `nightmare_sea_dragon.template_ids` 内。
  即：中途开统计时，**P2 空中海龙放出的第一条冰眼/龙息，同时完成了"seed 实体"和"确定副本"**。
- 意味着场景 Boss 在中途统计场景下不会比全量抓包更差，反而更可靠。

## 8. 边界条件清单

| # | 场景 | 期望行为 |
|---|---|---|
| 1 | 本机打小怪/杂兵 | 不算 focusTarget，不改变 focus |
| 2 | 本机打暗影/召唤物（终局 4237025） | 不在 registered，不算 focusTarget |
| 3 | Boss 死亡但无 0 包（天启） | 不在 focus，靠集合差集 forget |
| 4 | 本机停手 10~20s | 上一目标仍在 15s 窗口内则沿用；超过则返回 None（不过滤），不闪"未识别" |
| 5 | 本机死亡/复活 | 同上 |
| 6 | 换边（天启 → 狮蝎） | 差集 forget 旧侧，零残留 |
| 7 | AOE 蹭到对侧 | dominant 占比抗抖，不切侧 |
| 8 | 场景 Boss 阶段结束 | 不再发技能，行自然消失 |
| 9 | 副本未识别 | `registered` 空 → 返回 None → 不过滤 |
| 10 | 索引缺失、技能行 `templateId = 0` | 0 不在 `others` 里 → 天然保留 |
| 11 | 同时必须打两只的本 | 单目标只播一只，需 `multi_target` 覆盖字段 |
| 12 | 同一模板跨阶段复用 | 场景声明需带阶段约束，否则会误播 |

## 9. 验收标准

1. 梦魇海龙完整包：P2 冰眼/龙息全部保留（改前首 2 次被吞）。
2. 终局面板：天启/狮蝎残留 **0 行**。
3. 换边：旧侧倒计时/语音 **0 提示**。
4. 沙龙硬核：分边判定 **零退化**。
5. 黑龙经典/硬核：无新增吞行（回放逐行对比）。
6. 中途起抓 380/400/450/500/550s：`nestKey` 全部正确，首条 Boss 事件 ≤ 7s。

## 10. 三个决策点（已确认）

| # | 决策 | 结论 |
|---|---|---|
| 1 | 场景 Boss 呈现 | **B**：与目标 Boss 平铺两行，都参与倒计时 |
| 2 | focusTarget 口径 | **A**：15 秒内伤害占比最高 |
| 3 | focus 为空时 | **A**：返回 `None`，不过滤 |

决策 1 的实现方式是"不额外分组"：倒计时行本来就是扁平行表，场景 Boss 进白名单后
它的技能行会自然与目标 Boss 的行并列显示，不需要新增 UI 结构。

## 11. 实现顺序（已完成）

1. ✅ profile 加 `scene_template_ids`；`nest_inference` 加 `scene_boss_templates()`
2. ✅ 聚合器新增 `attackScores`（15s 按秒分桶的伤害占比），轻量版 sink 传 `amount`
3. ✅ `voice_signals.retired_sources()` 场景 Boss 豁免（`exempt_templates`）
4. ✅ `overlay_service._split_group_hint` → `_focus_hint`；`split_peer_boss_templates`
   与 `_current_target_groups` 保留函数但退出生产路径（测试仍在用，避免无谓破坏）
5. ✅ 前端 `applyBossLifecycle` 改集合差集；状态类行保护
6. ✅ 影子对比脚本 `.dev/diag/shadow_focus_replay.py` → 验收见 §12

## 12. 全量回放结果（六个抓包）

脚本：`.dev/diag/shadow_focus_replay.py`，同一份包同时跑旧（分边黑名单）与新
（focus 白名单），逐行判定"播/吞"。报告：`.dev/diag/focus_replay_report.txt`。

关键结论：
- **梦魇海龙 1/2/3**：冰眼 325450、龙息 325447 **全部保留**（5/5、2/2、0 条）；
  `P2 空中海龙 4237034` 的技能行 **0 条被吞**（场景 Boss 生效）。
- **沙龙硬核**：`new_swallow = 0`。旧逻辑吞 2 条、新逻辑吞 2 条，都是卡基夫的分边行为，
  与旧版一致，无退化。
- **黑龙硬核 / 黑龙经典**：`both_play` 100%，**零差异**，新逻辑未吞任何行。
- 发现并修复一个旧逻辑同样存在的缺陷：`monster_spawn` / `boss_hp_state` 这类
  状态事实行被模板过滤误吞（沙龙硬核 t=99s、t=493s，梦魇海龙 t=539.5s）。
- 终局验证（海龙 1，t=2219s）：`focus=[4237023, 4237034]`，`others` 含 **4237011（天启）**。
  即天启已不在白名单 → 前端集合差集会清掉它的"已好"，不需要等血量归零包。

### 已知边界

清理是**模板级**的：同一模板 4237023 在不同阶段可能对应「地狱犬」和「狮蝎」两个实例，
若本机终结时刻仍在打同模板的另一只，前一实例的"已好"无法按实例区分。
要彻底区分需要 actor 级的实例身份，超出本次范围。
