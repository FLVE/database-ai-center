# M4 完整验证与发布

范围为 Redis、MongoDB 8.0.34、PostgreSQL 17.11、Doris 3.0.8 的 lab 只读诊断。
应用 0.8.0，迁移仍是 0002_m1。新增依赖只有 M4.2 的 PyMongo/dnspython；PG 与 Doris
复用 psycopg、PyMySQL、httpx。不包含网页界面、自动修复、任意 SQL API 或生产接入。
先通过全部本地验证，再统一 GitHub 发布，最后远程升级；未完成的门槛不得标记通过。

## 实现入口

- `src/dbic/core/{mongodb,postgresql,doris}_models.py` 与 `_rules.py`：严格证据、归属、时效和事实规则。
- `src/dbic/plugins/{mongodb,postgresql,doris}/`：固定版本/主机/只读命令，限时、限量及脱敏。
- `core/policy.py`、`planner.py`、`runner.py`、`adapters/{registry,diagnostic_tools}.py`、
  `settings.py`、`main.py`、`api/routes/diagnoses.py`、`mcp/server.py`：分派及 REST/MCP；无新 Port 方法。
- `deploy/compose.{mongodb,postgresql,doris}.yaml` 与对应子目录：独立 lab 和 verification 服务。
- `scripts/init_{mongodb,postgresql,doris}_lab.py`：随机私有凭据与显式初始化。
- `scripts/verify_m42.py`、`verify_m4_remaining.py`：只读部署验收。
- `tests/unit/test_{mongodb,postgresql,doris}*.py`、`test_remaining_m4_api.py` 与对应 live 测试：
  边界/脱敏、真实 fixture、归档、反馈、权限和 MCP。

设计细节见 [MongoDB](m4-mongodb-plan.md)、[PostgreSQL](m4-postgresql-plan.md)、[Doris](m4-doris-plan.md)。

## 准备与隔离测试

保留已有 `.env`；初始化脚本重复执行不会覆盖凭据。所有私有文件位于 Git/Docker 忽略的 dist。

```bash
uv sync --locked --python 3.12
python3 scripts/init_redis_lab.py --test
python3 scripts/init_mongodb_lab.py
python3 scripts/init_mongodb_lab.py --test
python3 scripts/init_postgresql_lab.py
python3 scripts/init_postgresql_lab.py --test
python3 scripts/init_doris_lab.py
python3 scripts/init_doris_lab.py --test
dc_test() {
  docker compose --env-file .env -f deploy/compose.yaml \
    -f deploy/compose.mongodb.yaml -f deploy/compose.postgresql.yaml \
    -f deploy/compose.doris.yaml --profile verify "$@"
}
dc_test up -d --wait test-mongodb
dc_test exec -T test-mongodb mongosh --quiet /opt/dbic/replica.js
dc_test run --build --rm dbic-tests
```

测试自动引导隔离 Doris；其账号/BE 注册只发生在 verification 网络。PG fixture 使用独立
test-postgresql，不在 DBIC 的主存储上制造锁或业务表。Mongo/PG 的随机测试对象均 finally 清理。
Doris fixture 仅生成有界合成 SELECT Profile，不创建业务数据或执行维护操作。

AMD64 使用独立项目，先停止本轮启动的 ARM 测试服务以释放内存（不要停止其他项目）：

```bash
dc_amd64() {
  docker compose -p dbic-m4-amd64 --env-file .env -f deploy/compose.yaml \
    -f deploy/compose.mongodb.yaml -f deploy/compose.postgresql.yaml \
    -f deploy/compose.doris.yaml -f deploy/compose.verify.amd64.yaml --profile verify "$@"
}
dc_amd64 up -d --wait test-mongodb test-postgresql test-postgres test-mysql test-redis
dc_amd64 exec -T test-mongodb mongosh --quiet /opt/dbic/replica.js
dc_amd64 run --build --rm --no-deps -e DBIC_DORIS_TESTS=0 dbic-tests
```

验证实际容器平台，不能仅检查多架构 index 默认平台。AMD64 为 OrbStack 仿真测试；真实远程
Linux AMD64 的部署验收另行记录。Doris BE 在 OrbStack AMD64 仿真中退出 139，故上述仿真命令
明确排除 Doris；其原生 AMD64 验证使用独立项目，不能将跳过计为通过：

```bash
# 在原生 Linux AMD64 主机的独立测试目录执行，不覆盖运行中的项目配置。
python3 scripts/init_doris_lab.py --native-verify
docker build --target verification -f deploy/Dockerfile -t dbic-m4-tests:amd64 .
docker compose --env-file dist/doris-native-verify/doris.env \
  -f deploy/compose.doris.native-verify.yaml run --rm dbic-tests
docker compose --env-file dist/doris-native-verify/doris.env \
  -f deploy/compose.doris.native-verify.yaml stop test-doris-fe test-doris-be test-postgres
```

该测试使用独立随机凭据、网络和元数据库，不发布宿主端口，不替换现有 API。
默认套件跳过独立模型模拟与宿主 DSN 迁移入口，数据库套件
自身会执行真实 PostgreSQL 迁移和持久化测试。

## 本机运行服务

先构建 API，再启动新增 lab，沿用已有 Redis/MySQL/DBIC PostgreSQL。

```bash
dc_lab() {
  DBIC_MCP_ENABLED=true docker compose --env-file .env \
    --env-file dist/redis-lab/redis.env --env-file dist/mongodb-lab/mongodb.env \
    --env-file dist/postgresql-lab/postgresql.env --env-file dist/doris-lab/doris.env \
    -f deploy/compose.yaml -f deploy/compose.mongodb.yaml \
    -f deploy/compose.postgresql.yaml -f deploy/compose.doris.yaml \
    --profile mongodb --profile postgresql --profile doris "$@"
}
dc_lab build dbic-api
dc_lab up -d --wait mongodb-lab postgresql-lab doris-fe doris-be
dc_lab exec -T mongodb-lab mongosh --quiet /opt/dbic/replica.js
dc_lab run --rm --no-deps doris-init
dc_lab up -d --no-deps --no-build --wait dbic-api
python3 scripts/verify_m4.py --require-mcp
python3 scripts/verify_m42.py --require-mcp
python3 scripts/verify_m4_remaining.py --database postgresql --require-mcp
python3 scripts/verify_m4_remaining.py --database doris --require-mcp
python3 scripts/verify_m3.py --require-mcp
python3 scripts/verify_m2.py --skip-suite
```

PG 新问题 maintenance_health，Doris 新问题 cluster_health，均拒绝 window/额外执行参数，
禁用实例返回 403。M4 新数据库使用 NullAgent，通常 partial；脚本仍严格要求真实数据源成功，
不会把 forbidden/error/unavailable 当成通过。Doris lab 必须有一个在线 BE。

## 已知限制

- Redis 没有真实外部大 Key 审计输入时明确 unavailable；合成审计测试不代表已接入真实扫描源。
- Mongo 单成员复制集不提供 HA；当前操作采样不覆盖历史或所有操作。
- PG dead tuples 是统计估算，不是物理膨胀证明；不执行 VACUUM、ANALYZE、取消/终止会话。
- Doris 单 FE/BE、有限保留且只输出 diag 自身 Profile，不代表业务慢查询覆盖；3.0.8 v2
  Profile 列表实测有空指针错误，固定使用 v1，不提升诊断账号到管理员。
- Doris 容器受内存限制；小型 lab 使用官方 SKIP_CHECK_ULIMIT，宿主 kernel/swap 不改动。
  BE 关闭不在本批范围内的 Java UDF/外部连接器 JNI 支持。该配置不能用于生产或大容量压力测试。
  升级前必须检查远程剩余内存。Mac 用原生 ARM64；Doris AMD64 仿真目前不受支持，原生 AMD64 已实测。
- API 仅绑定 127.0.0.1:8086，新增数据库不暴露宿主端口；全部私有配置不能推送 GitHub。

## 发布与回滚

远程必须叠加原有 compose.es.local.yaml 和 compose.m3.local.yaml；保留原 .env 和 Redis 文件。
先保留当前 API 镜像与 commit，拉取已验证源码、加载对应 AMD64 镜像，生成新 lab 凭据，初始化
Mongo/PG/Doris，再仅重建 API。不得执行 compose down -v、修改 ES/Kibana、监控服务或生产数据库。
失败时仅将 API 恢复为原镜像和原配置；新 lab 可单独停止，保留数据卷用于排查。

## 实际结果

2026-10-31 已完成：

- 最终完整 ARM64 隔离套件：457 passed、13 skipped，58.02 秒。
  实测修复 Doris 跨秒耗时 `3sec342ms` 的解析边界，并重新通过实库及完整套件。
- AMD64 仿真非 Doris 套件：451 passed、15 skipped，97.99 秒；额外 2 skipped 明确为 Doris。
- 目标服务器独立原生 AMD64 Doris：29 passed，16.66 秒，含 2 项真实集成和 27 项边界单测。
  FE 约 610 MiB、BE 524 MiB、独立 PG 24 MiB；验证后停止这三个测试容器释放资源。
- 宿主 HTTP/模型模拟套件：447 passed、23 skipped，21.90 秒；跳过宿主未启用的实库测试。
  两次模型回归共记录 24 次 HTTP 请求，成功/401/429/503/超时/伪造引用均实际执行。
- 本机 verify_m4、verify_m42、verify_m4_remaining（PG/Doris）、verify_m3、verify_m2 均通过。
- Ruff、格式检查、mypy（65 个源码文件）、compileall、锁文件和公开候选文件凭据扫描通过。
- 统一应用/OpenAPI/MCP/报告版本为 0.8.0 后，全部单元测试 421 passed，6.38 秒。

服务器独立验证用传输哈希校验的 wheel 包，新增依赖 SHA256 与 uv.lock 相符，已安装依赖
逐一比对锁文件。最终源码和 0.8.0 元数据的原生 AMD64 再次 29 passed（7.73 秒）。
上述独立验证完成后，已执行以下正式发布。

## 正式发布结果

2026-10-31，代码提交 `2cfeb00819722e0576dfc9f2e470124ea95dac7e` 已推送 GitHub main。
[GitHub Actions](https://github.com/FLVE/database-ai-center/actions/runs/41193671012) 成功：
436 passed、34 skipped；Ruff/格式/mypy/compileall 均通过。CI 跳过外部模型和多数实库用例，
不能替代上面已经执行的本机隔离与原生 AMD64 验证。

远程 `root@106.52.59.184:/root/database-ai-center` 已升级为 0.8.0：

- GitHub 直连拉取超时后，通过经 Git 校验的同一提交 bundle 快进更新；没有重置或覆盖工作区。
- 使用 `dbic-remote-runtime:2cfeb00-amd64`，源码指纹与提交一致，全部已安装依赖版本与 uv.lock 一致。
  仅新增的锁定 wheels 和应用 wheel 通过校验传输，复用已验证基线镜像，离线构建。
- 新 MongoDB、独立 PostgreSQL、Doris FE/BE 均 healthy，API 为 `a5b68f3ba164`，
  只发布 `127.0.0.1:8086`。healthz/readyz 均 200；受保护 OpenAPI 版本为 0.8.0，五类数据库启用。
- verify_m4、verify_m42、verify_m4_remaining（PG/Doris）、verify_m3 全部通过。
- `verify_m2.py --skip-suite --require-search --start 2026-10-31T00:00:00Z --end 2026-10-31T00:30:00Z`
  通过，保留真实 ES 事件证据；metrics=null 的原有降级保持明确。
- `verify_agent.py` 通过，lock_wait/slow_query 均 agent_status=ok，各返回 2 条建议，未自动执行。
- 原 .env、ES/M3 本地 override、Redis 凭据文件的 SHA256 完全未变。
  ES、Kibana、六个监控容器、原 DBIC PostgreSQL/MySQL 的容器 ID 和启动时间均未变。
- 独立远程测试容器已停止；运行 lab 验证后主机约 0.9 GiB 可用内存，未做压力/生产容量验证。

远程日常操作必须保留完整参数，不能用仅 base 的重建命令关闭新增能力：

```bash
cd /root/database-ai-center
dc_m4() {
  docker compose --env-file .env \
    --env-file dist/redis-lab/redis.env --env-file dist/mongodb-lab/mongodb.env \
    --env-file dist/postgresql-lab/postgresql.env --env-file dist/doris-lab/doris.env \
    -f deploy/compose.yaml -f deploy/compose.es.local.yaml -f deploy/compose.m3.local.yaml \
    -f deploy/compose.mongodb.yaml -f deploy/compose.postgresql.yaml -f deploy/compose.doris.yaml \
    --profile mongodb --profile postgresql --profile doris "$@"
}
dc_m4 ps
python3 scripts/verify_m4_remaining.py --database postgresql --require-mcp
python3 scripts/verify_m4_remaining.py --database doris --require-mcp
```

已保留回滚镜像 `dbic-lab-dbic-api:rollback-m41-8ce7178`；未执行回滚。若需恢复原 M4.1：

```bash
docker tag dbic-lab-dbic-api:rollback-m41-8ce7178 dbic-lab-dbic-api
docker compose --env-file .env --env-file dist/redis-lab/redis.env \
  -f deploy/compose.yaml -f deploy/compose.es.local.yaml -f deploy/compose.m3.local.yaml \
  up -d --no-deps --no-build --pull never --force-recreate --wait dbic-api
```

无 schema 变更，不回滚或删除数据库内容。下一阶段进入网页界面设计，仍不授权生产接入或自动修复。
