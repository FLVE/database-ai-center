# 网页 V1.1 验收与运行

2026-11-01，本机 **0.10.0 / 0003_web_history** 已实现、迁移、启动并验收。
远程保持已发布的 `a683dcd` / 0.9.0。本地验收期间没有 commit/push，也没有访问或修改远程服务。
用户随后明确授权将本批提交并推送 GitHub；该发布不包含远程迁移或升级。
工作区中原有的 V1 远程验收文档改动已保留。

## 使用

打开 **http://127.0.0.1:8086/**，用本机 `.env` 的 DBIC_API_TOKEN 登录，点击“任务历史”。
列表读取 PostgreSQL，不依赖本次会话记录；页面刷新后重新登录仍可查询历史。
支持实例精确匹配（可输入已移除实例 ID）、六种任务状态、创建时间起止与每页 10/20/50 条。
创建时间以本机时区输入，查询转换 UTC；起点包含、终点不包含，不是报告内的历史证据窗口。
打开报告后“返回任务历史”保留本页筛选与页码；清除、刷新或重新筛选回第一页。

新增接口 `GET /api/v1/diagnoses` 沿用 Bearer，完整参数、响应与边界见
[设计](web-v11-plan.md)。GET 的查询模型采用 FastAPI 的
[Query Parameter Models](https://fastapi.tiangolo.com/tutorial/query-param-models/)；
FastAPI OpenAPI 为接口定义来源，单元测试校验参数与安全声明。

## 实现文件

- `src/dbic/core/task_history.py`：只含摘要的响应模型、有界查询、UTC 时间及绑定筛选的游标。
- `src/dbic/core/ports.py`：TaskStorePort 新增 list_tasks，已有方法签名不变。
- `src/dbic/adapters/persistence/tasks.py`：复合排序/游标、参数化筛选，只查任务摘要，最多 limit+1 行。
- `src/dbic/api/routes/diagnoses.py`：受既有鉴权保护的 GET 列表端点，不影响原 POST 和详情接口。
- `migrations/versions/0003_web_history.py`、`adapters/persistence/postgres.py`：两个索引及就绪版本要求。
- `src/dbic/web/{index.html,styles.css,app.js,ui.js}`：历史入口、筛选、分页、报告往返及移动布局。
- `tests/unit/test_task_history.py`、`tests/integration/test_task_history_live.py`：契约、边界、实库与迁移。
- `tests/fakes.py`、`tests/web/server.py`、`tests/web/history.spec.js`：测试适配和仅测试服务器的历史样例。
- `scripts/verify_web.mjs`：扩展真实 lab 验收到筛选、报告往返、刷新重登及服务器历史回读。
- `pyproject.toml`、`uv.lock`、`main.py`、`core/report.py`、`mcp/server.py`：统一 0.10.0，无依赖升级。
- README、AGENTS、架构/实施和 V1.1 文档：范围、迁移与验收说明。

## 实际验证

```bash
uv run python -m pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy
npm run test:web
npm run test:web -- tests/web/history.spec.js
node scripts/verify_web.mjs
```

- 全部 Python：**450 passed、50 skipped，3.34 秒**。新增 26 项历史单元/API 用例通过。
  跳过的外部集成未被当作通过；新增实库用例另行在下面的专用数据库实际执行。
- Ruff、149 个 Python 文件格式检查、mypy（67 个源码文件）全部通过。
- Chromium：**12 passed，38.5 秒**。新增历史流程包含服务器读取、分页、条件筛选、
  报告往返、重登、空结果、503/401、迟到响应、390px 窄屏；随后加强退出时在途请求用例，
  历史三项重新运行 **3 passed，6.9 秒**。沿用原 9 项诊断/证据/反馈回归。
- 查看桌面/手机历史截图，无横向溢出、遮挡或虚假统计；仍未在 Safari/Firefox 或手机真机验证。
- 本机实际 lab 浏览器通过，MySQL 任务 `38345931-1056-465d-bd30-154adc8bcec9`，
  2 条真实证据及验收反馈归档；重新登录后会话列表为空，仍能从服务器历史找回任务。
  浏览器没有脚本错误，退出后清空历史 DOM；截图 `dist/web/history-live.png`。

独立 PostgreSQL 验证使用已有 verification 网络中的 `test-postgres`，数据库必须是 `dbic_test`。
不初始化其他诊断数据库，不运行故障注入，不重建测试数据库容器。实际命令：

```bash
docker compose --env-file .env -f deploy/compose.yaml --profile verify build dbic-tests
docker compose --env-file .env -f deploy/compose.yaml --profile verify \
  run --rm --no-deps --pull never dbic-tests python -c '
import os, pytest
from dbic.settings import Settings
s = Settings()
assert s.env == "lab"
os.environ["DBIC_TEST_DATABASE_URL"] = s.postgres_url().set(database="dbic_test").render_as_string(hide_password=False)
raise SystemExit(pytest.main(["-q", "-p", "no:cacheprovider", "tests/integration/test_task_history_live.py", "tests/integration/test_migration.py"]))
'
```

结果 **2 passed，4.97 秒**：24 个独立 fixture 任务、同时间 UUID 排序、完整分页无重复、
新增任务后后续页保持、组合状态/实例/半开时间筛选、未知实例与注入字符串为空、索引存在、
重复升级、降级再升级、降级不删除任务且旧 schema 不通过新 readyz。
fixture 使用唯一实例与任务 ID，结束后清除本测试记录；最终 schema 恢复 head。
上述只验证当前 PostgreSQL 单机条件，不代表大数据量性能或并发状态更新快照保证。

## 本机部署与回退

本机所有 M4 lab 已运行，沿用完整配置，只更新元数据库索引与 API：

```bash
dc_v11() {
  DBIC_MCP_ENABLED=true docker compose --env-file .env \
    --env-file dist/redis-lab/redis.env --env-file dist/mongodb-lab/mongodb.env \
    --env-file dist/postgresql-lab/postgresql.env --env-file dist/doris-lab/doris.env \
    -f deploy/compose.yaml -f deploy/compose.mongodb.yaml \
    -f deploy/compose.postgresql.yaml -f deploy/compose.doris.yaml "$@"
}
dc_v11 build dbic-api dbic-migrate
dc_v11 run --rm --no-deps --pull never dbic-migrate
dc_v11 up -d --no-deps --no-build --pull never --wait --wait-timeout 120 dbic-api
curl --noproxy '*' --fail http://127.0.0.1:8086/healthz
curl --noproxy '*' --fail http://127.0.0.1:8086/readyz
```

已实际执行：切换前 queued/running 为 0；迁移完成、healthz=ok、readyz=ready。
API 环境变量指纹和 loopback 端口绑定与升级前相同，其他全部运行容器 ID 相同。
没有修改私有配置、ES/Kibana 或远程服务器。升级前 API 镜像保留为
`dbic-lab-dbic-api:rollback-web-v1-a683dcd`。

旧 0.9.0 的 readyz 严格要求 0002_m1，因此不能仅换旧镜像。需要回退时先确认无执行中任务，
使用上面的 dc_v11 函数停止 API，再使用当前 0.10.0 migrate 镜像移除两个新增索引：

```bash
dc_v11 stop dbic-api
dc_v11 run --rm --no-deps --pull never dbic-migrate alembic downgrade 0002_m1
docker image tag dbic-lab-dbic-api:rollback-web-v1-a683dcd dbic-lab-dbic-api:latest
dc_v11 up -d --no-deps --no-build --pull never --force-recreate --wait --wait-timeout 120 dbic-api
```

索引降级已在 dbic_test 实测；运行中的本机 API 没有实际回退。
远程未来升级必须额外保留 ES/M3 私有 override，不能直接套用本机参数。

## 限制与下一步

- 沿用单用户 Token；历史可读权限与既有详情相同，包括 MCP 和已禁用实例旧任务。
- 游标分页不返回总数、不支持跳到任意页；上一页游标栈仅在本页内存中保存。
  状态变更/并发提交可能改变筛选集合，不宣称跨请求快照；刷新从最新第一页开始。
- 仅任务创建时间筛选，不搜索报告正文或反馈；不提供批量删除、导出或生产访问。
- 本次本机更新已完成，GitHub 提交/推送已获用户授权；远程迁移/升级待另行授权。无新增运行依赖。
