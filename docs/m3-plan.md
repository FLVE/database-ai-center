# M3：Agent Runtime 与 MCP

用户在远程 M2 `--require-search` 验收通过后授权继续。ES 验收使用专用 lab
合成事件；不代表真实日志采集或 Prometheus 接入已完成。

## 范围与文件

- `core/agent.py`、`models.py`、`runner.py`：结构化调查建议及证据引用校验；规则报告保留事实所有权。
- `adapters/agent/`：默认 Null，可选 OpenAI 兼容 Chat Completions 函数调用适配器。
  单次请求、有限超时/响应大小、禁止重定向与环境代理，不发送 payload、SQL、反馈、凭据。
  模型只能选择预定义建议类型和工具，不能返回直接展示的自由文本或确认根因。
- `mcp/server.py`、`api/service.py`、`core/planner.py`：标准 MCP SDK 的 Streamable HTTP，
  三个具名工具；复用 TaskRunner、Policy、工具实现、PostgreSQL 归档及审计。
- `main.py`、`settings.py`、Compose、样例配置：`/mcp` 默认关闭；开启后每个请求
  使用已有本地 Bearer Token，限制 Host/Origin、请求大小、任务容量与执行预算。
- 测试、验收脚本、架构与实施文档：同步 M3 能力契约，M1/M2 验收脚本向后兼容。
- `postman/mocks/llm/`、`postman/simulations/`：本地模型协议模拟；`package_release.py`
  将这些源码夹具纳入部署包，继续排除私有环境文件。

## 验收

1. 无模型/模型失败/超时/恶意响应时仍保存规则报告，明确 Agent 状态。
2. Agent 输出不存在、跨任务或未脱敏的证据引用、危险工具、过量建议、自由文本被拒绝。
   建议仅供人工后续发起检查，不自动执行；预算按已有调用加建议总量校验。
3. MCP 未认证、非法 Origin/Host、未授权实例、任意 SQL 参数均不能执行工具。
   合法调用的结果含 task_id、evidence_id，REST 可回读，成功调用沿用 PostgreSQL 审计。
4. 运行单测、真实 HTTP 协议测试、隔离 PostgreSQL/MySQL 套件、ruff、mypy、锁文件检查。
5. 保留进程内队列限制，不宣称持久 Worker/生产 HA。M3 阶段清单优先于规划表中的
   可选 worker；不增加后台调度、Redis、生产连接、Elastic Agent Builder 或外部发布。

## 实现取舍

保持 AgentRuntimePort.explain 签名兼容；Report 仅增加默认可缺省的 Agent 状态与建议。
MCP 单工具请求创建内部 `tool:<tool_id>` 任务，REST 创建问题类型仍仅 lock_wait/slow_query。
新增依赖为官方 MCP SDK，HTTP 客户端复用 HTTPX；不引入特定 Agent 框架。
真实模型服务未指定或无凭据时，用本地协议模拟验证，不把模拟通过称为真实模型验收。

## DeepSeek 接入补充

用户选择 DeepSeek 并在服务器配置 Key 后，增加 DBIC_LLM_PROVIDER=deepseek 参数模式。
仅调整适配器的请求参数和服务端 schema 方言；核心策略、Port、诊断 API 和迁移不变。
使用 /beta 严格工具调用、非思考模式、max_tokens；不发送未声明支持的 store、parallel_tool_calls。
展开 schema 引用、移除服务端不支持的数组长度字段，但本地 Pydantic 限额继续强制执行。
验收：两种方言的模拟故障矩阵、负面引用/数量校验、远程两个诊断场景 agent_status=ok，
MCP 与 ES 回归。模型调用不向业务库写入数据，建议仍不自动执行。
