# DN倒计时工具

基于 Npcap 抓包的游戏倒计时工具，只有两个悬浮窗：**增益 / 减益倒计时** 与 **Boss 技能倒计时**。
界面为 macOS 毛玻璃极简风，设置导航只有「Ncap 路径」。

## 目录结构

```
v420_runtime.py            引擎入口（PyInstaller 打包入口）
v420_backend.py            本地 HTTP 服务：抓包控制 / 悬浮窗数据 / Npcap 路径
dragon_nest_builder/
  v410_native_core.py      抓包与解析核心
  live_dps/                报文解析、事件匹配、悬浮窗快照（算法未改动）
  data/modular/            协议与技能表（解析所需）
  data/live_dps_profiles/  Boss 技能 / 动作 / 血量档位
frontend/                  Vue + Tauri 桌面壳
  src/App.vue              主面板 + 右侧 Ncap 导航
  src/views/LiveDpsView.vue              状态、两个采集开关、开始/停止
  src/views/SettingsView.vue             Ncap 路径设置
  src/components/BuffOverlay.vue         浅蓝胶囊（buff-overlay 窗口）
  src/components/SkillCountdownPanel.vue 暖橙胶囊（skill-countdown-overlay 窗口）
  src-tauri/               Rust 壳：窗口、托盘、快捷键、位置记忆
tools/                     构建脚本（便携打包、图标生成）
release-portable/          构建产物：DN倒计时工具.zip
```

## 本地开发（免打包）

```powershell
# 一条命令：脚本托管后端（源码）+ tauri dev（Vite 热更新 + 悬浮窗）
powershell -ExecutionPolicy Bypass -File tools\dev_run.ps1

# 只调引擎逻辑，不起桌面壳（浏览器里看界面）
powershell -ExecutionPolicy Bypass -File tools\dev_run.ps1 -BackendOnly
```

- 改 `.vue` / `.ts` → Vite HMR 立即生效；改 `dragon_nest_builder/**.py` → 后端 2 秒内自动重启（采集会话会中断，需重新点「开始统计」）。
- 后端日志实时打到控制台，同时落盘 `.dev\backend.log`；前端/tauri 日志在 `.dev\shell.log`。
- 脚本设置 `DRAGON_NEST_DEV_EXTERNAL_BACKEND=1`，让 debug 版桌面壳跳过"自己杀后端再 spawn"（见 `tools/dev_run.ps1` 与 `lib.rs`）。
- `-BackendOnly` 时浏览器里有两个页面：`http://127.0.0.1:1420/`（主界面）与 `/index.html?view=buff-overlay`（悬浮窗）。dev 是全新 origin，首次需要 `localStorage.setItem('dragon-nest-v420-buff-overlay-enabled','1')` 再刷新，否则主界面不会轮询悬浮窗数据。
- 端口 48710 被占时脚本会直接中止并给出提示。最常见原因是**之前启动的便携版还在以管理员权限运行**（`dragon-nest-simulator-v420.exe` + `DragonNestEngine.exe`），未提权的 PowerShell 杀不掉它。先手动关掉托盘里的工具，或以管理员身份运行本脚本。

### 调试记录（观测日志 + 保留抓包）

入口在 **设置 → 调试记录**（左侧栏「设置」）。**一个开关同时控制两件事**：

| 开关打开时 | 落盘位置 |
|---|---|
| 观测日志（每轮一个 `.jsonl`） | `<保存目录>\probe\` |
| 本轮原始抓包（每轮一个 `.dpscap`，可在关掉游戏后离线反复复算） | `<保存目录>\capture\` |

开关关闭（默认）时行为与以前完全一致：抓包留在 `%TEMP%\DragonNestACT\working`，统计结束就删除，磁盘上不留任何痕迹；观测日志不写。

保存目录可以在设置页里改，也可以点「选择目录」；留空 = 默认 `~/Documents/DragonNestDpsCapture/debug_records`。配置写在 `<app>/data/debug_recording.json`（与 `npcap.json` 同级，便携包整体搬家不丢），重启后自动恢复。

后端接口：`GET /api/debug-recording` 读状态，`POST /api/debug-recording {"enabled":true,"outputDir":"D:\\captures"}` 修改并立即生效。环境变量 `DRAGON_NEST_NEST_PROBE=1` 只在配置文件里还没写过开关时作为启动默认值；`/api/probe` 保留为「只切观测日志」的临时入口。

每次点「开始统计」会自动换一个新日志文件，一轮副本对应一份日志 + 一份抓包。

日志**只记录、不参与任何判定**，release 构建在未开启时是完全空操作。

记录内容：

| kind | 含义 |
|---|---|
| `map_hit` / `map_miss` | 地图身份包解出了什么（map_id、layout、是否战斗地图） |
| `nest_identity` | 副本身份由哪条路建立：`route=map`（切图包）或 `route=template`（Boss 模板反推），并附 `classic_regex_match` —— 这个名字会不会被前端当成「黑龙经典」 |
| `spawn_hit` / `spawn_template` | 怪物出生包：模板号、是否在 `monster_templates` 里、`boss` 标记 |
| `boss_bind` | `boss_actor_ids` 的增减，以及当时的 Boss 阶段名 |
| `boss_channels` | 三段通道（`03 14 00` / structured / `0e 16 00`）各自解出了什么 |
| `skill_candidate` | **去掉白名单**扫出来的技能号，附 `in_catalog` —— 用来发现「Boss 其实发了但我们不认识」的号 |
| `evidence_skip` | 语音证据被哪一环拦下（技能号/通道/模板不匹配） |
| `shape_stats` | 每 5 秒汇总服务端报文「长度 + 签名」分布，用来确认某类包到底有没有出现在抓包里 |

打完后看报告：

```powershell
.venv\Scripts\python.exe tools\probe_report.py            # 最新一份
.venv\Scripts\python.exe tools\probe_report.py --all      # 所有文件概览
```

## 测试

```powershell
# 后端：悬浮窗减益行（stdlib unittest，无 pytest，<1s）
.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v

# 前端
cd frontend && node --test tests/overlayDebuffVisibility.test.ts
```

## 打包（免安装绿色版）

```bash
打包便携版.bat
```

产物：`release-portable/DN倒计时工具.zip`，解压即用，不写注册表。
运行说明见 `docs/portable-readme.txt`。

## 接口（仅这些）

```
GET  /api/health
GET  /api/capture/status | voice-events | timeline | buff-overlay | buff-overlay/wait | toggles
POST /api/capture/start | stop | auto | toggles
GET/POST /api/npcap/config
POST /api/npcap/detect | test
```

配装、配队、评分、排行、历史记录、导出 CSV、诊断上传等接口与页面已从代码中删除。
