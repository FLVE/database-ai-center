# Database AI Center — M2

基线为 [architecture.md](docs/architecture.md) V2.1 与
[implementation.md](docs/implementation.md)。M0、M1 本地验收已通过；M2 新增可选历史指标与事件证据。
M2 配置、升级及尚待实测项目见 [docs/m2-validation.md](docs/m2-validation.md)。
Linux 服务器从 GitHub 拉取并部署的步骤见 [docs/remote-validation.md](docs/remote-validation.md)。

## 本阶段范围

- Python 3.12、FastAPI、Pydantic v2、SQLAlchemy 2、psycopg 3、Alembic。
- `core/models.py`：Task、Evidence、Finding、Report、Instance、ToolResult 和 UTC 时间窗。
- `core/ports.py`：六个异步 Protocol；核心不依赖数据库驱动、ES 或 Agent SDK。
- `core/engine.py`：纯函数状态机 `queued → running → completed/partial/failed/cancelled`；
  非法转换包含任务 ID，终态不能重入。重试应创建新任务并保留 parent_task_id。
- PostgreSQL 八张主数据表、事务存储、步骤/证据/审计、报告/反馈及 request_id 迁移。
- MySQL 8.4 的 health、lock_chain、slow_query_summary，固定只读查询，不返回 SQL 原文。
- Planner/Policy/规则、Null adapters 和进程内执行器；证据不足返回 partial/inconclusive。
- 健康检查、迁移就绪检查、能力列表、统一错误与服务端生成的 request_id。
- Prometheus 固定指标、Elasticsearch `db-events-lab` 结构化事件，只读且默认关闭。
- 带时间窗的任务追加两份历史证据；历史源失败仍保留实时诊断结果并标为 partial。

运行库还包括 uvicorn、pydantic-settings、PyMySQL[rsa]、PyYAML 和 HTTPX；
M2 将 HTTPX 从开发依赖提升为运行依赖，没有新增 ES/LLM SDK。开发依赖为 pytest、ruff、mypy。
本地安装使用 `uv sync --locked`；Docker 使用 uv 0.11.7 与 `uv sync --frozen` 安装锁定依赖。

## Mac Apple Silicon / OrbStack 启动

在仓库根目录执行。镜像使用原生多架构标签，不强制 amd64。Compose 项目名固定为
`dbic-lab`，仅管理本项目容器、网络和卷，不声明现有 Elasticsearch/Kibana 服务。
只有显式启用历史配置后才会查询已有本地历史服务；不自动建索引或导入数据。

1. 首次安装才执行 `cp .env.example .env`，已有 `.env` 请保留。设置四项独立随机值：
   `DBIC_PG_PASSWORD`、`DBIC_API_TOKEN`（至少 24 字符）、`MYSQL_ROOT_PASSWORD`、
   `MYSQL_DIAG_PASSWORD`（32–128 位随机字母数字）。
   可运行 `python3 -c 'import secrets; print(secrets.token_hex(32))'` 为每项生成新值。
   `.env` 不入库；建议 `chmod 600 .env`。随机十六进制可避免 Compose `$` 插值问题。
2. `docker compose --env-file .env -f deploy/compose.yaml config --quiet`
3. `docker compose --env-file .env -f deploy/compose.yaml up -d --build`
4. `docker compose --env-file .env -f deploy/compose.yaml ps -a`
5. `curl --noproxy '*' --fail http://127.0.0.1:8086/healthz`
6. `curl --noproxy '*' --fail http://127.0.0.1:8086/readyz`

从根目录运行时也可以使用基线命令 `docker compose -f deploy/compose.yaml up -d --build`，
Compose 默认读取当前目录 `.env`；显式 `--env-file` 更便于从其他目录调用。
API 启动前，迁移服务等待 PostgreSQL 就绪并运行 `alembic upgrade head`。
仅 API 映射 `127.0.0.1:8086`；PostgreSQL/MySQL 不暴露宿主端口，保留在内部网络。
API 同时连接普通 bridge 网络 `api-host`：仅连接 internal 网络时 Docker 不发布宿主端口。
该网络同时允许 API 出站；应用仍只允许配置本地 lab 数据库。
首次创建 diag 账号运行 `python3 scripts/setup_mysql_lab.py`，详见 M1 升级步骤。
API 仅持有 diag 密码；root 密码只用于实验实例和独立测试，不传入 API。

能力列表需 `Authorization: Bearer <DBIC_API_TOKEN>`；在终端中安全设置同名环境变量后：

```bash
curl --noproxy '*' --fail -H "Authorization: Bearer ${DBIC_API_TOKEN}" http://127.0.0.1:8086/api/v1/capabilities
curl --noproxy '*' --fail -H "Authorization: Bearer ${DBIC_API_TOKEN}" http://127.0.0.1:8086/openapi.json
```

MySQL 返回三个已实现工具，其余数据库返回 `not_implemented` 和空工具清单。
实现状态不代表目标实例连通性。诊断、证据、反馈 API 均需 Token；M1 不开放 MCP。
`/healthz` 无鉴权且只报告进程存活；`/readyz` 无鉴权，仅返回是否连接到期望迁移版本，
失败返回脱敏的 503；OpenAPI 同样受 token 保护。所有响应带 `X-Request-ID`。

## 本地开发与验证

已完成 M1 的 diag 账号设置后，重建 M2 镜像，在宿主终端运行：

```bash
python3 scripts/verify_m2.py
```

脚本绕过代理检查真实 API、历史窗口、降级和报告引用，再构建 verify profile 运行独立测试库及 HTTP 故障夹具。
默认 Null 配置也能验收；真实历史数据验收使用文档中的 `--require-history`。
`verify_m1.py` 仍可运行 M1 回归（包括反馈），兼容 M2 能力版本。
测试会临时构造行锁、撤销测试账号权限、关闭测试实例 digest consumer，并在 finally
恢复；测试服务与运行中 lab 网络隔离，不修改运行中 diag 权限或采集开关。
原 `scripts/verify_m0.py` 保留为 M0 历史入口，不适用于 M1 的能力契约。

```bash
uv sync --locked --python 3.12
uv run python -m pytest -q
uv run python -m compileall -q src
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

主机启动需显式设置 `DBIC_API_TOKEN`、`DBIC_DATABASE_URL`（完整、正确 URL 编码的
`postgresql+psycopg` DSN，目标仅限 localhost/loopback 的 `dbic` 或 `dbic_test`），然后：

```bash
uv run alembic upgrade head
uv run uvicorn dbic.main:create_app --factory --host 127.0.0.1 --port 8086
```

应用不自动读取 `.env`；Compose 将单独密码传入，应用用 SQLAlchemy URL 安全构造 DSN。
环境仅允许 `lab`，不支持远程数据库地址或 URL 选项。localhost 限制不能识别用户自建的
端口转发，配置者仍须确保目标为隔离实验库。

重复迁移验证（仅本项目）：

```bash
docker compose --env-file .env -f deploy/compose.yaml run --rm dbic-migrate
docker compose --env-file .env -f deploy/compose.yaml run --rm dbic-migrate
```

集成测试要求单独的本地 PostgreSQL `dbic_test` 数据库。将其完整 DSN 设置为
`DBIC_TEST_DATABASE_URL` 后运行 `uv run pytest -q -m integration`。测试重复升级两次、
验证八张表和就绪状态；不清库、不自动降级。未设置时明确 skip，不用 SQLite 替代 PostgreSQL。
CI 提供隔离 PostgreSQL 17 服务并执行此测试；trust 认证仅用于一次性 CI 服务。

## 边界与下一阶段

任务先入 PostgreSQL，再交给进程内 BackgroundTasks；状态更新有期望状态条件，结果事务归档。
进程被强制终止时 queued/running 任务会保留，不自动恢复，不宣称持久可靠队列。
持久化层验证报告引用属于同一任务的真实证据；规则不自动升级 confirmed_root_cause，
人工确认只写 feedback，不改写原报告。工具不收集 SQL 原文/锁数据/业务行；摘要仅保留 Digest。
慢查询摘要是累计计数；历史查询另外读取连接数、慢查询计数器速率和结构化事件。
历史源不可用、采样缺口、截断均显式标注；不能据此确认精确慢日志增长或根因。

完成 M2 真实环境验收后再进入 M3（Agent Runtime 与 MCP）；当前不实现 LLM/MCP。

停止服务用 `docker compose --env-file .env -f deploy/compose.yaml stop`，保留卷。
M0 新文件可整体移除回退；不要用 `down -v` 清除已有实验数据。迁移 downgrade 会删除
本项目八张表，只有确认数据可丢弃后才能手工执行；不自动运行破坏性回滚。

FastAPI 生命周期测试与迁移离线检查分别遵循
[FastAPI 官方说明](https://fastapi.tiangolo.com/advanced/testing-events/) 和
[Alembic 官方说明](https://alembic.sqlalchemy.org/en/latest/offline.html)。
