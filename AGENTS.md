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

**当前任务：M3**（用户在远程 M2 ES 样例验收通过后已授权继续），不提前实现 M4–M5。范围及验收见 `docs/m3-plan.md`。
