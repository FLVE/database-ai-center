# M3 配置与验收

范围见 [m3-plan.md](m3-plan.md)。MCP 和模型默认关闭；本地验证不等于远程部署或真实模型接入。
M2 远程 ES 样例验收已完成，详见 [m2-validation.md](m2-validation.md)。

## 升级已有 lab

保留 `.env`、数据库卷及已有网络 override。依赖使用 `uv sync --locked --python 3.12`，
官方 MCP SDK 锁定 2.3.0；无新增迁移。先构建并验证隔离测试镜像：

```bash
docker compose --env-file .env -f deploy/compose.yaml --profile verify run --build --rm dbic-tests
```

在 `.env` 添加 `DBIC_MCP_ENABLED=true`，`DBIC_AGENT_BACKEND=null`。本地升级 API：

```bash
docker compose --env-file .env -f deploy/compose.yaml up -d --build --no-deps --wait dbic-api
python3 scripts/verify_m2.py --skip-suite
python3 scripts/verify_m3.py --require-mcp
```

远程同样命令须保留原有 `-f deploy/compose.es.local.yaml` 网络配置；不要误用 host-gateway
覆盖已经验证的 ES 容器 IP 映射。仅修改配置时使用 `--no-build --force-recreate`，源码更新需重建。
不重新初始化 diag，不运行 down -v，不改已有 ES/Kibana。`verify_m3.py` 只验收 MCP 和规则路径，
不会把 Agent 配置声明当成真实模型可用。M1/M2 验收脚本继续支持 M3。

## MCP 客户端

端点 `http://127.0.0.1:8086/mcp/`，每次请求必须携带 `Authorization: Bearer <DBIC_API_TOKEN>`。
标准 Streamable HTTP；官方 SDK 新协议与 legacy handshake 均有真实 HTTP 集成测试。
客户端填写该 URL 与授权头即可。远程可先用 SSH 转发到本机另一个 loopback 端口；不开放公网端口。
本地单用户静态 Token，不提供 OAuth 自动登录；只接受 loopback Host 和 Origin。
工具输入严格只有 `{"instance_id":"mysql-lab-01"}`。返回 Evidence，包括 task_id/evidence_id，
原始 SQL/凭据不返回。工具自身 unavailable/forbidden 等状态保存在 Evidence.status，客户端必须检查。
有权限的请求创建任务后，执行失败也保留任务状态；无权限或参数无效请求不触发数据库工具。
每次调用只执行一个工具，不共享无限时长的会话预算；同一服务最多 16 个待执行任务，串行执行。

## 可选模型

只有明确配置以下值才会请求模型（由部署者在 `.env` 私下填写，不提交密钥）：

```dotenv
DBIC_AGENT_BACKEND=openai_compatible
DBIC_LLM_BASE_URL=https://YOUR_PROVIDER/v1
DBIC_LLM_MODEL=YOUR_MODEL
DBIC_LLM_API_KEY=
DBIC_LLM_TIMEOUT_SECONDS=8
```

base URL 不带 `/chat/completions`，适配器追加该路径。模型须支持 Chat Completions 的
tools/forced function/strict schema/parallel_tool_calls=false/max_completion_tokens/store=false。
不支持该子集的提供商返回错误时回退规则报告。公网必须 HTTPS，TLS 校验不关闭；本地 lab
可使用 `http://host.docker.internal:<port>/v1`。没有默认模型或默认外部 URL，不自动启用云服务。

只发送证据元数据；原始 payload、日志、SQL、自由文本不发送。最多一轮 propose_checks，
最多三条建议，每条引用当前任务证据并经核心 Policy 校验。建议不自动执行，不升级根因；
报告原有 summary/findings/limitations 保持规则输出。Report 新字段：

| 字段 | 含义 |
|---|---|
| agent_status | disabled / ok / unavailable / rejected |
| agent_reason | backend_failure / invalid_advice / budget_exceeded（可空） |
| agent_suggestions | tool_id、instance_id、reason、evidence_ids |

reason 的解释：refresh_snapshot＝重新获取当前快照；check_missing_evidence＝补查缺失证据；
compare_current_state＝对照另一项当前状态。都是调查建议，不是故障事实。
模型不可用不改变已有有效规则调查的终态，但会标注 agent_status；原有历史源缺失仍导致 partial。

## 本地模型协议模拟

使用 Postman API Simulation 技能，在本地跑协议模拟，不发布 workspace 或 mock：

```bash
postman simulation run postman/simulations/agent.sim.yaml --port 18095 --no-history --output ndjson
```

另一个终端：

```bash
DBIC_LLM_SIMULATION_URL=http://127.0.0.1:18095/llm/v1 uv run pytest -q tests/integration/test_agent_simulation.py
```

覆盖成功、401、429、503、超时、伪造 evidence_id；使用假的模型 key，仅 loopback。
模拟通过证明客户端故障处理和 Engine 回退，不能证明真实模型质量、提供商兼容性或费用。
仓库夹具由官方函数调用契约及实际请求建立；CLI 未登录，未搜索远端 owner mock。

## 检查与限制

```bash
uv sync --locked --python 3.12
DBIC_HTTP_TESTS=1 uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run python -m compileall -q src scripts
```

2026-10-31 本次实际结果：

| 检查 | 结果 |
|---|---|
| 首轮本机全量（DBIC_HTTP_TESTS=1，并接入本地 Postman 模拟） | 268 passed、11 skipped，16.46 秒；跳过宿主未启用的数据库测试 |
| 最后应用改动后的 Compose 隔离 MySQL/PostgreSQL 全量 | 273 passed、7 skipped，21.83 秒；跳过 6 个独立 Postman 模拟测试及 1 个独立 DSN 迁移测试，另有真实迁移验证 |
| 后续部署包边界检查 | 10 passed；新增 Postman 协议夹具打包检查，私有配置继续排除 |
| Postman 模拟单独运行 | 6 passed，0.79 秒；日志确认 6 次实际 HTTP 请求，无重试 |
| 官方 MCP 客户端真实 loopback HTTP（auto / legacy） | 2 passed，1.44 秒 |
| ruff / format / mypy / compileall / uv lock --check / Compose config | 全部通过；mypy 43 个源文件 |
| 本机部署 verify_m3.py --require-mcp | PASS：三个工具、鉴权、拒绝 SQL 参数、REST 证据一致性 |
| 本机部署 verify_m2.py --skip-suite | PASS：实时与窗口诊断、已知摘要样本缺口回退 |

本机 API 已构建为 M3，MCP 通过命令环境 `DBIC_MCP_ENABLED=true` 启用，未修改私有 `.env`。
后续重建若需继续开启，请显式保留该变量或写入自己的 `.env`。本地 Postman 模拟已停止。
远程服务器仍为已验收的 M2，本次没有修改远程容器，也没有提交或推送 M3 代码。
完整隔离套件验证了真实 MySQL/PostgreSQL、MCP 审计和 Agent 报告回读。
真实模型未配置；Elastic Agent Builder 未实现（架构中为可选）；进程内队列不自动恢复中断任务。
回退模型/MCP：设 DBIC_AGENT_BACKEND=null、DBIC_MCP_ENABLED=false 后仅重建 API，保留全部数据。
M4/M5 不在本次范围内。

协议依据：[MCP ASGI 集成](https://py.sdk.modelcontextprotocol.io/run/asgi/)、
[OpenAI 函数调用](https://developers.openai.com/api/docs/guides/function-calling)。
