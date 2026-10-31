# Database AI Center（DBIC）本地实施与 Codex 开发手册

> 对应架构：`01-architecture-design.md` V2.1  
> 执行方式：Mac Apple Silicon + OrbStack / Docker；Codex 在本地仓库逐阶段实现。  
> 开发原则：优先做**无 LLM、无 ES 也可运行的 MySQL 锁等待诊断闭环**；验证通过后接入 Elastic 9 与 Agent。

## 0. 先决条件与约束

- 已有 Elasticsearch 9 / Kibana 本地环境；不要改变或删除现有容器、卷、索引。
- 本地可用 Docker Compose。Elasticsearch 在宿主机 `localhost:9200`，Kibana `localhost:5601` 是示例；真实端口从本地配置读取，不在源码硬编码。
- 数据库测试实例默认新建隔离环境；不连接生产库、不重用生产凭据。
- Python 3.12+，FastAPI、Pydantic v2、SQLAlchemy 2、Alembic、asyncpg/psycopg、pytest；MySQL 驱动优先 PyMySQL（同步调用经线程池隔离）或 asyncmy，团队统一即可。
- 不要求首版立即开通 Elastic Agent Builder。先用 `NullAgentRuntime` 生成规则式报告，后续接入 LLM。

## 1. 仓库目标结构

```text
database-ai-center/
├── README.md
├── AGENTS.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── docs/
│   ├── architecture.md
│   └── implementation.md
├── configs/
│   ├── instances.example.yaml
│   ├── policies.yaml
│   └── playbooks/
│       ├── mysql.lock_wait.yaml
│       └── mysql.slow_query.yaml
├── src/dbic/
│   ├── main.py
│   ├── api/routes/diagnoses.py
│   ├── api/routes/health.py
│   ├── core/models.py
│   ├── core/ports.py
│   ├── core/planner.py
│   ├── core/engine.py
│   ├── core/evidence.py
│   ├── core/rules.py
│   ├── core/report.py
│   ├── core/policy.py
│   ├── plugins/mysql/diagnostics.py
│   ├── plugins/mysql/queries.py
│   ├── plugins/redis/__init__.py
│   ├── plugins/mongodb/__init__.py
│   ├── plugins/postgresql/__init__.py
│   ├── plugins/doris/__init__.py
│   ├── adapters/search/elastic.py
│   ├── adapters/search/null.py
│   ├── adapters/metrics/prometheus.py
│   ├── adapters/metrics/null.py
│   ├── adapters/agent/null.py
│   ├── adapters/persistence/postgres.py
│   └── mcp/server.py
├── migrations/
├── tests/unit/
├── tests/integration/
├── tests/fixtures/
├── tests/incident_replay/
└── deploy/compose.yaml
```

`core/` 不得导入外部数据库驱动、Elasticsearch SDK、Prometheus 客户端或 Agent 框架。依赖方向必须从 `adapters/plugins` 指向 `core/ports`。

## 2. 本地 Compose 规划

新增服务（使用独立 project name `dbic-lab`，不与已有 Elastic Compose 混用）：

| 服务 | 首版 | 用途 | 网络暴露 |
|---|---|---|---|
| `dbic-api` | 必须 | REST API + 任务编排（开发阶段同进程执行） | `127.0.0.1:8086:8086` |
| `dbic-postgres` | 必须 | 任务、报告、证据、审计主数据 | 不映射宿主端口（或仅 loopback） |
| `mysql-lab` | 必须 | MySQL 8.4 测试 | 不映射宿主端口（可配置 loopback） |
| `redis-lab` | M2 | Redis 测试 | 不映射宿主端口 |
| `dbic-worker` | M3 | 异步告警调查/任务队列 | 不暴露 |
| 现有 Elastic 9 | 可选 | 日志/案例搜索 | 已独立运行，不重复创建 |

**重要：** Docker 容器内 `localhost` 指向容器自身。访问宿主机既有 ES 时，Mac OrbStack 可测试 `host.docker.internal:9200`；如果需要安全连接，使用显式网络、认证与允许的地址。不要默认禁用 TLS 或关闭认证。

`.env.example`（示例键名，不填真实凭据）：

```dotenv
DBIC_ENV=lab
DBIC_API_HOST=0.0.0.0
DBIC_API_PORT=8086
DBIC_DATABASE_URL=postgresql+psycopg://dbic:${DBIC_PG_PASSWORD}@dbic-postgres:5432/dbic
DBIC_PG_PASSWORD=change-me-locally
MYSQL_LAB_DSN=mysql+pymysql://diag:${MYSQL_DIAG_PASSWORD}@mysql-lab:3306/mysql
MYSQL_DIAG_PASSWORD=change-me-locally
DBIC_SEARCH_BACKEND=null
DBIC_ELASTIC_URL=http://host.docker.internal:9200
DBIC_ELASTIC_API_KEY=
DBIC_METRICS_BACKEND=null
DBIC_PROMETHEUS_URL=
DBIC_AGENT_BACKEND=null
DBIC_ALLOW_PRODUCTION=false
DBIC_MAX_TOOL_CALLS=8
DBIC_TASK_TIMEOUT_SECONDS=90
```

实际 Compose 的环境变量插值必须有可验证的默认值或启动前校验；禁止将生产凭据提交到 Git。注意 `.env.example` 的 `${...}` 引用是否被应用正确展开；在 Python 中不要假定 dotenv 自动做所有变量插值，优先在 Compose `environment` 中传完整 DSN。

## 3. 核心对象和数据库表

优先使用 Pydantic v2 模型，存储层 PostgreSQL 至少包括：

- `instances`（实验注册：`id`, `type`, `environment`, `version`, `connection_ref`, `enabled`；不存明文密码）
- `diagnostic_tasks`（`id`, `instance_id`, `problem_type`, `window_start/end`, `playbook_id`, `status`, `created_at`, `started_at`, `finished_at`, `error_code`, `parent_task_id`）
- `diagnostic_steps`（`id`, `task_id`, `step_key`, `tool_id`, `status`, `elapsed_ms`, `error_code`, `started_at`, `finished_at`）
- `evidence`（`id`, `task_id`, `kind`, `source`, `observed_at`, `collected_at`, `status`, `quality`, `payload_json`, `limitations_json`, `redacted`）
- `findings`（`id`, `task_id`, `statement`, `certainty`, `rule_id`, `evidence_ids_json`）
- `reports`（`task_id`, `summary_md`, `report_json`, `generated_at`, `generator_version`）
- `feedback`（`id`, `task_id`, `verdict`, `confirmed_root_cause`, `comment`, `created_at`）
- `audit_events`（`id`, `task_id`, `actor`, `action`, `target`, `elapsed_ms`, `status`, `created_at`）

首版需要 Alembic 迁移（`upgrade head` 能重复执行）。所有敏感字段严格脱敏，设置日志/证据体积上限。

## 4. Port（稳定接口）

```python
from typing import Protocol


class AssetRegistryPort(Protocol):
    async def get_instance(self, instance_id: str): ...


class DiagnosticToolPort(Protocol):
    async def run(self, *, tool_id: str, instance_id: str, params: dict): ...


class MetricsPort(Protocol):
    async def query_range(self, *, instance_id: str, metric_key: str, start: str, end: str): ...


class EvidenceSearchPort(Protocol):
    async def search_events(
        self, *, instance_id: str, start: str, end: str, categories: list[str]
    ): ...
    async def similar_incidents(self, *, query: str, limit: int): ...


class AgentRuntimePort(Protocol):
    async def explain(self, *, task, evidence: list, findings: list): ...


class TaskStorePort(Protocol):
    async def create_task(self, task): ...
    async def save_evidence(self, evidence): ...
    async def finish_task(self, task_id: str, report): ...
```

`NullSearch`、`NullMetrics` 返回 `unavailable` 而不是伪造空列表；测试中使用 `Fake*` 注入确定性数据。模型、错误码、时间、证据 ID 规范以设计文档为准。

## 5. MySQL 首个诊断工具实现

### 5.1 mysql.health

受控获取：MySQL 版本、可用性、`Threads_connected`、`Threads_running`、`Uptime` 等。字段来自 `SHOW GLOBAL STATUS` / `SELECT VERSION()` 的预设语句；禁止任意 SQL 参数。

### 5.2 mysql.lock_chain

针对 MySQL 8.0+/8.4 优先使用 `performance_schema.data_lock_waits` 与 `performance_schema.data_locks`，必要时联合 `performance_schema.threads`、`events_statements_current`；版本/权限不支持则返回 `unavailable`。应支持构建 waiter→blocker 边，并识别循环或缺失节点。禁止根据不存在的当前锁链判断过去无死锁。

**测试构造**（由测试夹具或人工受控执行，仅对本地 `mysql-lab`）：

- 连接 A：开启事务，更新 `accounts(id=1)`，保持事务。
- 连接 B：开启事务，更新相同记录，形成等待。
- 调用 DBIC `mysql.lock_chain`，观察阻塞者、等待者与等待资源。
- 释放 A 事务，确认状态恢复。

测试夹具需 `try/finally` 回滚/清理，不能对生产库做锁实验。

### 5.3 mysql.slow_query_summary

第一版优先读取启用的 `performance_schema.events_statements_summary_by_digest`（需要确认 instrumentation 和计数器有效），按 SQL Digest 返回有限 Top N、执行次数、总耗时、平均耗时、扫描行数等可获得字段；必要时从 Elastic SearchAdapter 读取历史慢日志。不要声称某个汇总表包含完整精确慢 SQL 历史；采集缺失时要报告不完整。

### 5.4 返回安全契约

每个工具结果统一：`status`、`observed_at`、`source`、`payload`、`limitations`、`duration_ms`。SQL 文本参数/个人数据默认脱敏；实例级执行超时、单次最大 rows/bytes 和并发上限。

## 6. Engine 首版执行过程

1. `POST /api/v1/diagnoses` 校验 schema、认证及实例 allowlist；任务入库，返回 `202`。
2. `TaskRunner` 查 Asset Registry，选择 `mysql.lock_wait` 或 `mysql.slow_query` Playbook。
3. Policy Engine 根据环境/工具 ID/预算决定允许执行的工具。
4. Diagnostic Tool Port 执行；Evidence Gateway 统一时间和单位，追加 evidence。
5. Rule Engine 根据 evidence 形成 findings，给出 `inconclusive`/`observed` 等状态。
6. `NullAgentRuntime`（M1）用确定性模板生成报告；后续 LLM/Agent 只补充解释及调查建议，不能改写证据事实。
7. 保存 steps、evidence、findings、report，并正确进入 `completed` / `partial` / `failed`。
8. API 返回可追踪报告，支持反馈。

M1 可以用 FastAPI BackgroundTasks 或进程内队列进行实验，但生产可靠性要求另配持久队列/Worker；**不能将内存后台任务宣称为持久可靠执行**。

## 7. Elastic 9 接入（M2/M3，可关闭）

- `DBIC_SEARCH_BACKEND=null|elastic`，切换只影响 SearchAdapter。
- 已运行的 ES 不要在 DBIC Compose 中再次启动；读取 `DBIC_ELASTIC_URL` 和 API Key，连通性检查应可区分认证失败/网络失败/索引不存在。
- 按设计文档使用 `db-events-*`、`db-incidents-*` 等索引；实验先用 `db-events-lab`；模板/映射通过受版本控制的初始化脚本管理。
- 映射字段：`@timestamp`, `db.type`, `db.instance_id`, `event.category`, `event.code`, `message`；SQL 和 URI 做脱敏。
- ES 适配器输出统一 `Evidence`；核心不接受原生 ES JSON/DSL；先实现关键词过滤与按实例/时间搜索，再考虑 ES|QL / 向量检索。
- 提供 `scripts/seed_elastic.py` 幂等写入模拟数据；M2 的验收应包含 NullSearch 与 ElasticSearch 两种后端下的同一报告结构。
- Agent Builder 是独立可选组件：不得把唯一的 Playbook 和工具逻辑只放在 Kibana 中；许可/LLM 配置失败不影响 M1。

## 8. MCP 设计（M3）

使用标准 MCP SDK 暴露**明确命名的** `mysql_health`、`mysql_lock_chain`、`mysql_slow_query_summary` 工具，传入 instance_id，不直接传连接字符串。MCP 调用应与 REST 共享同一 PolicyEngine、ToolRegistry 和审计，不要实现两套业务逻辑。

MCP Server 需明确部署网络边界、客户端鉴权、工具列表的实例与环境授权。默认 loopback 测试；不得把无鉴权诊断端点暴露给局域网或公网。

## 9. 分阶段任务（Codex 请严格按顺序实现）

### M0：仓库骨架与基础服务

- 建立 pyproject、目录、格式化/类型检查、pytest、`.env.example`、Compose、PostgreSQL migration。
- `GET /healthz`、`GET /readyz`、`GET /api/v1/capabilities`。
- Task/ Evidence/ Finding/Report Pydantic 模型及单测。
- **验收**：`docker compose -f deploy/compose.yaml up -d --build` 启动；`pytest` 成功；`curl http://127.0.0.1:8086/healthz` 返回正常。

### M1：MySQL 确定性诊断闭环

当前执行 M1（用户已在 M0 验收后授权）。实现范围见 [m1-plan.md](m1-plan.md)，
升级、API、独立测试网络和已知限制见 [m1-validation.md](m1-validation.md)。
M1 增加 `0002_m1` request_id 迁移，默认诊断 schema 为 performance_schema；
驱动 PyMySQL[rsa] 在线程池执行。Docker 与 CI 使用源码内 uv.lock。

- 实装 MySQL 8.4 lab 连接与诊断白名单，`mysql.health`、`mysql.lock_chain`、`mysql.slow_query_summary`。
- 实装 Planner、PolicyEngine、RuleEngine、任务持久化、Evidence/Report 与 REST API。
- 建立锁等待有/无/权限不足及慢查询存在/缺失测试；实现 `NullMetrics`/`NullSearch`/`NullAgent`。
- **验收**：`POST /api/v1/diagnoses` 触发诊断；查询报告含证据 ID；权限不足返回 partial 而非虚构成功；无 ES/LLM 时仍能运行。

### M2：Prometheus 与 Elasticsearch 历史证据

- `PrometheusMetricsAdapter`、`ElasticSearchAdapter`、索引映射/样例数据、历史查询窗口和时间对齐。
- 断连/401/403/超时测试，结果统一到 Evidence 模型。
- **验收**：开关任一后端不破坏 MySQL 实时诊断；指标/日志证据可追溯。
- 远程 M2 隔离套件 224 passed、1 skipped；用户后续运行 `--require-search` 验收通过。
  ES 使用专用 lab 合成事件，真实日志采集/Prometheus 接入仍待验证，见 [m2-validation.md](m2-validation.md)。
- 用户已授权在指定 Linux 验证主机部署同样的隔离 lab；准备与执行状态见
  [remote-validation.md](remote-validation.md)。此授权不开放生产数据库或公网诊断接口。

### M3：Agent Runtime 与 MCP

当前执行 M3（用户在远程 M2 ES 样例验收后授权继续）。实施范围见 [m3-plan.md](m3-plan.md)，
配置与验证见 [m3-validation.md](m3-validation.md)。沿用 0002_m1，无新表迁移。
阶段清单优先于第 2 节远期 Compose 规划：本阶段不添加独立 worker 或 Redis。
用户后续选择 DeepSeek；请求方言由 DBIC_LLM_PROVIDER 显式选择。严格真实模型验收用
`python3 scripts/verify_agent.py`，不以现有 MCP/规则回退验收替代。

- MCP Server 复用已有工具；实现可配置的 LLM/Agent Adapter（首先简单工具调用，不默认多 Agent）；可选 Elastic Agent Builder。
- Agent 只提出后续调查建议，PolicyEngine 最终审核；对 prompt injection、过量工具调用、敏感数据泄漏建立负面测试。
- **验收**：LLM 不可用时退化为规则报告；超过预算被拒绝；AI 文本不得引用不存在的 Evidence。

### M4：Redis，再扩展 MongoDB/PostgreSQL/Doris

- Redis 首批：memory_breakdown、slowlog_summary、bigkey_audit_read；测试内存异常证据分离。
- MongoDB：current_ops/replica_health；PG：blocking_sessions/vacuum_health；Doris：fe_be_health/query_profile_summary。按接口能力渐进交付。
- **验收**：每种数据库至少 2 个可重复的诊断 fixture；不支持的能力显式返回 unavailable/not_implemented。

### M5：巡检与诊断质量

- 定时任务/告警 webhook、任务去重、反馈、历史案例回放、准确性/证据完整性评测、限流及安全审计。
- 仅在 M1–M4 稳定后考虑独立 Web UI 和受控修复流程。

## 10. API 调试示例

```bash
curl -X POST http://127.0.0.1:8086/api/v1/diagnoses \
  -H 'Content-Type: application/json' \
  -d '{"instance_id":"mysql-lab-01","problem_type":"lock_wait"}'

curl http://127.0.0.1:8086/api/v1/diagnoses/<TASK_ID>
curl http://127.0.0.1:8086/api/v1/diagnoses/<TASK_ID>/evidence
```

`<TASK_ID>` 为占位符，需替换为真实 API 返回的 task_id。认证机制在 M0 必须规定：即使本地开发允许单用户 token，也不能直接无鉴权绑定 0.0.0.0 对外提供接口。

## 11. 测试矩阵和验收命令

| 测试类 | 重点 |
|---|---|
| Unit | 路由选择、状态机、Playbook、规则、证据模型、脱敏 |
| Contract | Null/Elastic/Prometheus Adapter 的统一数据结构 |
| Integration | MySQL 锁等待、slow query、PostgreSQL 迁移、API |
| Failure | DB 断连、只读权限不足、超时、ES 不可达、LLM 不可达 |
| Security | 未授权实例、危险工具 ID、长查询、SQL 参数泄漏、prompt 注入 |
| Replay | 固定输入及预期 findings，不依赖 LLM 随机输出 |

统一提供：

```bash
python -m pytest -q
python -m compileall -q src
# 项目可以选 ruff / mypy，选型后写到 CI 并执行
```

每个 Milestone 结束时 Codex 输出：改动文件清单、启动命令、已运行测试及结果、已知限制、下一步工作；不得只输出“已完成”。

## 12. 给 Codex 的实现准则

1. **先读取两个设计文档和 AGENTS.md，再写代码。**
2. 每次只实现一个 Milestone；M0/M1 阶段不要引入 Elastic Agent Builder、LangGraph、Kafka、Kubernetes。
3. 未确定数据库版本或权限时先返回 `unavailable`，不要伪造执行结果。
4. 所有工具仅允许预设只读操作；禁止任意 SQL、kill、DDL、FLUSH、运行 shell 操作生产实例。
5. 如需实验故障，只能在 `*-lab` 容器中构造，并有自动回滚/清理机制。
6. 核心类型稳定、依赖方向正确、测试和文档与代码同步。
7. 不覆盖或清理现有 Elasticsearch 容器、卷及数据。
8. 安全默认：接口监听容器内 0.0.0.0，但宿主端口仅绑定 127.0.0.1；未授权不得访问实际数据库。

## 13. 首轮实际执行任务（可直接发给 Codex）

M0 落地补充：端点集中在 `src/dbic/main.py`。psycopg 3 同步就绪查询在线程池运行，
readyz 核对迁移版本 `0001_m0`。Compose 的一次性 dbic-migrate 服务是 API 启动前置条件。
具体运行步骤见根目录 README，实测与未验证项见 [m0-validation.md](m0-validation.md)。
契约细化见架构第 12 节；Repository、诊断执行器、Null adapters 与诊断 API 留到 M1。

> 请阅读 `docs/architecture.md`、`docs/implementation.md` 和 `AGENTS.md`。仅实现 M0，不要提前开发 M1-M5。使用 Python 3.12、FastAPI、Pydantic v2、PostgreSQL + SQLAlchemy/Alembic、Docker Compose，创建模块化单体骨架和 Port 接口、诊断数据模型、健康检查、能力列表、任务持久化表迁移、单元测试。必须在 Mac Apple Silicon 的 OrbStack 中可运行。不要修改已有 Elastic 9 容器，不要连接生产数据库，不要将敏感凭据写入仓库。运行测试并汇报执行结果，列出 M1 的建议任务清单。
