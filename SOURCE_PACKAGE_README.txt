DNACT 4.88.0 最新开发源码快照（2026-10-21）

这不是已发布安装包的逐字节源码备份，也不是新的发布版本。
包含当前 Python 后端/评分/解析/模拟、Vue/TypeScript 前端、Rust/Tauri 宿主、
CloudBase/Supabase 云函数、数据表、测试和构建脚本。
包含 038_online 分批迁移、自动回填脚本及其回归测试。
独立技能时间轴和 Hit 对照器源码在 independent_tools/；保留当前数据与待核状态，
未完成的技能审核没有因本次打包被宣布完成。

不包含抓包、玩家审计输出、日志、密钥、生产同步配置、node_modules、target、
构建缓存、历史安装包、第三方 EXE/DLL 运行时。若发现已知凭据/JWT 在其他文本中
被复制，仅在压缩包内替换为占位符，原工作目录未改动；具体文件见 SOURCE_MANIFEST.json。

开发入口：安装 requirements-lock.txt 与 frontend/pnpm-lock.yaml 中的依赖；
需要云端同步时参考 dragon_nest_builder/data/modular/supabase_score_sync.example.json
创建本机配置（不要提交密钥）。前端/Tauri 的版本文件为 4.88.0；根 README 中
部分历史说明尚为旧版，不应用它推断实际版本。

完整安装器入口是 build_v488_tauri.bat，但打包前须另行恢复
third_party/npcap/npcap-1.88.exe，并重建/复制 third_party/skill_hit_tool 的运行时。
工具源代码在 independent_tools 中；机器相关路径及外部客户端证据可能需调整。
本次未在空白机器重新构建安装器，不保证无需补充外部依赖即可直接打包。

SOURCE_MANIFEST.json 记录每个实际入包文件的 SHA-256。
打包后校验了 ZIP CRC、文件摘要、敏感目录排除规则及关键版本/迁移文件。
