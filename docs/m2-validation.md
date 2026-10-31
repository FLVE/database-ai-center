# M2 历史证据：配置与验收

## 最新验收记录（2026-10-31）

代码已发布到 GitHub，远程 Linux x86_64 lab 部署完成，隔离套件 **224 passed、1 skipped**。
API/运行数据库健康，迁移成功；没有更改已有 ES/Kibana 容器或生产数据库。
用户随后配置只读 ES key、将 DBIC API 接入已有 ES 网络，并在专用索引准备合成事件。
用户执行以下命令成功（第一次因索引不存在失败，补齐后再次通过）：

```bash
python3 scripts/verify_m2.py --skip-suite --require-search \
  --start 2026-10-31T00:00:00Z --end 2026-10-31T00:30:00Z
```

输出：无窗口锁等待 completed/2 份证据；有窗口锁等待 partial/4 份证据；
有窗口慢查询 partial/4 份证据，明确报告 `no timed application digest samples`。
最终 `PASS: M2 API verification with Elasticsearch event evidence ...; suite skipped`。
这是实际 ES 协议、映射、只读认证及证据归档验收；不是实际 MySQL 日志采集验收。
Prometheus 仍关闭，真实模型尚未接入。以下为此前阶段记录，不能覆盖本节的最新状态。

M0、M1 已由用户在 OrbStack 验收。M2 的部署 API 验收及隔离测试套件也已由用户在
宿主终端运行通过：**211 passed、1 skipped、1 warning**（17.16 秒）。
本次未要求历史后端可用；真实 Elasticsearch 9 与 Prometheus 数据接入仍待单独验收。
代理会话的沙箱仍无法访问 Docker socket 或监听 loopback，容器执行结果来自用户提供的输出。
本次开发未连接生产、未修改现有 ES/Kibana 容器或卷，代理未执行样例导入。

## 实现与接口

- `src/dbic/adapters/http.py`：本地目标限制、TLS、禁止代理/重定向、超时和响应上限。
- `src/dbic/adapters/metrics/prometheus.py`：固定连接数/慢查询速率，单位、UTC、采样网格与缺口。
- `src/dbic/adapters/search/elastic.py`：固定 lab 索引、实例与类别，结构化日志和错误降级。
- `src/dbic/adapters/history_factory.py`、两种 Null adapter：独立开关与默认退化。
- `src/dbic/core/{models,evidence,history,history_rules,runner}.py`：历史元数据、证据归档、规则事实。
- `src/dbic/api/routes/diagnoses.py`、`settings.py`、`main.py`：窗口校验、配置、M2 能力清单。
- `src/dbic/adapters/agent/null.py`：规则报告、按观测时间排序的时间线。
- `configs/elastic/`、`scripts/seed_elastic.py`：新 lab 索引映射及 create-only 样例。
- `tests/unit/test_history_*.py`、`test_seed_elastic.py`：HTTP 契约、脱敏、降级和索引冲突。
- `tests/integration/test_history_http.py`：真实 loopback HTTP 成功/401/403/503/超时/断连。
- `tests/integration/test_m1_live.py`：新增 PostgreSQL 历史证据、窗口、引用重建测试。
- `scripts/verify_m2.py`：部署后 smoke 验收与隔离测试；`verify_m1.py` 兼容 M2 回归。
- `pyproject.toml`、`uv.lock`：版本 0.3.0，HTTPX 提升为运行依赖。
- `.env.example`、`deploy/compose.yaml`、CI、README、AGENTS 与架构/实施文档同步更新。

无新迁移，沿用 `0002_m1`。既有诊断 API 路径和 Port 签名不变；创建请求的 window
新增最长 24 小时、结束不在未来的校验（允许 5 秒时钟误差）。capabilities 增加
`historical_backends`，只表示配置，不保证后端可用。无 window 的任务不查询历史后端。

## 先验收默认 Null 模式

保留现有 `.env` 和 MySQL diag 账号，无需重新初始化、清库或重设密码：

```bash
docker compose --env-file .env -f deploy/compose.yaml config --quiet
docker compose --env-file .env -f deploy/compose.yaml up -d --build
python3 scripts/verify_m2.py
```

脚本检查实时诊断、带窗口的两个问题、四份证据和时间窗/引用归档。默认 Null 时，
历史证据应为 unavailable、任务 partial，MySQL 实时证据仍成功。
接着运行隔离 verify profile：真实 PostgreSQL/MySQL、HTTP socket 故障夹具和全部单元测试。
慢查询摘要允许三种明确的数据缺口：无计时摘要样本、digest consumer 未启用/缺失、
statement instrumentation 不完整。验收必须同时确认任务为 partial、报告列出缺失原因，
且没有使用该不可用摘要支持结论；脚本会打印 INFO，而不把它当作摘要采集成功。
MySQL health 仍必须成功，权限不足、超时、连接失败或未知原因仍会失败，并输出工具名、
status 和 limitations。无需为了通过 smoke 验收修改运行中 lab 的采集开关或生成业务流量。
只检查部署 API 用 `--skip-suite`。该验证会在 DBIC PostgreSQL 中留存 lab 诊断任务，
不会向业务数据库写数据；测试库夹具仍仅作用于隔离的 test-mysql/test-postgres。

## 启用已有本地历史服务

不新建 Prometheus/exporter，不修改已有 ES/Kibana 容器。先确认实际本地端口、协议、
证书及 MySQL lab 的 Prometheus 标签；不要将生产服务转发到允许的本地端口。
在 `.env` 追加所需键，不覆盖原有密码：

| 键 | 用途 |
|---|---|
| DBIC_SEARCH_BACKEND | `null`（默认）或 `elastic` |
| DBIC_ELASTIC_URL | API 容器访问已有本地 ES 的 URL；宿主服务用 `http(s)://host.docker.internal:<实际端口>` |
| DBIC_ELASTIC_API_KEY | ES 编码后的 API key；仅授予 `db-events-lab` 的 `read` |
| DBIC_METRICS_BACKEND | `null`（默认）或 `prometheus` |
| DBIC_PROMETHEUS_URL | 同样使用实际本地端口；不接受附加 URL path/query/凭据 |
| DBIC_PROMETHEUS_TOKEN | 可选 Bearer Token |
| DBIC_PROMETHEUS_INSTANCE_LABEL | 默认 `instance`，用于精确匹配所选 lab |
| DBIC_PROMETHEUS_INSTANCE_VALUE | 必填，实际 exporter 的标签值；不假设等于逻辑实例 ID |
| DBIC_HISTORY_CA_FILE | 可选可信 CA PEM 路径；TLS 验证始终开启 |

目标只允许 localhost、127.0.0.1、::1、host.docker.internal，且必须有显式端口。
在容器里 localhost 指容器自身，不能用来访问宿主 ES/Prometheus。
CA 如需带入镜像，只复制**公开 CA 证书**到 `configs/`，设置容器路径 `/app/configs/...pem`
后重建镜像。私钥和 token 不进入镜像/源码。历史后端认证失败会在证据里显示 forbidden；
不影响 readyz（readyz 只检查 PostgreSQL）。配置本身无效则启动失败，避免静默忽略配置错误。

Prometheus 需已有 `mysql_global_status_threads_connected` 和
`mysql_global_status_slow_queries`（计数器）的 lab 序列。后者查询 `rate(...[5m])`，
不是任务窗口内的慢查询总量。默认最多 301 点；缺点、warning 或 info 均标注限制。
多条匹配序列会被拒绝，应选能唯一标识该 lab 的标签；不会替用户聚合可能不同的实例。

## 新建 lab 索引与样例

此步骤可选，且是显式写入**专用 lab 索引**的离线脚本，不是诊断 API 的能力。
运行之前确认目标是已有的本地 ES。使用有该专用索引 `create_index`、`create_doc`、
`read`、`view_index_metadata` 权限的临时 seed key；API 日常 key 只保留 read 权限。
脚本不更新已有 mapping，不删除数据；同名但不同 mapping 会停止；文档用 `_create`，
409 后只读取核对，内容不同会停止。新索引可能已建好但中途失败，脚本不会删除它来回滚。

宿主机执行（先在终端安全设置 `DBIC_ELASTIC_URL` 为实际 loopback URL、
`DBIC_ELASTIC_API_KEY` 为临时 seed key；若需要 CA，指定宿主机上的证书路径）：

```bash
uv sync --locked --python 3.12
uv run python scripts/seed_elastic.py
```

脚本从 `.env` 读取其余配置，进程环境变量优先；不要把临时写 key 放进 API 的 `.env`。
样例是明确标记的合成事件，时间固定为 `2026-10-31T00:05:00Z`、`00:10:00Z`，
只适合对应窗口。反复运行不会重写已有文档。若需要其他窗口，先审阅修改样例文件的
时间和**新文档 ID**，不要把合成事件当作真实故障证据。

## 真实历史数据验收

启用配置后重建 API。用已有指标和日志共同覆盖的真实时间段，替换下面的示例窗口：

```bash
docker compose --env-file .env -f deploy/compose.yaml up -d --build
python3 scripts/verify_m2.py --start 2026-10-31T00:00:00Z --end 2026-10-31T00:30:00Z --require-history
```

`--require-history` 要求两个后端都启用，两个问题都返回非空历史证据；无数据会失败。
只验证 ES 时使用 `--require-search`；它要求两个问题的事件证据非空，允许指标后端为 Null。
默认不带此参数只验证降级和归档，**不能作为真实后端可用的证明**。
没有历史 Prometheus 采样时不要为通过验收伪造指标；先完成 Null/单后端验收并记录缺口。
HTTP 故障夹具仅证明客户端真实 HTTP 行为，MockTransport 证明协议和解析，
二者都不能代替 Elasticsearch 9 的映射/权限兼容性及真实 Prometheus 标签验证。

## 已执行的检查与未覆盖项

2026-10-31，当前 Mac Python 3.12 环境实际执行：

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/python -m compileall -q src scripts
docker compose --env-file .env -f deploy/compose.yaml config --quiet
UV_CACHE_DIR=/private/tmp/dbic-uv-offline uv lock --check --offline
```

结果：173 passed、21 skipped、1 warning；ruff 与格式检查通过；mypy 37 个源文件通过；
编译、Compose 静态校验和锁文件检查通过。跳过项为 8 个隔离数据库用例、1 个独立 DSN
迁移用例、12 个 loopback HTTP 用例。Starlette/httpx 弃用告警沿用 M1，未屏蔽。
本机用缓存 wheelhouse 离线重装了 0.3.0 项目元数据；没有宣称在此网络环境完成全新依赖下载。
当时 Docker 构建与上述真实用例因沙箱限制未执行；用户后续宿主终端验收结果见下文。
CI 已启用 HTTP 用例，但当前没有 CI 执行结果。

后续验收脚本修正：用户实测两个 lock_wait 场景通过，slow_query 停在原来过严的
“realtime MySQL tools failed”断言；该输出未包含具体证据原因，不能据此确认现场是数据缺失。
`scripts/verify_m2.py` 已按上述三种缺口修正，并保留真实故障拦截。
新增 `tests/unit/test_verify_m2.py`，通过 TestClient 执行脚本三个 API 场景，覆盖缺口、
连接/权限/超时错误、伪成功任务和缺失解释。18 项回归通过；全量复跑为
191 passed、21 skipped、1 warning，ruff/格式/mypy 通过。这属于内存 API 验证，
该内存验证本身不代表用户现场问题已消失。

用户随后在宿主终端重新执行 `python3 scripts/verify_m2.py`，提供结果：

```text
211 passed, 1 skipped, 1 warning in 17.16s
PASS: M2 API verification (historical backend availability not required); isolated suite passed
```

这确认修正后的部署 API 验收、隔离 PostgreSQL/MySQL 及 loopback HTTP 故障测试套件通过。
剩余跳过项为未配置独立 DSN 的迁移测试（隔离套件另有真实迁移验证）。
弃用告警仍为 Starlette/httpx，未屏蔽。本次输出未包含慢查询的 INFO 详情，因此不追认
现场缺口的具体原因；也不把这次通过视为真实 ES/Prometheus 历史数据接通的证明。

保留 M1 的进程内队列限制：强制退出后不自动恢复 running/queued。
历史事件只支持本仓库 lab mapping，不支持任意既有日志 schema、ES|QL 或相似案例检索。
非 ES 的真实搜索替代实现契约验收仍是后续架构验证项；目前替代路径为 Null 和注入 Fake。
样例导入、真实 ES 9/TLS/权限、真实 Prometheus 标签/采样尚未实测。

回退历史接入：把两个 backend 设为 `null` 并重建 API，保留 PostgreSQL 数据和所有索引/卷；
不用 `down -v`，不自动删除 lab 索引。补齐 M2 真实历史数据验收后，下一阶段为 M3 Agent Runtime/MCP，
继续复用现有权限和证据规则，不提前开放生产或写操作。

协议依据：[Prometheus HTTP API](https://prometheus.io/docs/prometheus/latest/querying/api/)、
[Elasticsearch Search](https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-search)、
[Elasticsearch Create document](https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-create)。

用户指定远程主机的部署准备与限制见 [remote-validation.md](remote-validation.md)。
