# V4.01 模块架构

## 入口层

- `run.py`：程序启动入口。
- `dragon_nest_builder/app.py`：兼容旧导入路径，不再承载业务实现。
- `dragon_nest_builder/main_window.py`：主窗口、启动检查、全局样式和页面装配。
- `dragon_nest_builder/editor.py`：通过 mixin 组合形成 `BuildEditor`。

## 配装层

- `mixins/build_pages.py`：装备、刻印、纹章、石板、卡片和其他页面。
- `mixins/skill_build.py`：模拟加点、技能专精和技能纹章。
- `mixins/editor_state.py`：配装状态、收集、刷新和一键配置。
- `ui/compare_page.py`：配装对比页面。

## 技能模拟层

- `mixins/skill_simulation.py`：技能数据、DPS/DPA 和技能模拟公共计算。
- `mixins/skill_timeline_engine.py`：技能选择、冷却、连招和时间轴核心算法。
- `mixins/skill_timeline_support.py`：Buff、Debuff、召唤物和职业特殊规则。
- `mixins/timeline_editor.py`：可编辑时间轴交互。
- `mixins/timeline_runtime.py`：时间轴回放和效果窗口。
- `mixins/timeline_presenter.py`：时间轴及结果表格渲染。
- `mixins/skill_timeline_ui.py`：时间轴模块聚合入口。

## 职业机制数据层

- `data/modular/tables/skills/job_mechanics.json`：职业连招、资源、召唤、姿态、开场顺序、自动选技和团队效果技能的唯一可编辑数据源。
- `job_mechanics.py`：读取、校验并把 JSON 编译为引擎使用的只读注册表。
- `shared.py`：保留旧常量名称作为兼容接口，运行值由职业机制注册表生成。
- 复杂状态仍由时间轴执行器负责，例如召唤快照、怒气消费和审判力量刷新；执行器名称记录在各职业的 `runtime_handlers` 中，具体数值和技能关系放在 JSON。

## 配队层

- `mixins/team_effects.py`：团队 Buff、Debuff、减抗及贡献计算。
- `mixins/team_practical.py`：个人配队实战模拟。
- `mixins/team_simulation.py`：普通配队模块聚合入口。
- `mixins/extreme_engine.py`：极限配队搜索、评分和结果计算。
- `mixins/extreme_ui.py`：极限配队配置、进度及结果窗口。
- `mixins/extreme_team.py`：极限配队模块聚合入口。

## 依赖方向

`MainWindow -> BuildEditor -> 各业务 mixin -> shared/data/calculator`

业务模块之间通过 `BuildEditor` 的组合接口协作。`app.py` 保留旧公开 API，因此原来的 `from dragon_nest_builder.app import ...` 不需要修改。

## 修改边界

- v3.40：冻结，只用于运行和结果对照。
- V4.00：冻结为上一版本基线。
- V4.01：当前主开发版本，后续功能开发和模块细化均在这里进行。
- 数据修改优先落在 `data/modular`，不要重新把业务数据塞回 Python 文件。
- 调整职业特殊机制时优先编辑 `job_mechanics.json`；只有新增一种引擎从未支持过的机制类型时，才新增 Python 执行器。
