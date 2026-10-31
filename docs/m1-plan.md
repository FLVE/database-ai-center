# M1 实施范围

沿用 V2.1：仅 mysql-lab-01 / MySQL 8.4；无 ES、LLM 或生产连接。

变更：core 模型/Port/Planner/Policy/规则/Engine；YAML registry 与 Playbook；
MySQL 固定 health/lock_chain/slow_query_summary；Null adapters；PostgreSQL 事务存储；
鉴权 diagnoses/证据/反馈 API；本地账号配置脚本、Compose 和测试。
新增 PyMySQL（rsa extra 支持 MySQL 8.4 认证）与 PyYAML；驱动仅用于插件和实验脚本。

验收：有锁/无锁/权限不足、摘要存在/缺失；所有 Finding 可追溯；权限不足 partial；
任意 SQL 参数和非 lab 实例被拒绝；运行相关单元/API 测试与可用的真实 lab 测试。

实现选择：同进程 BackgroundTasks，任务先持久化；单实例并发 1，工具调用预算 8，
任务预算 90 秒，工具 socket/SELECT 超时及返回行数/字节限制。进程中断时 queued/running
记录保留，不宣称可靠队列，不自动重跑；重试创建带 parent_task_id 的新任务。
状态变更使用期望状态条件，证据/步骤/审计及报告/结论/终态分别事务提交。
TaskStore Port 扩展并将 finish_task 改为接收完整终态 Task，以保留核心的状态所有权。
报告窗口请求不用于伪造累计摘要的历史精度；显式标注 unavailable 的历史指标与日志。
