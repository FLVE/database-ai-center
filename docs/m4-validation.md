# M4.1 配置与验收

范围见 [m4-plan.md](m4-plan.md)。本批次只实现 Redis 单节点 lab；MongoDB、PostgreSQL、
Doris 诊断与 M5 未实现。PostgreSQL 仍是系统存储，不等于已实现 PostgreSQL 诊断插件。
应用 0.5.0，capabilities.milestone=M4；无需新增迁移，readyz 仍核验 0002_m1。

## 已实现文件

| 路径 | 用途 |
|---|---|
| `src/dbic/core/redis_models.py`、`redis_rules.py` | 纯证据 Schema、时间/引用校验、内存事实与有证据的假设 |
| `src/dbic/plugins/redis/` | 有限 INFO / SLOWLOG GET、脱敏、固定审计文件读取 |
| `core/planner.py`、`policy.py`、`runner.py`、`api/service.py` | 显式数据库类型路由与逐工具授权 |
| `adapters/diagnostic_tools.py`、`adapters/agent/factory.py` | 静态工具分派、Redis NullAgent 与 MySQL 模型隔离 |
| `main.py`、`mcp/server.py`、`settings.py`、`api/routes/diagnoses.py` | memory_pressure、三个 Redis MCP 工具、能力及开关 |
| `configs/`、`deploy/compose.yaml`、`.env.example` | lab allowlist、可选 Redis profile、隔离测试实例 |
| `scripts/init_redis_lab.py`、`verify_m4.py` | 私有 ACL/env 生成、只读部署验收 |
| `tests/unit/test_redis*.py`、`test_verify_m4.py`、`tests/integration/test_redis_live.py` | 规则、权限、文件边界、真实 Redis/PostgreSQL/MCP 验证 |

MySQL 工具与 Agent 的工具集合维持 MySQL 专属，不能因新增全局工具 ID 而接受 Redis 工具。
M1–M3 验收脚本识别 M4，旧报告保持可读；M0 脚本仍为历史契约入口。
唯一新增运行库为 redis-py 8.1.0（uv.lock）；未更新其他依赖版本。

## 在已有 MySQL lab 上启用

在仓库根目录执行，不覆盖现有 `.env`：

```bash
uv sync --locked --python 3.12
python3 scripts/init_redis_lab.py
docker compose --env-file .env --env-file dist/redis-lab/redis.env \
  -f deploy/compose.yaml --profile redis up -d --wait redis-lab
DBIC_MCP_ENABLED=true docker compose --env-file .env --env-file dist/redis-lab/redis.env \
  -f deploy/compose.yaml up -d --build --no-deps --wait dbic-api
python3 scripts/verify_m4.py --require-mcp
python3 scripts/verify_m3.py --require-mcp
python3 scripts/verify_m2.py --skip-suite
```

初始化脚本只生成 `dist/redis-lab/users.acl` 与 `redis.env`，不连接 Redis、不修改现有数据库。
已有文件保留；不自动轮换密码。明文 env 文件为 0600，目录 0700；Redis 只读挂载 ACL 哈希文件。
该目录被 Git 和源码发布包排除。API 只获得 diag 密码，不获得测试管理员凭据。
每次重建须保留第二份 env 文件，或由部署者把 Redis 密码和开关放入自己的秘密配置。
命令中的 MCP 开关只对本次命令生效，不会改写私有 `.env`。

Redis 不映射宿主端口；API 仍为 127.0.0.1:8086。Redis 数据仅作易失性 lab 状态，
关闭持久化、限额 64 MiB、noeviction；不用于真实缓存数据或生产。
镜像固定 `redis:8.4.8@sha256:8966e8ff525ef9e3f3ea476cb15db868a41a701f5188c77c138e3ce613c2d81a`，
已检查 Linux ARM64 和 AMD64 manifest。
客户端使用 RESP2、driver_info=None、零重试；实测 diag 无额外 CLIENT SETINFO 权限需求。

远程若后续部署，必须继续保留既有 ES 与 M3 两个 override 文件；不能复制本机命令后丢掉它们。
本机验收及用户复验通过后，用户已授权提交并推送本批次源码。
源码发布不包含远程 M4 部署，服务器上的 M3/DeepSeek 保持原状。

## API 与报告

使用现有鉴权创建端点，body：

```json
{"instance_id":"redis-lab-01","problem_type":"memory_pressure"}
```

返回 202，读取任务详情、证据及反馈沿用原路径。非空 window、跨数据库问题和额外命令/路径
参数返回 422；未授权或禁用实例返回 403。不支持 Cluster、Sentinel、历史增长分析。
三个工具固定执行，通常三条证据；所有重要结论引用本任务 evidence_ids。
Redis 不向 LLM 发请求，报告 agent_status=disabled；MySQL 模型开关和 schema 不变。

默认未提供大 Key 审计文件时，该步骤 unavailable；慢日志只代表保留采样。
因此有效报告通常是 partial，不能把它当作服务故障，也不能掩盖缺失证据以改成 completed。
verify_m4 要求实时内存成功、慢日志 ok/empty、归档引用正确；不会把断连/权限失败当作通过。
`--require-audit` 要求有效审计，`--require-mcp` 要求 MCP 真调用与 REST 回读一致。
验证脚本不构造故障、不修改 ACL，不自动执行模型建议。

## 可选大 Key 审计

输入 Schema 和限制见设计稿：只允许固定 `redis-lab-01.json`，不接受 Key/值或其他额外字段。
将部署者已脱敏的文件放在专用目录，通过本地 Compose override 只读挂载到 `/app/audits`，
并设置 `DBIC_REDIS_AUDIT_DIR=/app/audits`。目录与文件需对容器 UID 10001 可读，
不得用符号链接；挂载整个目录以支持部署者原子替换文件。不要挂载凭据目录。
审计最多 64 KiB、结束时间距检查最多 15 分钟、时间跨度最多一小时。
不由 DBIC 生成真实扫描结果；测试中的合成文件不代表运行实例的真实大 Key 状态。
配置后运行 `python3 scripts/verify_m4.py --require-audit --require-mcp`。

## 隔离套件和静态检查

```bash
python3 scripts/init_redis_lab.py --test
docker compose --env-file .env -f deploy/compose.yaml --profile verify \
  run --build --rm dbic-tests
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv lock --check
uv run python -m compileall -q src scripts
```

test-redis 与 test-mysql/test-postgres 仅在内部 verification 网络，测试管理员仅传给测试容器。
Redis 低/高占用 fixture 只写随机测试前缀、120 秒 TTL，并 finally 删除自己的 Key；
权限负测使用 ACL DRYRUN，不执行 FLUSHALL/KEYS 等危险命令。临时撤销慢日志权限后恢复。
测试慢日志阈值由测试容器启动配置设为 0，不用耗时命令造慢请求。
重复执行迁移，并通过新应用实例回读 PostgreSQL 证据、报告、反馈与审计。

## 实际结果（2026-10-31）

| 检查 | 结果 |
|---|---|
| 本机全量，包含真实 HTTP/MCP 与 Postman 模型模拟 | 334 passed、15 skipped，17.35 秒；当时未启用宿主数据库测试 |
| 最后源码改动后的定向 Redis 单测 | 61 passed，0.83 秒 |
| ARM64 Compose 全量隔离套件 | 349 passed、13 skipped，22.18 秒；跳过 12 项独立 Postman 模拟与 1 项 DSN 迁移入口，真实迁移已由数据库套件验证 |
| Linux AMD64 完整隔离套件（OrbStack 仿真） | 349 passed、13 skipped，30.63 秒；API、Redis、MySQL、PostgreSQL 均以 AMD64 在独立 dbic-m4-amd64 项目运行 |
| 本地 Postman 模拟 | 通用/DeepSeek 共 12 次 POST，成功、401、429、503、超时及假引用均验证；无真实模型请求 |
| 本机部署 verify_m4.py --require-mcp | PASS：memory_pressure、三条证据、三个 MCP 工具及 REST 回读；audit unavailable 明确保留 |
| 本机部署 verify_m3.py --require-mcp | PASS：原 MySQL 三工具、鉴权和归档一致性 |
| 本机部署 verify_m2.py --skip-suite | PASS：实时、历史窗口降级；不要求真实 ES/指标源 |
| ruff / format / mypy / compileall / lock / Compose config | 全部通过；mypy 51 个源文件 |

AMD64 验证同时构建并启动了目标架构镜像，不仅检查 manifest；这是本机仿真验证，不是远程服务器验收。
AMD64 首轮发现测试在收集时预计算“未来时间”会随套件运行失效，改为测试执行时计算并注入时钟后全量通过。
本次新建的 AMD64 测试服务和 ARM64 test-redis 已停止，原有 test-mysql/test-postgres 维持运行状态。
Postman 本地模拟已停止，运行中 API、MySQL lab、Redis lab 与主存储保持健康。
上述为本机验收结果；未重新验证远程 ES 实例或调用真实 DeepSeek。
GitHub CI 状态以本批次提交的 Actions 记录为准。

## 已知限制与回退

实测 Redis 8.4.8 刚启动时出现 used_memory_dataset=18446744073709547568、
used_memory=1366792、overhead=1370840 的不一致组合。适配器移除不一致组成字段，
留下 missing/inconsistent limitation，仍允许基于有效总内存/限额形成独立事实。
这不是把异常值改成零；已有复现单测与冷启动真 Redis 验证。
类似统计问题见 [Redis 官方仓库问题记录](https://github.com/redis/redis/issues/13973)，
该记录是语义参考，不将其他版本问题直接当作本机原因证明。

慢日志保留 20 条、5 秒工具总超时、64 KiB 脱敏证据上限；普通 Redis 客户端仍可能在
脱敏前解析较大的响应，不宣称网络层字节硬上限。此能力仅限受控 lab。
审计文件是部署者提供的快照，来源真实性不由 hash 证明；缺少真实审计源是当前已知缺口。
进程内队列、全局串行执行、无重启恢复与生产 HA；本批次未扩展这些能力。

关闭 Redis：在重建命令前显式 `DBIC_REDIS_ENABLED=false`，保留其他已授权配置，仅重建 API。
可停止本项目 redis-lab，不执行 down -v，不删除 PostgreSQL 数据。
关闭功能不回退 Schema，历史 Redis 报告仍能读取。
本机另外保留 `dbic-lab-dbic-api:rollback-m3-local`（既有 b8137f2 的 AMD64 M3 镜像，
不是当前运行容器的逐字节快照）；需要回退整个应用时应先核对目标平台与配置。
后续入口为本次代码审核/远程 M4.1 发布验收；其他数据库和 Redis 模型建议另立阶段。
