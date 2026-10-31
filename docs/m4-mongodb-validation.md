# M4.2 MongoDB 配置与验收

本批仅交付 MongoDB 8.0 单成员复制集 lab，设计见 [m4-mongodb-plan.md](m4-mongodb-plan.md)。
本批验收时应用 0.6.0，沿用 0002_m1，无新迁移。后续 PostgreSQL/Doris 与统一发布的
当前状态见 [M4 完整验证](m4-final-validation.md)，此处保留 M4.2 独立验收结果。

## 文件与行为

| 文件 | 用途 |
|---|---|
| `src/dbic/core/mongodb_models.py`、`mongodb_rules.py` | 严格证据模型、时效/归属校验、有引用的确定性事实 |
| `src/dbic/plugins/mongodb/` | 固定只读聚合/复制集查询、版本/拓扑检查、投影与错误脱敏 |
| `core/policy.py`、`planner.py`、`runner.py`、`adapters/diagnostic_tools.py` | 类型白名单、两个固定步骤、规则分派；无新增 Port 方法 |
| `adapters/registry.py`、`settings.py`、`main.py`、REST/MCP | 默认禁用开关、activity_health、两个 MongoDB MCP 工具 |
| `deploy/compose.mongodb.yaml`、`deploy/mongodb/` | 独立 lab 与测试实例、初始化最小角色、受控复制集初始化 |
| `scripts/init_mongodb_lab.py`、`verify_m42.py` | 私有凭据生成、只读验收；M3 验收脚本兼容新增工具列表 |
| `tests/unit/test_mongodb*.py`、`test_verify_m42.py`、`tests/integration/test_mongodb_live.py` | 纯规则、安全边界、真实 MongoDB/PostgreSQL/MCP 回归 |

PyMongo 4.19.0 和其依赖 dnspython 2.9.1 已进入 uv.lock，未升级其他库。
MongoDB 镜像为 `mongo:8.0.34@sha256:b9924c330a8bbc5800fe9c69f04a84e221eca7b28fd049a0354147d9908fa142`，
ARM64 和 AMD64 均实测启动；客户端使用异步 API、固定地址和 directConnection，不跟随成员发现到任意主机。

## 启动已有本地 lab

以下在仓库根目录执行，保留已有 `.env`。两次凭据生成分别为运行 lab 和测试环境，
重复执行不覆盖已有文件；目录 0700、文件 0600。keyfile 的容器内副本由 mongod 用户读取。
不要把这些私有文件提交或打入源码发布包。
`dist/` 已同时排除于 Git、源码发布包和 Docker 构建上下文。

```bash
uv sync --locked --python 3.12
python3 scripts/init_mongodb_lab.py
python3 scripts/init_mongodb_lab.py --test
docker compose --env-file .env -f deploy/compose.yaml -f deploy/compose.mongodb.yaml \
  --profile mongodb up -d --wait mongodb-lab
docker compose --env-file .env -f deploy/compose.yaml -f deploy/compose.mongodb.yaml \
  exec -T mongodb-lab mongosh --quiet /opt/dbic/replica.js
DBIC_MCP_ENABLED=true docker compose --env-file .env \
  --env-file dist/redis-lab/redis.env --env-file dist/mongodb-lab/mongodb.env \
  -f deploy/compose.yaml -f deploy/compose.mongodb.yaml \
  up -d --build --no-deps --wait dbic-api
python3 scripts/verify_m42.py --require-mcp
python3 scripts/verify_m4.py --require-mcp
python3 scripts/verify_m3.py --require-mcp
python3 scripts/verify_m2.py --skip-suite
```

上述保留已经启用的 Redis。未启用 Redis 时省略对应 env 文件和 M4.1 验收命令。
MongoDB 容器 healthcheck 只确认进程响应；必须执行 replica.js，脚本认证后确认主节点就绪。
脚本只允许固定单成员配置，已有不同配置报错，不执行 reconfig；重复运行保留配置。
初始化账号只用于新建 lab 引导，API 只获得 diag 密码，没有集合读写或管理权限。
MongoDB 不发布宿主端口；API 仍仅 127.0.0.1:8086。
远程升级时须叠加既有 `compose.es.local.yaml`、`compose.m3.local.yaml`，保留 ES/DeepSeek 配置；
本批没有执行远程升级，不可直接用本机简化命令覆盖远程配置。

## API 与只读验收

使用已有 Bearer Token 创建诊断：

```json
{"instance_id":"mongodb-lab-01","problem_type":"activity_health"}
```

固定返回两个工具步骤，证据分别是 mongodb.current_ops_summary、mongodb.replica_health。
window、命令/路径/host 额外字段和跨数据库问题返回 422；实例禁用返回 403。
缺字段保留缺失，最多 50 个操作样本，不返回命令、命名空间、客户端地址或业务内容。
运行至少一秒只是样本描述，不能当作慢查询根因；getMore 等待也不自动成为故障。
单成员健康不代表高可用；当前读取成功但状态不是 PRIMARY 会记录对应事实，不伪造正常。
有限采样与单成员限制使任务通常 partial；MongoDB agent_status=disabled 是设计行为。
verify_m42 要求真实源成功和 lab 主节点可达，失败来源不会因为 partial 而被当作通过。

## 隔离测试

只启动 verification 网络中的测试数据库，不触碰正在使用的 mongodb-lab。
test-mongodb 与运行 lab 的凭据/数据独立；fixture 管理员不会传给 API。

```bash
python3 scripts/init_mongodb_lab.py
python3 scripts/init_mongodb_lab.py --test
python3 scripts/init_redis_lab.py --test
docker compose --env-file .env -f deploy/compose.yaml -f deploy/compose.mongodb.yaml \
  --profile verify up -d --wait test-mongodb
docker compose --env-file .env -f deploy/compose.yaml -f deploy/compose.mongodb.yaml \
  exec -T test-mongodb mongosh --quiet /opt/dbic/replica.js
docker compose --env-file .env -f deploy/compose.yaml -f deploy/compose.mongodb.yaml \
  --profile verify run --build --rm dbic-tests
```

真实 fixture 为正常单成员状态和等待中的 getMore；后者只在随机测试 capped collection 写入
一条合成数据，等待上限 8 秒，finally 关闭自己的游标并删除自己的集合，不使用 killOp 或压力循环。
额外测试撤销 inprog 后恢复、业务集合读取拒绝、自定义角色仅含两个观察动作、
REST 证据/反馈/新应用回读、MCP 归档和真实 PostgreSQL 审计。变更用户权限只在 test-mongodb 内执行。

复现 AMD64 套件：在以上凭据准备后，使用单独 Compose 项目并追加平台 override。
当前平台 override 已覆盖完整 M4，须先按 [完整准备步骤](m4-final-validation.md) 生成 PG/Doris 测试凭据。
下面的函数只缩短重复参数，所有测试数据库均指定 linux/amd64；不要仅检查多架构镜像的默认架构。

```bash
dc_amd64() {
  docker compose -p dbic-m42-amd64 --env-file .env \
    -f deploy/compose.yaml -f deploy/compose.mongodb.yaml \
    -f deploy/compose.postgresql.yaml -f deploy/compose.doris.yaml \
    -f deploy/compose.verify.amd64.yaml --profile verify "$@"
}
dc_amd64 up -d --wait test-mongodb test-postgresql test-postgres test-mysql test-redis
dc_amd64 exec -T test-mongodb mongosh --quiet /opt/dbic/replica.js
dc_amd64 run --build --rm --no-deps -e DBIC_DORIS_TESTS=0 dbic-tests
dc_amd64 stop test-mongodb test-mysql test-postgres test-redis test-postgresql
```

## 实际验证

2026-10-31：

- 最终源码原生 ARM64 完整 Compose 套件：400 passed、13 skipped，34.91 秒。
- 最终源码 Linux AMD64 完整 Compose 套件（OrbStack 仿真）：400 passed、13 skipped，44.09 秒。
  两种架构均使用真实 MongoDB、Redis、MySQL、PostgreSQL；跳过 12 项独立 Postman 模拟与
  1 项 DSN 迁移入口，实际迁移由数据库集成测试验证。
- 宿主完整 HTTP/模型模拟套件：394 passed、18 skipped，18.44 秒；未启用宿主数据库 fixture。
  模型模拟日志记录 12 次 POST，通用/DeepSeek 成功、401、429、503、超时及伪造引用均通过。
- 部署脚本 verify_m42、verify_m4、verify_m3、verify_m2 --skip-suite 均通过。
  本地历史后端为 null，此项不代表本批重新验证远程 ES；本批也没有调用真实 DeepSeek。
- ruff、format、mypy（56 个源文件）、compileall、uv lock、Compose config 与 git diff 检查通过。

最终补充规则只接受 quality=observed 的证据，不能把推断质量的数据提升为观察事实；
MongoDB 规则/接口/验收脚本定向测试 48 passed，0.85 秒，随后双架构全量按上述结果重新通过。
本次创建的 AMD64 四个测试数据库及本机 test-mongodb/test-redis 在验收结束后停止；
原有本机 test-mysql/test-postgres 维持运行。Postman 模拟服务停止，正常 lab 和 API 保持运行。

## 限制与回退

仅固定版本线和单成员复制集，不支持分片、任意目标、多节点复制延迟、profiler 或任意 explain。
当前操作只采样 50 条，字段缺失、采样截断和单成员覆盖限制显式保留；不宣称完整监控或生产可靠性。
启动配置的 WiredTiger cache 为 0.25 GiB，不是容器总内存硬上限。
普通客户端可能先解码响应再脱敏，不宣称网络字节硬上限；只用于受控 lab。

关闭能力时在原重建命令前设置 `DBIC_MONGODB_ENABLED=false`，保留 Redis/ES/模型等已有配置，
只重建 API；可以单独停止 mongodb-lab，保留数据卷和私有凭据，不执行 down -v。
本地保留 `dbic-lab-dbic-api:rollback-m41-local`；回退旧应用时把该镜像重新标记为 API 镜像，
使用 `--no-build --pull never --no-deps` 重建 API，避免重建覆盖回退版本。
无数据库迁移变化，已有任务、证据和反馈继续保留。
下一批为 M4.3 PostgreSQL 诊断设计，使用独立目标 lab，不把 DBIC 主存储当作故障实验库。
