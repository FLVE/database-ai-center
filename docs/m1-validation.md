# M1 实现与验收

M0 数据和既有 Elasticsearch/Kibana 保留。M1 不连接生产库，无任意 SQL/命令诊断接口。

## 最终验收结果（2026-10-31）

用户在 Mac Apple Silicon + OrbStack 宿主终端执行 `python3 scripts/verify_m1.py`，
输出确认验证镜像构建成功、两个独立测试数据库 healthy，最终为：

```text
82 passed, 1 skipped, 1 warning in 2.98s
PASS: M1 live API and isolated database test suite
```

M1 本地验收通过。脚本成功出口覆盖真实 API 鉴权、任务/证据/报告/反馈，以及独立库中的
有锁、无锁、权限不足、摘要存在、采集缺失、业务表读取拒绝和原子状态/归档回滚测试。
真实 PostgreSQL 的 0002 迁移及重复升级也已由测试运行验证。
这些证据来自用户终端输出，不是当前沙箱直接访问 Docker 所得。

剩余 1 个 skip 是未配置 DBIC_TEST_DATABASE_URL 的原有独立迁移用例；M1 真实测试已运行，
不能将这个 skip 解读为没有测试 PostgreSQL。warning 为 Starlette/httpx 弃用提示。
当前仍是单机实验实现，进程中断恢复与累计摘要的限制见文末；未进入 M2。

## 从当前 M0 环境升级

工作区 `.env` 已补充随机 MYSQL_DIAG_PASSWORD（不入库，权限 600）。先在当前运行中的
mysql-lab 建立最小权限账号，再更新容器。命令从仓库根目录执行：

```bash
python3 scripts/setup_mysql_lab.py
docker compose --env-file .env -f deploy/compose.yaml up -d --build
curl --noproxy '*' --fail http://127.0.0.1:8086/readyz
python3 scripts/verify_m1.py
```

首次安装需先启动 PostgreSQL/MySQL：
`docker compose --env-file .env -f deploy/compose.yaml up -d dbic-postgres mysql-lab`，
等待 mysql-lab healthy 后再运行上述账号脚本。账号脚本只创建 diag，并授予七张
Performance Schema 表的 SELECT；若账号已存在则失败退出，不重置、不接管旧账号。
成功运行过脚本后再次部署应跳过它。API 不持有 MySQL root 密码。

迁移服务执行新增 `0002_m1`（仅增加任务 request_id 列），随后 API 才启动。
readyz 必须在新镜像与新迁移均部署后才为 ready；不要只重建 API 跳过迁移。
如需回到 M0，优先回滚应用并保持数据；M0 readyz 只识别旧 revision，需要审阅并手工
协调降级。0002 的 downgrade 只删除 request_id 列，但仍会丢失关联数据，不自动执行。

## API

- POST /api/v1/diagnoses：instance_id=mysql-lab-01，problem_type=lock_wait/slow_query，
  可选 UTC window 与 parent_task_id（必须为同实例、同问题的终态任务）。返回 202 queued。
- GET /api/v1/diagnoses/{id}：task、steps、report、feedback。
- GET /api/v1/diagnoses/{id}/evidence：该任务的已脱敏证据。
- POST /api/v1/diagnoses/{id}/feedback：verdict=correct/partial/incorrect，可选确认说明和备注。
  仅终态接受反馈；不修改规则证据或自动升级根因置信度。
- 上述端点和 capabilities/OpenAPI 均需本地 Bearer Token；身份为 local-user。

例如在终端安全设置 DBIC_API_TOKEN 后：

```bash
curl --noproxy '*' --fail -H "Authorization: Bearer ${DBIC_API_TOKEN}" \
  -H 'Content-Type: application/json' http://127.0.0.1:8086/api/v1/diagnoses \
  -d '{"instance_id":"mysql-lab-01","problem_type":"lock_wait"}'
```

MySQL 连接限定 diag@mysql-lab:3306/performance_schema，不能配置任意远程 DSN。
registry 的 env:MYSQL_LAB_DSN 在本阶段由 DBIC_MYSQL_DIAG_PASSWORD 安全构造，未提供
通用环境变量引用解析。默认库使用 performance_schema，避免需要额外授予 mysql 库权限。
YAML Playbook/Policy 为源码默认策略的可测试镜像，不是热更新配置；修改需要同步代码和测试。

## 测试分层

单元/API：状态机、五条诊断路径、权限/输入拒绝、错误脱敏、重试、时间窗限制、预算、超时、
证据引用、缺失/过期证据、MySQL 固定查询与单位、Null adapters。无需外部服务。

真实测试：verify profile 的 test-postgres/test-mysql 只接 verification 内部网络，无宿主端口。
dbic-tests 在该网络将别名 dbic-postgres/mysql-lab 解析到测试服务，不能访问运行中 lab。
覆盖实际 PostgreSQL 存储和审计、有锁/无锁/权限不足、摘要存在/采集关闭、账号禁止读取业务表。
锁事务最终回滚，权限和采集状态恢复；专用测试库保留测试任务和初始化夹具，不清理运行库。
可单独运行：

```bash
docker compose --env-file .env -f deploy/compose.yaml --profile verify run --build --rm dbic-tests
```

测试完可停止专用测试服务，不删除数据：
`docker compose --env-file .env -f deploy/compose.yaml --profile verify stop test-mysql test-postgres`。

## 当前结果与限制

下面保留容器验收前的本机 Python 3.12 检查记录；最终真实验收结果以上文为准：

| 检查 | 结果 |
|---|---|
| `.venv/bin/python -m pytest -q` | 75 passed、8 skipped、1 warning |
| `.venv/bin/ruff check .` | All checks passed |
| `.venv/bin/ruff format --check .` | 53 files already formatted |
| `.venv/bin/mypy` | 31 个源码文件通过 |
| `.venv/bin/python -m compileall -q src scripts tests` | exit 0 |
| `uv lock --check --offline`（临时可写缓存） | 44 packages，exit 0 |
| `alembic upgrade head --sql`（临时实验配置） | 0001 + 0002 SQL 生成通过 |
| Compose verify profile 配置及隔离断言 | 通过；API loopback，测试服务只在 verification 网络 |

8 个 skip 包括 7 个 M1 真实测试及原有 DSN PostgreSQL 测试；不是测试通过。
warning 是既有 Starlette/httpx 弃用提示。
新增依赖通过已有本机 wheel 缓存安装；锁内当前平台已安装包版本逐项一致，
缺少的 colorama/tzdata 为非当前平台依赖。完整离线 uv sync 曾因构建依赖 hatchling
缓存路径不可用失败，随后用本机缓存重建安装并核对版本；没有声称容器构建已通过。
当时沙箱无法连接 OrbStack，新镜像构建、账号创建、0002 真迁移和真实 MySQL 用例
未能在该会话内直接验证。后续用户终端运行已补齐真实 API 和独立数据库验收证据。

实际尝试 `python3 scripts/setup_mysql_lab.py` 及 Compose verify run 均被 Docker socket
权限阻断，没有执行 lab SQL，也没有改变现有运行容器。启动命令请由可访问 OrbStack
的宿主终端执行；本次未提交 Git，未操作 Elasticsearch/Kibana 或生产数据库。

## 实现文件

- 核心：`src/dbic/core/models.py`、`ports.py`、`planner.py`、`policy.py`、`evidence.py`、
  `rules.py`、`runner.py`；复用原有 `engine.py` 状态转换。
- 适配器：`src/dbic/adapters/registry.py`、`persistence/tasks.py`、`persistence/postgres.py`、
  `agent/null.py`、`metrics/null.py`、`search/null.py`，及包初始化文件。
- MySQL：`src/dbic/plugins/mysql/queries.py`、`diagnostics.py`，及包初始化文件。
- API：`src/dbic/api/service.py`、`api/routes/diagnoses.py`、`src/dbic/main.py`、
  `src/dbic/settings.py`，及包初始化文件。
- 配置与部署：`configs/instances.yaml`、`configs/policies.yaml`、`configs/playbooks/*.yaml`、
  `deploy/compose.yaml`、`deploy/Dockerfile`、`.env.example`、本地未提交的 `.env`。
- 迁移与依赖：`migrations/versions/0002_m1.py`、`pyproject.toml`、`uv.lock`、
  `.github/workflows/checks.yaml`。
- 脚本与测试：`scripts/setup_mysql_lab.py`、`scripts/verify_m1.py`、`tests/fakes.py`、
  `tests/unit/test_api.py`、`test_models.py`、`test_diagnoses.py`、`test_m1_contracts.py`、
  `test_mysql_tools.py`、`tests/integration/test_migration.py`、`test_m1_live.py`、`tests/__init__.py`。
- 文档：`AGENTS.md`、`README.md`、`docs/architecture.md`、`docs/implementation.md`、
  `docs/m1-plan.md`、本文件。

执行器是单进程实验实现，16 个待处理任务上限、每实例并发 1、90 秒任务预算、8 次工具预算。
PyMySQL 在线程池执行，连接/读写有超时；取消协程不会立即中止线程，线程持有实例锁直到结束。
正常失败保留步骤/证据并归档；数据库本身断连时最后状态保留并记录任务 ID，需人工排查。
进程强制退出后不恢复 queued/running，也不允许把仍标记执行中的任务作为 retry parent。
慢摘要永远注明累计口径，当前返回 partial；本阶段不判定慢 SQL 增长根因。
