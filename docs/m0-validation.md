# M0 验证记录（2026-10-31）

## 最终验收结果

用户在 Mac Apple Silicon + OrbStack 宿主终端执行
`python3 scripts/verify_m0.py --lock`，完整运行成功：

- 宿主 healthz、readyz 通过。
- 缺失/错误 Token 被拒绝，有效 Token 返回 M0 能力列表，OpenAPI 鉴权通过。
- dbic 的 upgrade head 连续执行两次成功。
- 专用 dbic_test 的重复迁移、八张表、任务外键元数据与 lab CHECK 约束验证通过；
  测试写入回滚，测试库保留。
- uv.lock 已生成，uv sync --locked 完成；50 passed、1 skipped、1 warning。
- Ruff 检查和 21 个文件格式检查通过，mypy 的 10 个源码文件检查通过。

据此，M0 本地验收通过。上述真实服务结果来自用户终端输出，并非当前沙箱直接访问所得。
pytest 的 1 个 skip 为未配置 DBIC_TEST_DATABASE_URL 的单独用例；脚本已另行验证真实
PostgreSQL。仍保留 Starlette/httpx 弃用警告。现有镜像未按新锁文件重建，Dockerfile
尚未消费 uv.lock；不能把本次宿主锁定环境的版本视为容器版本。
下文保留排障历史，不代表当前仍有同样的验收阻塞。

## 收尾复验

本会话再次运行：pytest 为 50 passed、1 skipped（需要真实 PostgreSQL 的用例）、
1 条 Starlette/httpx 弃用警告；Ruff、mypy、源码编译均通过。
当前沙箱仍拒绝 Docker socket，且无法访问宿主 8086；这不推翻用户此前的成功 curl 证据。
`uv lock --offline` 因缓存缺少可用 ast-serialize 解析数据失败，尚未生成锁文件。

新增 `scripts/verify_m0.py`，用 `python3 scripts/verify_m0.py --lock` 从宿主终端执行。
该脚本检查真实接口鉴权、重复迁移、专用测试库结构与 lab 约束，并可锁定依赖及复验。
脚本已通过 Ruff、py_compile 和 CLI 帮助检查；实际执行在首个 Docker 访问检查处被阻断，
非零退出。脚本后续真实服务步骤尚未实测，不能记为通过。

## 启动排障补充

用户提供的真实 OrbStack 输出确认：API/PostgreSQL/MySQL 均 healthy，迁移服务 Exited (0)，
容器内 readyz 持续返回 200。先前未验证的首次真实迁移与容器启动已有用户侧证据；
重复迁移仍未验证。宿主访问失败，API 的 PORTS 仅显示 8086/tcp，没有宿主映射。

原 Compose 将 API 仅接到 internal 网络，导致端口无法发布。
已给 API 增加普通 bridge 网络 api-host，数据库仍仅使用内部网络；API 宿主绑定仍为
127.0.0.1:8086。普通网络也允许 API 出站。依据：
[Docker/Moby 网络说明](https://github.com/moby/moby/discussions/53256)。
curl 示例增加 --noproxy，避免本机代理返回的 502 掩盖实际连接错误。
用户应用配置修复后，已从宿主机实际验证：

| 命令 | 用户提供的实际结果 |
|---|---|
| `curl --noproxy '*' --fail http://127.0.0.1:8086/healthz` | `{"status":"ok"}` |
| `curl --noproxy '*' --fail http://127.0.0.1:8086/readyz` | `{"status":"ready"}` |

宿主 HTTP 访问与 PostgreSQL 迁移版本就绪检查已通过，启动问题已解决。
这些是用户终端实测证据；本会话的 Docker socket 权限限制仍存在。
剩余验证为真实能力端点鉴权、重复迁移及专用 dbic_test 集成测试；跨平台依赖锁仍待补齐。

下面为首次交付时的验证记录。

实现已落地；完整容器验收尚未完成。原始目录只有 AGENTS 与两份基线文档，无 `.git`，
未执行 Git 初始化、提交或推送。未访问生产数据库，未修改 Elasticsearch/Kibana。

## 实际运行结果

| 命令 | 结果 |
|---|---|
| `.venv/bin/python -m pytest -q` | 50 passed, 1 skipped, 1 warning |
| `.venv/bin/ruff check .` | All checks passed |
| `.venv/bin/ruff format --check .` | 19 files already formatted |
| `.venv/bin/mypy` | Success: no issues found in 10 source files |
| `.venv/bin/python -m compileall -q src` | exit 0 |
| `alembic upgrade head --sql` | exit 0；生成八张业务表、索引、约束和版本记录 |
| `docker compose -f deploy/compose.yaml config --quiet` | 使用临时非敏感测试变量，exit 0 |

覆盖完整 6×6 状态转换矩阵、UTC/时间窗、生命周期时间戳、证据序列化、结论引用、
根因确认标记、核心依赖方向、API 鉴权、就绪/失败、错误脱敏与 request_id。
API 使用 TestClient 实际执行 ASGI 生命周期；不等同于网络或容器验收。

Python 3.12.13 ARM64；安装 FastAPI 0.141.1、Pydantic 2.14.1、SQLAlchemy 2.0.51、
Alembic 1.18.5、psycopg 3.3.6、pytest 9.1.1、Ruff 0.17.0、mypy 2.3.1。
联网 uv sync 被沙箱拒绝。通过本机已下载缓存重建临时 wheelhouse，再执行：

```bash
UV_CACHE_DIR=/private/tmp/dbic-uv-offline uv pip install --no-index \
  --find-links /private/tmp/dbic-wheelhouse -e . --group dev
```

临时缓存不是项目启动依赖。联网环境使用 README 标准安装命令；跨平台 uv.lock 尚未生成，
依赖范围解析可能与本次缓存版本不同，后续应生成锁文件并复验。

## 首次交付时的未完成验收与已知问题（后续结果见上文）

- Docker 客户端识别 darwin/arm64、orbstack context，但 socket 访问被拒绝；
  未运行 Compose up/build，没有读取或修改现有容器。
- 本机无 PostgreSQL 服务二进制且不能访问 Docker：真实升级、重复升级和数据库约束
  执行未实测，待人工在隔离 lab 跑一下。集成测试明确 skip，CI 已配置 PostgreSQL 17。
- uvicorn 工厂初始化及应用启动完成，但监听 127.0.0.1:8086 被沙箱以 EPERM 拒绝；
  随后 curl 无法连接。真实 HTTP 冒烟检查未完成，不能宣称 M0 完整验收通过。
- Starlette TestClient 对 httpx 发出弃用提示；测试正常通过，未隐藏警告。
- 访问 PyPI 的权限升级被自动审批策略拒绝（本会话禁止沙箱权限升级）。

## 后续入口

M0 收尾项已通过上述脚本验证。下一阶段为 M1 的最小权限 MySQL 工具、规则与诊断闭环，
本次不自动进入 M1。后续可将 Docker/CI 安装统一改为使用锁文件，并独立验证镜像重建。
关闭服务只用本项目 stop，保留卷；不要对现有 Elastic 栈执行清理命令。
