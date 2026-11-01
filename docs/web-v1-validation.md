# 网页界面 V1 验收与运行

2026-10-31：本地实现及启动完成，应用/OpenAPI/MCP/新报告版本为 **0.9.0**。
M4 工具、任务与证据契约保持兼容，迁移仍为 0002_m1。
以下为本地验收记录；用户随后已授权将网页 V1 与改版统一提交/推送 GitHub。
远程已于 2026-11-01 升级并独立验收，结果见本文“远程发布与验收”；本地与远程证据分别记录。

## 入口与使用

打开 http://127.0.0.1:8086/ ，输入已有根目录 `.env` 中的 `DBIC_API_TOKEN`。
该令牌不是数据库密码，也不是 DeepSeek API Key。无需 Node 服务或单独前端端口。
Token 只存当前页面内存；刷新、退出或 401 后需重新输入。

1. 选择 lab 实例，在概览点击调查问题或“新建诊断”；MySQL 可选不超过 24 小时的历史窗口。
2. 点击“开始诊断”，页面每 2 秒读取任务；终态停止轮询，等待超过 2 分钟暂停并提示手动刷新。
3. 在“调查报告”查看结论和缺失项，点击证据编号打开右侧抽屉；“检查与证据”查看执行记录和全部证据。
4. 终态任务在“人工反馈”提交准确性、确认根因与备注；反馈独立归档，不改写原始报告。
5. 保存含 `#task/UUID` 的地址，或在“打开已有任务”输入 UUID，登录后重新读取。

“本次会话”最多保存本页内存中的 20 个任务，不是服务器历史列表。
enabled 仅表示通过策略，不表示数据库在线。partial/unavailable 不被渲染为健康。
AI 建议仅显示，没有执行按钮。所有动态报告、反馈和证据内容按纯文本展示。

## 实现文件

- `src/dbic/web/{index.html,styles.css,app.js,ui.js}`：中文同源网页、请求/轮询、报告与证据渲染。
- `src/dbic/api/routes/instances.py`：受 Bearer 保护的实例摘要，复用 Registry/Policy/Playbook。
- `src/dbic/main.py`：页面与固定静态资源路由、响应安全及禁止缓存头、新实例路由。
- `pyproject.toml`、`uv.lock`、`core/report.py`、`mcp/server.py`：统一 0.9.0；Python 依赖版本不变。
- `package.json`、`package-lock.json`、`playwright.config.js`：固定 Playwright 1.58.2，仅开发测试。
- `tests/unit/test_web.py`、`tests/web/`：API 契约与浏览器回归；测试服务只使用内存存储/工具。
- `scripts/verify_web.mjs`：真实本机 lab 浏览器验收，不记录 Token/浏览器 trace。
- `.github/workflows/checks.yaml`：补 Node 22、锁定安装及 Chromium 浏览器测试。
- `.gitignore`、`.dockerignore`：排除 Node 依赖、截图、trace 和测试输出。
- `docs/{architecture,implementation,web-v1-plan}.md`、`README.md`、`AGENTS.md`：契约、范围与入口。

## 本机更新

本机 M4 lab 已运行，以下命令保留当前五类实例配置，仅重建 API。
首次搭建仍按 [M4 完整验证](m4-final-validation.md) 初始化 lab，不能用此命令替代初始化。

```bash
dc_web() {
  DBIC_MCP_ENABLED=true docker compose --env-file .env \
    --env-file dist/redis-lab/redis.env --env-file dist/mongodb-lab/mongodb.env \
    --env-file dist/postgresql-lab/postgresql.env --env-file dist/doris-lab/doris.env \
    -f deploy/compose.yaml -f deploy/compose.mongodb.yaml \
    -f deploy/compose.postgresql.yaml -f deploy/compose.doris.yaml "$@"
}
dc_web build dbic-api
dc_web up -d --no-deps --no-build --pull never --wait --wait-timeout 120 dbic-api
curl --noproxy '*' --fail http://127.0.0.1:8086/healthz
curl --noproxy '*' --fail http://127.0.0.1:8086/readyz
```

服务仍仅发布 `127.0.0.1:8086`。本机有额外 ES/模型私有 override 时必须继续叠加原配置。
远程必须使用本文远程发布部分的完整参数，不能直接套用上述本机参数覆盖服务器的 ES/M3 override。

## 首次 V1 已执行验证

```bash
uv sync --locked --python 3.12
uv run python -m pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python -m compileall -q src
uv build --wheel

npm ci --ignore-scripts
npx playwright install chromium
npm run test:web
node scripts/verify_web.mjs

python3 scripts/verify_m3.py --require-mcp
python3 scripts/verify_m2.py --skip-suite
python3 scripts/verify_m4.py --require-mcp
python3 scripts/verify_m42.py --require-mcp
python3 scripts/verify_m4_remaining.py --database postgresql --require-mcp
python3 scripts/verify_m4_remaining.py --database doris --require-mcp
```

实际结果：

- Python：**424 passed、49 skipped**（3.08 秒）。新增 3 项网页/实例 API 单测。
  49 项跳过属于未配置的实库、HTTP/模型集成入口；不能作为这些外部系统通过的证据。
- Ruff、格式、mypy（66 个源码文件）、compileall 通过；wheel 包含四个网页资源。
- Chromium：**7 passed**（29.5 秒）。真实 FastAPI/Engine + 内存工具/存储验证登录、五类显示、
  禁用实例、创建/轮询、历史窗口/partial、证据对话框、纯文本 XSS 防护、反馈回读、任务链接、
  401 清理、429、断网/HTML 错误、切换竞态、failed/404、390px 移动端与焦点恢复。
  失败响应/迟到响应在浏览器网络层注入，未将其当作真实数据库故障验证。
- 真实 lab 浏览器：MySQL 任务 `8e9d3279-45bd-40e5-a4e3-54df9eddb5d0`，两条实际证据，
  反馈经独立 API 回读确认已写入 PostgreSQL；浏览器脚本错误为 0，退出清空报告。
  该反馈明确标记为网页验收，不是人工确认根因。截图保存在 Git 忽略的 `dist/web/`。
- 五类数据库 REST/MCP 部署验证全部通过；M2 Null 历史后端降级保持明确。
  本机没有启用 ES/真实模型，此次没有重跑远程 ES/DeepSeek。
- 本机 API healthy，运行配置全部环境变量排序后的 SHA256 与升级前完全一致。
  数据库 lab、元数据库和测试库未重建；没有操作 ES/Kibana 或远程服务器。

测试准备时发现本机代理将回环健康探测送到代理，现已在 Playwright 配置中显式加入
localhost/127.0.0.1 的 NO_PROXY。运行 pytest 必须使用上面 `python -m pytest` 入口；
裸 `uv run pytest` 在当前环境缺少仓库模块导入路径，会在收集阶段失败。

## GitHub 设计研究后的改版复验

同日用户要求研究其他 GitHub 项目后重新设计，依据与取舍见
[设计研究](web-design-research.md)。改动仅涉及四个网页资源、浏览器测试、真实验收脚本和文档；
没有新依赖、API 契约变更或数据库迁移，版本仍为尚未发布的 0.9.0。

布局改为实例概览、新建对话框、三个报告标签页和右侧证据抽屉。
缺失项常显，时间线/工具列表可折叠；读取已有任务时同步实例上下文。
创建请求未返回时关闭对话框并切换实例，迟到的任务会进入本次会话，不覆盖当前查看内容。

本次实际运行：

```bash
uv run python -m pytest tests/unit/test_web.py -q
uv run python -m pytest -q
npm run test:web
npm exec --yes --package=prettier@3.6.2 -- prettier --check \
  src/dbic/web/index.html src/dbic/web/styles.css src/dbic/web/app.js src/dbic/web/ui.js \
  tests/web/workspace.spec.js scripts/verify_web.mjs
node scripts/verify_web.mjs
git diff --check
```

- 网页 API 单测 **3 passed**；完整 Python **424 passed、49 skipped，2.96 秒**。
  外部集成跳过项仍不能视为外部系统验收通过。
- Chromium **9 passed，34.5 秒**：原 V1 路径全部保留，新增对话框/标签键盘导航、
  实例切换、抽屉焦点恢复、轮询保留当前标签和创建结果延迟返回场景。
- Prettier 与 diff 空白检查通过。查看桌面概览、报告、新建对话框、证据抽屉及 390px 手机截图，
  未见遮挡或横向溢出；未进行 Safari/Firefox 真机验收。
- 按上述完整 Compose 参数仅重建、更新本机 `dbic-api`，`healthz=ok`、`readyz=ready`。
  更新前后环境变量指纹及端口配置一致，其他全部运行容器 ID 一致。
- 真实 MySQL lab 任务 `b78a59c0-250e-4ed6-b338-dc346206d9c4` 完成；
  2 条证据可查看，网页验收反馈经独立 API 回读确认归档；浏览器无脚本错误，退出清空报告。
  该任务不代表人工确认数据库根因。截图在 `dist/web/{overview,create,evidence,desktop,mobile}-live.png`。
- 本次未重复五类驱动 REST/MCP、远程 ES/DeepSeek 验证；这些代码和配置没有修改。

改版前镜像已保留为 `dbic-lab-dbic-api:rollback-web-v1`。需要回到原 V1 页面时：

```bash
docker image tag dbic-lab-dbic-api:rollback-web-v1 dbic-lab-dbic-api:latest
dc_web up -d --no-deps --no-build --pull never --force-recreate --wait dbic-api
```

只读确认镜像标签存在；未实际回退。上述改版本地验收完成时尚未推送或更新远程；后续发布见下文。

## 远程发布与验收

2026-11-01，经用户授权，`root@106.52.59.184:/root/database-ai-center` 已从 0.8.0 升级到
**0.9.0 / a683dcd9d0b04083bb651499e6ca5c49bed24987**。
[该提交 GitHub CI](https://github.com/FLVE/database-ai-center/actions/runs/41210446823) 全部通过，
包括 Python、静态检查和 9 项 Chromium 浏览器测试。

发布过程与保留范围：

- 服务器直连 GitHub 拉取 30 秒超时；使用同一提交 Git bundle，经 SHA256 与 `git bundle verify`
  校验后从 `178cc62` 快进到 `a683dcd`，没有重置或覆盖私有文件。
- 应用 wheel 的源码和四个静态资源逐文件与提交核对；SHA256 为
  `103679fa30e61dd12c17e8f2ce1441e5b4d188b7fadac6d079f3263d2a25b5c2`。
- 保留原运行镜像为 `dbic-lab-dbic-api:rollback-pre-web-a683dcd`，以其为基线 `--network none`
  离线构建 `dbic-remote-runtime:a683dcd-amd64`。同步已提交的源码、配置模板、迁移和锁文件，
  只重新安装应用 wheel；全部运行依赖逐项与 uv.lock 一致，无新依赖或迁移。
- 切换前元数据库中 queued/running 任务为 0；只重建 `dbic-api`。最终容器 `1ba21af8e00d` healthy，
  仍只绑定 `127.0.0.1:8086`；其他 15 个运行容器的 ID 和启动时间完全未变。
- 原 `.env`、ES/M3 override、四类 lab 凭据文件 SHA256、API 环境变量指纹及端口绑定均未变。
  没有修改 ES/Kibana、监控服务、数据库 lab 或生产数据库。升级后可用内存约 889 MiB，未做容量测试。

本次实际验证：

```bash
python3 scripts/verify_m4.py --require-mcp
python3 scripts/verify_m42.py --require-mcp
python3 scripts/verify_m4_remaining.py --database postgresql --require-mcp
python3 scripts/verify_m4_remaining.py --database doris --require-mcp
python3 scripts/verify_m3.py --require-mcp
python3 scripts/verify_m2.py --skip-suite --require-search \
  --start 2026-10-31T00:00:00Z --end 2026-10-31T00:30:00Z
python3 scripts/verify_agent.py
```

- 五类 lab REST/MCP 全部通过；Redis audit 缺失、MySQL digest 无样本等继续明确降级。
- ES 历史事件证据通过；metrics=null 保持原有降级。真实 DeepSeek 的 lock_wait / slow_query
  都为 agent_status=ok，分别 2 / 3 条建议，没有自动执行建议。
- healthz/readyz 为 200；匿名/错误令牌的实例接口和 OpenAPI 为 401；受保护 OpenAPI 为 0.9.0。
  实例接口五类均启用且仅包含显示字段；公开网页资源与提交逐字节一致，CSP/no-store 保持有效。
- 本机 Chromium 通过 SSH 隧道访问远程，复用 `scripts/verify_web.mjs` 的流程，仅调整目标地址和
  内存凭据读取为 SSH：登录、五类显示、真实 MySQL 诊断、证据、反馈回读和退出全部通过。
  任务 `23b0b5f3-52e1-48b3-93ab-3ac32faf9dd9`，两条证据；反馈明确标记网页验收，不代表确认根因。
  浏览器无脚本错误。查看桌面与 390px 手机截图，AI 建议正常显示。
- 远程浏览器验收副本在 Git 忽略的 `dist/web-remote-a683dcd/verify-browser.mjs`，
  截图在该目录的 `screenshots/`。服务器构建材料在同名 dist 目录，不包含凭据。
  本次没有重跑完整隔离实库套件或 Safari/Firefox；不能将 Chromium 视口测试视为真机测试。

远程网页通过本机 SSH 隧道访问 **http://127.0.0.1:18086/**，输入服务器 `.env` 中的
`DBIC_API_TOKEN`。本次已建立隧道；断开后在 Mac 终端重新运行：

```bash
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -L 127.0.0.1:18086:127.0.0.1:8086 cloud-remote
```

远程日常命令保持完整参数：

```bash
cd /root/database-ai-center
dc_web_remote() {
  docker compose --env-file .env \
    --env-file dist/redis-lab/redis.env --env-file dist/mongodb-lab/mongodb.env \
    --env-file dist/postgresql-lab/postgresql.env --env-file dist/doris-lab/doris.env \
    -f deploy/compose.yaml -f deploy/compose.es.local.yaml -f deploy/compose.m3.local.yaml \
    -f deploy/compose.mongodb.yaml -f deploy/compose.postgresql.yaml -f deploy/compose.doris.yaml \
    --profile mongodb --profile postgresql --profile doris "$@"
}
dc_web_remote ps dbic-api
```

需要回退到升级前 0.8.0 时，使用上面函数，仅切回 API；旧版没有网页，数据库内容保持：

```bash
docker tag dbic-lab-dbic-api:rollback-pre-web-a683dcd dbic-lab-dbic-api:latest
dc_web_remote up -d --no-deps --no-build --pull never --force-recreate --wait --wait-timeout 120 dbic-api
```

回退镜像身份已与原容器镜像比对，未实际回退。后续网页 V1.1 / M5 不在本次发布范围内。

## 限制与回退

- 沿用单用户 Bearer，尚无用户管理、SSO、服务器历史分页/检索；不开通生产或公网访问。
- 浏览器自动验收覆盖 Chromium 桌面和手机视口，未在 Safari/Firefox 真机运行。
- 创建请求响应丢失时，服务可能已经创建任务；页面不自动重发。反馈结果未知时需先刷新核实。
- 后台任务仍为原有进程内执行方式，不承诺服务重启后的可靠续跑。
- Redis 外部审计缺失、其他数据库采样覆盖限制仍如实展示，不通过 UI 消除 partial。
- GitHub CI 与原生 AMD64 远程网页已单独验证，见上面的远程发布记录；尚无压力或多用户容量验证。

旧运行容器引用的镜像对象无法再次打标签（Docker 返回 No such image）。已从已提交的
`178cc62` 源码导出到独立临时目录并构建 `dbic-lab-dbic-api:rollback-pre-web-v1`，
不是旧运行镜像的逐字节备份。只读确认该回退镜像包版本为 0.8.0；尚未实际切回服务。
无新迁移，现有 API/证据字段保持兼容。需要回退时使用上述 dc_web 函数：

```bash
docker image tag dbic-lab-dbic-api:rollback-pre-web-v1 dbic-lab-dbic-api:latest
dc_web up -d --no-deps --no-build --pull never --force-recreate --wait dbic-api
```

该命令仅切回 API 镜像，网页入口将不可用，已有数据库/任务/证据保留。
