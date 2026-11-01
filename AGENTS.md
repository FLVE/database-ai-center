# Codex Instructions — Database AI Center

本仓库以 `docs/architecture.md` 和 `docs/implementation.md` 为唯一基线。先阅读两份文档，再按当前 Milestone 实现，不要扩展需求。

## 不可违反的约束

- DBIC 是**数据库诊断系统**，不是新的指标采集或日志平台。
- `core/` 不得依赖 Elasticsearch SDK、数据库驱动或具体 Agent 框架。
- 本地第一阶段只支持 MySQL 8.4 lab；不开通生产实例。
- 工具必须是明确命名的预定义只读诊断能力；禁止任意 SQL/命令执行接口。
- 禁止 Kill Session、DDL、DML 修改业务数据、Redis FLUSH/KEYS/MONITOR 等高风险工具作为诊断 API。
- Playbook、规则、诊断证据模型是源码内的可测试资产，不得只保存在 Elastic Agent Builder。
- 每条重要结论必须包含 evidence_ids；缺少证据时返回 inconclusive/unavailable。
- 使用 PostgreSQL 保存任务、审计、反馈；Elastic 是可选搜索后端，不影响首版运行。
- 不覆盖、删除现有 Elastic/Kibana Docker 容器、volume 或索引。
- 默认本地网络绑定与最小权限，敏感配置使用 .env/gitignore。

## 任务执行

1. 检查当前仓库状态和现有文件，不覆盖用户工作。
2. 列出当前 Milestone 的文件变更与验收标准。
3. 编码并补单元/集成测试。
4. 尽可能在本机真实运行，提供实际测试命令和结果；无法运行时说明原因。
5. 结束时总结已完成、未完成、风险以及下一阶段入口。

**当前任务：完善 M4，再开始网页界面**（用户在远程 M4.1 复验通过后明确指定此顺序）。M4.1 Redis 已验收；按 `docs/implementation.md` 的阶段清单依次推进 M4.2 MongoDB、M4.3 PostgreSQL、M4.4 Doris，每批先补充版本、权限、证据契约与真实测试设计，再实现和验收。M4 全部验收前不开始网页界面，不提前实现 M5 巡检、自动修复或生产连接。Redis 范围仍见 `docs/m4-plan.md`，M3 实现与验收仍见 `docs/m3-plan.md`、`docs/m3-validation.md`。

M4.2 MongoDB、M4.3 PostgreSQL、M4.4 Doris 实现与完整验证入口见 `docs/m4-final-validation.md`；各批设计文件必须与最终适配保持一致。PostgreSQL 必须使用独立诊断目标 lab；Doris 不得为获取 Profile 授予 ADMIN/NODE 权限。发布状态以完整验证记录为准。

用户最新发布授权：先完成剩余所有 M4（PostgreSQL、Doris）并通过整体验证，然后统一 commit/push GitHub，最后升级远程服务器并验收。不要提前单独发布 M4.2/M4.3；若某批真实验证受阻，保留本地改动并说明阻塞，不把未通过的 M4 当作完成发布。此授权不允许改动既有 ES/Kibana、监控或生产数据库。

2026-10-31：M4 已按上述顺序完成，代码提交 2cfeb00，远程 0.8.0 的五类数据库、MCP、ES 和真实 DeepSeek 回归通过。结果与限制见 `docs/m4-final-validation.md`；下一阶段为网页界面，不扩展到生产或自动修复。

当前授权：网页界面 V1，见 `docs/web-v1-plan.md`。用户在 DBX 评估后选择先做独立 DBIC 界面；
不接入 DBX，不实施 M5。新增实例摘要 API、同源静态页面及测试，其他 API/Port 兼容。
网页 V1 已完成本地实现、启动和验证；用户随后已授权本批 GitHub 提交/推送。
2026-11-01：用户授权远程升级，服务器已部署 `a683dcd` / 0.9.0，网页、五类 lab REST/MCP、
ES 历史证据及真实 DeepSeek 验收通过，见 `docs/web-v1-validation.md` 的远程发布记录。
不自动进入网页 V1.1 或 M5；继续保持 lab、只读与证据约束。

用户随后授权继续网页 V1.1：任务历史、实例/状态/创建时间筛选与分页，范围见
`docs/web-v11-plan.md`。本次本地实现和验证，不自动提交/推送或远程升级，不进入 M5。

2026-11-01：V1.1 本机 0.10.0 已完成验收，用户随后明确授权提交并推送 GitHub；
本次发布不包含远程服务器迁移或升级，远程仍为 0.9.0。
