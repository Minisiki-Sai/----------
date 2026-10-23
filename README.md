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

## 本地开发

```bash
.venv/Scripts/python.exe v420_backend.py --port 48710   # 解析引擎
cd frontend && pnpm dev:web                              # 界面 http://127.0.0.1:1420
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
