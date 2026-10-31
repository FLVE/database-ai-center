# M4.3 PostgreSQL 诊断

按用户授权完成剩余 M4，全部验证后统一推送和远程升级，不单独发布本批。

独立 PostgreSQL 17.11 lab（postgresql-lab:5432/dbic_lab），与 dbic-postgres 主存储、
test-postgres 迁移库分离。沿用 psycopg 3，无新依赖，固定 diag 用户、密码由私有 env 提供。
最小角色为 CONNECT + pg_read_all_stats；无业务表 SELECT、写入、pg_signal_backend 或维护权限。
角色默认只读，客户端再次设只读事务、statement_timeout=1500ms、lock_timeout=500ms、
search_path=pg_catalog；异步连接 2 秒，工具总预算 5 秒，无自动重试。

新增问题 `maintenance_health`，两个固定步骤：

- `postgresql.blocking_sessions`：pg_stat_activity 限当前 db、client backend、Lock 等待，
  pg_blocking_pids 获取当前阻塞者，最多 50 个等待会话，额外一条检测截断。只返回 PID、
  阻塞 PID 和事务年龄秒数，不返回 SQL、用户、表名或客户端地址。PID 0 表示未解析的准备事务，
  不解释成真实会话；当前无阻塞不证明历史没有阻塞。
- `postgresql.vacuum_health`：最多 50 个 pg_stat_user_tables 样本，保留 relation OID、
  live/dead tuple 估算、最近 vacuum/autovacuum 时间及计数；统计重置时间单独保留。
  必须 track_counts=on。计数是估算，不推断物理膨胀或自动 vacuum 故障；无用户表为 empty。
  dead>=1000 且占估算总量>=20% 只产生核查线索，不执行 VACUUM/ANALYZE。

REST/MCP、证据、反馈与权限沿用既有路径；默认禁用，拒绝 window、任意 SQL/连接参数与跨类型问题。
纯 core Schema/规则校验 UTC、时效、归属、observed 质量；MongoDB/Redis/MySQL 路径不变。
PG NullAgent；不因新增工具扩大 MySQL 模型工具列表。不新增迁移。

验收至少包含真实无阻塞/行锁等待两个 fixture，另验证死元组统计、撤销观察角色及恢复、
diag 无业务表权限、readonly 与超时设置、REST/MCP 归档/反馈/重启回读、错误引用/脱敏边界。
所有 fixture 只在 test-postgresql 创建随机测试对象；等待语句有数据库超时，finally 回滚和清理。
两个架构全量验证后与 Doris 一起进入 M4 发布门槛。不能用 SQLite 或纯 mock 代替真实 PostgreSQL。

依据：[PG17 统计](https://www.postgresql.org/docs/17/monitoring-stats.html)、
[pg_blocking_pids](https://www.postgresql.org/docs/17/functions-info.html)、
[预定义角色](https://www.postgresql.org/docs/17/predefined-roles.html)。
