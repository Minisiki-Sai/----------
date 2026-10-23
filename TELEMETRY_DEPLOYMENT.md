# 匿名使用量统计部署

当前版本复用 CloudBase `team-score-sync` HTTP 云函数，不再部署旧的
Cloudflare Worker/D1 服务。

统计口径：

- 今日、近 7 日和近 30 日活跃：对应日期范围内至少启动一次的去重安装数。
- 累计安装：历史上至少成功上报一次的去重安装数。
- 最近 24 小时活跃曲线：每 15 分钟一个时间桶，统计该时间段内仍打开工具的去重安装数。
- 今日启动、抓包、评分、闪退：客户端只在对应低频事件发生时累加本地计数，20 秒合并发送；界面刷新和轮询不会重复计数。
- 抓包、评分和更新成功率：成功次数除以真正开始执行的次数；更新成功表示安装包已下载且文件大小、SHA-256 均校验通过。
- 统计线程平时休眠，只在低频事件或下一个 15 分钟心跳唤醒；网络失败时保留很小的本地累计值，之后按绝对总数幂等补传。
- 客户端只发送随机安装 UUID 和版本；云函数先做 HMAC-SHA256，PostgreSQL
  只保存哈希、时间桶、日期、版本和整数计数。
- 不保存角色名、游戏 ID、抓包内容或硬件标识。

部署步骤：

1. 在 CloudBase PostgreSQL SQL 编辑器完整执行
   `cloudbase/postgresql/016_anonymous_usage_analytics.sql`（已执行过可跳过），随后执行
   `cloudbase/postgresql/017_anonymous_usage_metrics.sql`。
2. 在 `team-score-sync` 云函数增加环境变量
   `ANONYMOUS_USAGE_HASH_SALT`，值使用至少 16 位的独立随机字符串。
   未设置时会兼容使用既有 `TEAM_SCORE_UPLOAD_TOKEN`，但建议独立设置。
3. 在项目根目录构建云函数包：

```powershell
powershell -ExecutionPolicy Bypass -File cloudbase\build_team_score_sync_package.ps1
```

4. 上传并部署：
   `cloudbase/releases/team-score-sync-cloudbase-2.5.0-usage-metrics.zip`。

部署后启动一次客户端。“设置 → 匿名使用量”可直接查看 24 小时曲线、今日事件、
成功率、DAU 曲线和版本分布，不需要管理员密码。该页面只在进入设置页或手动刷新
时读取一次汇总，不会轮询。
