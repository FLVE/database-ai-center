# M4 设计：先交付 Redis 诊断闭环

状态：2026-10-31 设计完成后，用户已授权实施 M4.1。下文保留设计契约；实际交付与
运行结果见 [m4-validation.md](m4-validation.md)，不能把设计条目当作已通过的测试。
基线为 [architecture.md](architecture.md)、[implementation.md](implementation.md)。
M3 的 MCP、DeepSeek 真实调用已经验收；不能据此宣称 Redis 能力可用。

## 1. 交付范围

M4 按数据库分批验收：M4.1 Redis；后续分别设计 MongoDB、PostgreSQL、Doris。
后续批次各自至少两个可重复 fixture，通过一个批次不代表整个 M4 完成。
远程 M4.1 复验后，用户明确先完善 M4 再开始网页界面；后续按 M4.2 MongoDB、
M4.3 PostgreSQL、M4.4 Doris 推进，整体门槛见 [实施计划](implementation.md#剩余批次与完成门槛)。
本文仍只定义 Redis 契约，不把后续数据库标为已实现。

M4.1 面向 `redis-lab-01` 单节点 lab，回答：当前内存使用是否接近配置上限、
有哪些内存构成事实、保留的慢命令记录和已有大 Key 审计提供了什么线索。
提供三个固定工具、一个 REST Playbook、三个 MCP 工具，复用 PostgreSQL 证据/报告/反馈/审计。
Redis 首版使用确定性规则，不调用模型；已有 MySQL / DeepSeek 路径保持可用。

本批次不增加 Cluster/Sentinel、Redis 历史指标适配、持续采集、大 Key 扫描、
任意命令、自动修复、Webhook、定时调度、持久 Worker、独立 UI 或生产连接。
`redis.cluster_health` 保留为未实现能力，不加入工具列表。

首个问题定名 `memory_pressure`，Playbook 为 `redis.memory_pressure`。
架构示例中的 `redis.memory_growth` 留给具备趋势证据的后续阶段；不拿快照证明增长。
Redis 8.4.x 作为拟定兼容线；实施时选择该线的修复版本，检查 ARM64/AMD64 镜像并锁定摘要，
记录实际 server/客户端版本。不是承诺支持任意 Redis 版本，也不使用浮动 latest。

## 2. 已有实现的扩展点

已检查当前代码；不是仅在插件目录增加 Redis 文件即可完成。

| 当前实现 | M4.1 变更设计 | 必须保留的行为 |
|---|---|---|
| `api/service.py` 固定校验 mysql.health、database_type=mysql | 先解析实例类型，再选择并授权全部 Playbook 工具；类型来自 Registry | 未授权请求不创建任务，不连接目标库；parent 仍同实例、同问题、终态 |
| `core/planner.py` 按 problem_type 查表 | `plan(problem_type, *, database_type)` 显式按类型与问题路由，更新 service、runner、全部测试调用方 | 不支持的问题不走默认慢查询分支；内部 tool: 路由也校验类型 |
| `core/policy.py` 只有一个 MySQL 白名单 | 显式 MySQL/Redis 实例和工具映射，核验环境、启用状态、版本、connection_ref | 不因扩大 TOOLS 联集而允许跨数据库调用 |
| `plugins/mysql/diagnostics.py` 使用全局 TOOLS | 改用 MySQL 自己的固定工具集合 | Redis 工具不能进入 MySQL 查询分支 |
| `main.py` 直接构造 MySQLDiagnostics | 装配一个静态工具分派适配器，按已授权工具路由到对应插件 | core 不导入驱动，不实现动态插件加载框架 |
| `core/runner.py` 的历史/规则分支隐含 MySQL | 显式按 task.database_type 分派规则和可选 Agent，MySQL 历史路径保留 | Redis 不触发 MySQL 指标或 ES 查询；单工具 MCP 不调用模型 |
| `core/rules.py` 非 lock_wait 默认慢查询 | MySQL 规则仍单独运行；新增纯函数 Redis 规则模块 | 未知类型返回 inconclusive，不产生 MySQL 结论 |
| `mcp/server.py` 固定三个 MySQL 工具 | 增加三个具名 Redis 工具，按每个工具对应实例分别过滤和鉴权 | Redis 禁用不隐藏 MySQL 工具；客户端不能传命令、连接串或文件路径 |
| AgentSuggestion / CompatibleAgentRuntime 限定 MySQL | 不扩大模型建议 schema；在适配器装配层按数据库选择 MySQL runtime / NullAgent | Redis 报告 agent_status=disabled，而非错误调用模型后 rejected |

`DiagnosticToolPort.run`、`TaskStorePort`、`AgentRuntimePort.explain` 签名保持不变。
工具分派器放 `adapters/diagnostic_tools.py`，Agent 分派放现有 `adapters/agent/factory.py`；
不让 core 知道 Redis SDK、DeepSeek 或具体部署配置。

## 3. REST、MCP 与能力契约

复用现有 REST 路径、Bearer Token、request_id、错误结构与任务状态机。
创建请求新增 `problem_type=memory_pressure`，不新增调用者可传的 database_type/host/port。

```json
{
  "instance_id": "redis-lab-01",
  "problem_type": "memory_pressure"
}
```

`POST /api/v1/diagnoses` 返回原有 `202 {task_id,status}`；GET 详情、证据、反馈端点不变。
新增枚举属于可见 API 变更，实施时同步受鉴权 OpenAPI 与契约测试；OpenAPI 仍由 FastAPI
模型生成，不另维护一份会漂移的手写规范。

| 输入/情况 | 设计结果 |
|---|---|
| 已启用 Redis lab + memory_pressure | 202；执行固定的三个证据步骤 |
| MySQL 实例 + memory_pressure / Redis 实例 + lock_wait | 422；不建任务、不调用工具 |
| Redis 请求携带非空 window | 422；本批次不支持 Redis 历史查询，不能静默忽略 |
| 其他实例、禁用 Redis、非 lab | 403；不连接数据库 |
| 请求含 command、key、DSN、path 或内部 tool: 问题类型 | 422；额外字段禁止 |
| Redis 连接失败、ACL 不足、版本不支持 | 已接受任务产生结构化 Evidence，保留报告和审计，通常 partial |
| 任务存储失败/任务总预算耗尽 | 沿用既有错误处理与 failed 语义，不标正常 |

任务固定顺序为 `redis.memory_breakdown`、`redis.slowlog_summary`、`redis.bigkey_audit_read`。
一个工具失败仍尝试剩余工具；每个步骤各归档一条 Evidence，正常调度共三条。

MCP 新增 `redis_memory_breakdown`、`redis_slowlog_summary`、`redis_bigkey_audit_read`，
输入严格只有 `instance_id: redis-lab-01`。复用 `inspect_tool`、Policy、Runner 和持久化，
每次调用归档一个内部任务及一条证据。MCP 认证、Host/Origin、请求体限制沿用 M3。

capabilities.milestone 在实现验收后变为 M4，现有字段保留；Redis 工具列表仅在配置启用
且实例已授权时出现。增加可缺省的 `enabled` 字段（默认 false，服务端逐项填值），区分实现可用和部署启用；
`status=available` 仅表达已实现，不代表目标连通。未实现数据库继续 not_implemented。
新增 `agent_database_types: ["mysql"]` 说明模型适配范围，不表示已启用或连通，避免误读全局 agent_backend。
M1–M3 验证脚本的 milestone/工具列表断言需兼容 M4，仍逐项验证 MySQL 契约。

## 4. 工具和证据

共同约束：不接受用户工具参数；UTC；字节与微秒字段带单位后缀；只保存白名单字段。
预计引入官方 Python `redis` 客户端一个依赖，实施时锁版本并验证 Python 3.12。
复用异步 Port；优先用客户端异步接口，关闭自动重试，超时/取消后释放连接。
每个实时工具连接超时 1 秒、单次读取 2 秒、整体 5 秒；保留全局串行执行、16 个待执行任务、
8 个工具调用/90 秒总预算。连接握手的 AUTH/HELLO 不是额外诊断工具，但须纳入超时。

工具调用数与 Redis 命令数分别约束：memory 工具最多三次 INFO；slowlog 工具最多两条诊断命令；
不使用 pipeline 重试、后台探测或无限循环。Redis 不可用不改变 `/readyz` 对 PostgreSQL 的检查。

### 4.1 redis.memory_breakdown

固定读取 `INFO server`、`INFO memory`、`INFO stats`。
从 server 检查版本与 standalone 模式，禁止跟随 Cluster 重定向；不保存原始 INFO。
保留 used_memory_bytes、used_memory_rss_bytes、used_memory_dataset_bytes、
used_memory_overhead_bytes、maxmemory_bytes、mem_not_counted_for_evict_bytes、
mem_fragmentation_ratio、mem_fragmentation_bytes、evicted_keys_total；不存在的字段为缺失，不填零。

读取时刻是本次 observed_at；多个 INFO 不是原子快照，应在工具版本语义中说明。
校验非负计数、有限浮点数及必要字段；mem_fragmentation_bytes 是有符号差值，不套用计数的非负约束。
不能把 RSS、used_memory、dataset、overhead 相加：
它们不是互斥内存分区。`maxmemory=0` 表示未配置该限额，不计算百分比，不声称没有内存风险。
evicted_keys 是累计计数，不能解释成“最近发生了淘汰”。

### 4.2 redis.slowlog_summary

固定 `INFO server` 校验模式/版本后执行 `SLOWLOG GET 20`，无 RESET、CONFIG 或任意 count 参数。
按 8.4 实测返回结构解析，不照抄最新文档中后续版本新增字段的位置。
仅输出保留样本数、最早/最晚时间、执行耗时分布摘要，以及按已知顶层命令名聚合的计数/耗时。
未知命令名归 OTHER；不输出 argv、Key、值、客户端地址、客户端名称或原始异常内容。
Redis 返回的参数在插件内即被丢弃，不能先交给 normalize 再声明 redacted。

没有记录时 empty，只表示本次未读到保留样本，不能区分“无慢命令”和“日志未记录/已覆盖”。
该数据是有限保留记录，不能构成请求延迟分位数或任意历史窗口全集；默认标注这一覆盖限制。
读取条数限制不等同于网络响应字节硬上限：M4.1 承诺脱敏后证据不超过 64 KiB，
不宣称普通客户端在解析前具有字节硬上限。使用隔离 lab 和固定 count/超时约束，
在故障测试中检查异常大记录；不能把此设计直接推广到生产。

### 4.3 redis.bigkey_audit_read

只读取部署者提供的既有脱敏审计 JSON，本工具不扫描 Redis，也不读取具体 Key。
默认未配置返回 unavailable。文件通过只读挂载提供，应用配置指定固定目录下的
`redis-lab-01.json`；API/MCP 无路径参数。拒绝符号链接、非普通文件、路径逃逸与超限文件；
有界读取最多 64 KiB 加一个探测字节，禁止先无界读入后检查长度。

建议版本 1 输入契约如下（数值仅为合成测试示例）：

```json
{
  "schema_version": 1,
  "instance_id": "redis-lab-01",
  "database_type": "redis",
  "environment": "lab",
  "audit_id": "92101104-ad00-4e32-969c-a673a7601ea0",
  "started_at": "2026-10-31T04:00:00Z",
  "finished_at": "2026-10-31T04:01:00Z",
  "coverage": "sampled",
  "scanned_keys": 100,
  "bigkey_threshold_bytes": 1048576,
  "bigkey_count": 2,
  "largest_key_bytes": 2097152
}
```

字段全部 required、extra=forbid；coverage 为 sampled/full；时间有时区且顺序合法，
计数为非负整数且 bigkey_count <= scanned_keys。审计时长最多 1 小时，未来时间容差 5 秒，
finished_at 距采样时刻最多 15 分钟。输入属于部署者断言，不能证明审计过程可信或快照完整。
有效审计用 kind=database_state、observed_at=finished_at、window=None；
payload 保留审计时间范围/覆盖率及 `origin=imported_audit`，不用读取文件时间冒充采样时间。

缺失/陈旧返回 unavailable，权限错误 forbidden，坏 JSON/错实例/错版本/非法字段返回 error，
均不返回原文。文件内容 hash 是完整性标记，不是来源认证。
有效摘要即使 bigkey_count=0 也返回 ok，规则只能说“该审计范围未报告大 Key”。
不输出 Key 名、普通 hash、DSN、命令或业务内容；`full` 也不证明当前实例没有大 Key。
审计导入本身为已知范围限制，报告不得把它作为当前内存异常的已确认原因。

## 5. 规则与任务状态

Redis 规则单独放 `core/redis_rules.py`，使用可注入时钟，可离线测试，不读取 Redis/文件。
实时证据沿用 90 秒新鲜度、未来 5 秒容差；审计使用自己的 15 分钟有效期，不套用实时 90 秒。
规则校验 source、task/instance 归属、状态、payload 形状和有效时间；每条 Finding 必带 evidence_ids。

| 规则 | 输出与限制 |
|---|---|
| 内存使用概况 | 有效字段生成 observed 事实，不把字段缺失当零 |
| 配置限额压力 | maxmemory>0 时以 max(0, used_memory-mem_not_counted_for_evict)/maxmemory 计算派生占比；必要字段缺失则 inconclusive；>=0.9 仅为版本化 lab 提示阈值 |
| RSS 与分配内存差异 | 只描述差异/比率，不能仅凭 fragmentation_ratio 推断内存泄漏或建议重启 |
| 已保留慢命令 | 描述已观察记录及耗时，不能推出网络延迟或内存异常因果关系 |
| 审计大 Key 线索 | 引用审计时间和范围；非零计数是审计事实，和内存压力同时存在最多是 supported_hypothesis，必须引用两种证据 |

Redis 不产生 confirmed_root_cause，不调用 LLM 升级结论。
延续现有语义：有可靠事实时 Report.status=observed；无可靠事实为 inconclusive。
任一步状态不是 ok/empty、存在覆盖限制或无可靠事实时 Task.status=partial；
全部步骤满足、无证据缺口才 completed。partial 不等于接口失败，也不等于内存正常。
由于慢日志覆盖与外部审计的限制，M4.1 典型成功验收可能是 partial；验收检查证据与原因，
不能为追求 completed 隐藏限制。超时/权限/不支持分别保持 timeout/forbidden/unavailable。

## 6. 权限、环境与部署

- 仅增加 `redis-lab-01 / redis / lab / 8.4 / env:REDIS_LAB_DSN` allowlist。
  应用连接固定 `redis-lab:6379`、逻辑库 0、用户名 diag，密码来自 SecretStr/私有环境；
  不接受 API 连接串，不允许任意主机、Redis Cluster/Sentinel 或 fallback 账号。
- 新增 `DBIC_REDIS_ENABLED=false`、`DBIC_REDIS_DIAG_PASSWORD`、可选
  `DBIC_REDIS_AUDIT_DIR`；注册项 enabled 与部署开关同时有效才授权 Redis。
  Registry 装配时形成有效 enabled 状态，Policy 再检查，不只在 capabilities 隐藏工具。
- Compose 新增可选 Redis profile 和隔离 `test-redis`，不映射宿主 6379；
  未启用时 MySQL 单独运行。API 不硬依赖 Redis healthy，故障须能产生诊断证据。
- 初始化脚本只处理新建 lab 的 ACL，诊断账号 deny-by-default，仅精确放行 PING、INFO、
  SLOWLOG GET 及必要连接握手；不授予 +@admin、+@dangerous、+@all、Key 模式或写权限。
  INFO 的具体 section 由固定插件约束；客户端隐藏握手命令必须通过实测确认，不为兼容扩权。
- SLOWLOG GET 在 Redis 官方分类包含敏感管理类别，必须明确按子命令授权；
  不能因为“只读”就授予整个 SLOWLOG 或管理命令类别。必须验证 SLOWLOG RESET 被拒绝。
- 禁止 KEYS、SCAN、MONITOR、FLUSH*、CONFIG SET、CLIENT KILL、EVAL、DEBUG、
  MEMORY PURGE 等作为诊断能力。权限负测不能拿真实破坏性命令当探针；
  使用独立测试实例的 ACL DRYRUN/权限断言，测试管理员与应用账号隔离。
- ACL 密码通过受限文件/secret 注入，不写在镜像、命令参数、示例或日志中；
  初始化需要的管理员凭据不得传给 API 或模型。
- PostgreSQL 表已有 database_type/JSON 字段且实例约束允许 redis，预计沿用 0002_m1。
  实施前运行真实迁移、旧报告回读及 Redis 归档测试确认，不为预期而预建新表。
- 保留目前全局串行 Runner，不在本批次增加跨实例并行或可靠队列。
  服务器部署仅新增本项目 Redis lab 与重建 API，保留现有三份 Compose 配置和回滚镜像；
  不改 ES/Kibana、既有 MySQL/PG 卷或其他服务，不做公网开放。

## 7. 实施文件与顺序

| 步骤 | 拟新增/修改文件 | 验收出口 |
|---|---|---|
| A. 类型/策略路由 | `core/planner.py`、`policy.py`、`api/service.py`、`api/routes/diagnoses.py`、`adapters/diagnostic_tools.py`、MySQL 工具局部白名单 | MySQL 原测试通过；跨类型/未知目标拒绝且零调用 |
| B. Redis 插件和部署 | `plugins/redis/{__init__,diagnostics,models,audit}.py`、`settings.py`、`main.py`、配置、Compose、`scripts/init_redis_lab.py`、`pyproject.toml`、`uv.lock` | 隔离 Redis 真连接、ACL、超时和脱敏通过 |
| C. 规则与归档 | `core/redis_rules.py`、`core/runner.py`、必要的 `core/report.py`、Agent 工厂 | 归档三条证据，事实/假设/缺失项准确；Redis 不发模型/历史请求 |
| D. MCP/能力/兼容性 | `mcp/server.py`、capabilities 模型、M1–M3 验证脚本 | 新旧具名工具逐实例授权，MCP 与 REST 回读一致 |
| E. 验收 | Redis 单测、隔离集成、合成审计 fixtures、`scripts/verify_m4.py`、`docs/m4-validation.md`、README | 真实验收可重跑，失败原因明确，旧版本功能回归 |

不要在 A 阶段顺手改调度、重做所有 Registry/Port 或搭建通用动态插件框架。
只在 B 阶段新增已说明的 Redis 依赖，锁文件和两种架构镜像一起验证。

## 8. 验收矩阵

1. 单测：内存字段缺失/0 上限/阈值两侧/负值/NaN；旧证据、未来时间、错误引用；
   sampled/full 审计、零/非零大 Key、超大文件、符号链接、错实例、陈旧、敏感额外字段。
2. 真 Redis 至少两个独立 fixture：低占用/有内存压力，分别核对真实 INFO 与报告；
   通过隔离测试实例的限额和有界合成数据构造，不靠固定字节精确值，不触碰正在使用的 lab。
3. 慢日志存在/空/权限不足：测试实例启动配置控制记录阈值，不用忙等、DEBUG SLEEP、
   Lua 压力或昂贵命令造慢请求。加入含假 Key/客户端地址的记录，验证落库/返回/日志无泄漏。
4. ACL 故障、断连、超时、版本/模式不支持；任务有证据和限制，不伪造正常；
   测试写入限指定前缀并 finally 清理，专用管理员只在 test-redis 网络使用，禁止 FLUSHALL 清理。
5. PostgreSQL 真归档：任务类型 redis，三条证据/步骤、引用正确、反馈和审计可回读；
   迁移重复执行、M0–M3 旧报告反序列化不回归。
6. REST/MCP 鉴权、错误实例、SQL/命令/路径注入、window 拒绝；Redis MCP 三工具各一条归档，
   请求无权限时目标端零调用；禁用 Redis 后 MySQL MCP 三工具仍可用。
7. MySQL 原全量、M2 ES、M3 MCP、DeepSeek 单独回归；Redis 调查断言模型客户端调用数为零。
   模型网络故障矩阵继续复用现有 Postman 模拟；Redis 协议通过真实隔离 Redis 验证，不用 HTTP mock 冒充。
8. Mac ARM64 + OrbStack 与 Linux AMD64 构建/启动；收集 pytest、ruff、mypy、lock 与
   Compose config 结果。CI 无法连接外部服务的跳过项必须单列，不混作远程真实通过。

未来 `verify_m4.py` 默认只对已启用 lab 做只读 API/MCP 检查，确认 memory 来源有效、
证据归属及限制，不生成故障、不改 ACL/业务数据。`--require-audit` 要求部署者已经提供
有效文件；缺失不能 PASS。隔离故障 suite 由独立 Compose verify 流程执行。
实施后脚本与启用命令统一维护在 m4-validation.md；设计本身不证明部署完成。

实施细化：共享证据 Schema 位于 core/redis_models.py，插件 models.py 负责协议字段转换，
避免纯规则反向依赖插件。Redis 8.4.8 启动时实测出现 dataset 计数下溢，
不一致的 dataset/overhead 字段会被省略并标 limitation；总内存/限额仍独立校验，
不将异常组成值伪装为零。其他非法必要字段仍作为错误处理。

## 9. 实施前要落实的事项

设计默认足以先进行本地实现，不需要生产地址或真实业务数据。开始 B 阶段时必须记录
Redis 8.4.x 镜像补丁/摘要、redis-py 版本、实际握手和 ACL 权限测试结果；若无法同时满足
ARM64/AMD64 或权限边界，说明阻塞并调整设计，不能改成全权限或偷偷换版本线。
真实大 Key 审计源目前没有给定；先用明确标记的合成 JSON 验证读取契约，
部署验收可返回 unavailable，不把合成样例当成真实实例扫描结果。
Redis 的外部模型建议、历史增长分析、生产权限与其他数据库另立设计，不包含在此验收中。

## 10. 协议依据

以下是工具语义依据；超时、阈值、保留数量、文件格式与阶段拆分是 DBIC 本设计决定。

- [Redis INFO](https://redis.io/docs/latest/commands/info/)：字段及内存指标语义。
- [Redis Key eviction](https://redis.io/docs/latest/develop/reference/eviction/)：maxmemory 与不计入淘汰的内存。
- [Redis SLOWLOG GET](https://redis.io/docs/latest/commands/slowlog-get/)：返回内容、数量、权限分类。
- [Redis ACL](https://redis.io/docs/latest/operate/oss_and_stack/management/security/acl/)：精确子命令授权。
- [Redis 8.4 发布说明](https://redis.io/docs/latest/operate/oss_and_stack/stack-with-enterprise/release-notes/redisce/redisos-8.4-release-notes/)：拟定兼容线参考，镜像摘要在实施时验证。
