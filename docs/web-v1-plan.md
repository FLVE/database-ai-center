# 网页界面 V1

用户在 M4 整体验收及 DBX 评估后授权第一版独立网页。本阶段不接入 DBX，
不扩展 M5、生产连接、任意查询或自动执行 Agent 建议。

## 范围与交付

- 实例与能力：读取服务端许可的 lab 摘要，不向浏览器返回连接引用、地址或凭据。
- 创建诊断：按实例支持的问题选择；仅 MySQL 支持可选历史窗口，使用浏览器当地时间输入、UTC 提交。
- 任务：提交后轮询，按任务 ID/链接重开；本次会话任务列表不冒充服务器历史列表。
- 报告：状态、摘要、事实与假设、步骤、缺失证据、建议和 Agent 状态；结论关联可打开的证据。
- 证据：仅消费原有脱敏接口，显示来源、采样时间、窗口、限制和 payload。
- 反馈：终态任务提交准确性、确认根因和备注，并回读归档记录。

## 实现与接口

新增 `GET /api/v1/instances`，与其他 API 使用相同 Bearer 鉴权。
返回已配置的固定 lab 实例摘要列表：id、database_type、environment、version、enabled、
problem_types、supports_history。enabled 表示通过既有 Policy，不代表数据库连通。
problem_types 来自源码 Playbook，禁用实例为空；不增加 Registry Port 方法。
创建/详情/证据/反馈契约不变；暂不新增服务器历史分页、用户管理或 Cookie 会话。

`src/dbic/web/` 使用原生 ES modules + HTML/CSS，由 FastAPI 同源提供 `/`、`/assets/*`。
当前页面复杂度无需运行时框架或 Node 构建；资源随 Python wheel/Docker 打包。
新增 Playwright 仅用于可复现浏览器测试，锁定 npm 依赖。接口契约仍以 FastAPI OpenAPI 为准。
本机更新仅重建 DBIC API，保留全部现有 lab 和 ES/Kibana；本阶段不自动提交或远程发布。

## 交互与安全

- 中文调查工作台；GitHub 同类项目研究、视觉规范和新版布局见 `web-design-research.md`。
- 左侧实例，中央实例概览或标签式报告，新建诊断使用对话框；证据编号打开右侧抽屉。
  空态从真实能力出发，不显示虚假统计、趋势或成功报告。
- Token 只存内存，不写 URL、local/sessionStorage、Cookie 或日志；刷新重新输入。
- 动态内容使用 textContent，报告不渲染原始 HTML/Markdown；CSP 禁止外部脚本、连接和嵌入。
- 请求 no-store、同源、无自动 POST 重试；提交响应丢失时明确可能已创建，不诱导重复提交。
- 退出/401 清除敏感 DOM、停止轮询、取消旧请求；任务切换使用代次保护，旧响应不覆盖当前任务。
- 长时间运行停止自动轮询并提供手动刷新，不把浏览器超时等同于任务失败。
- 支持窄屏、键盘导航、可见焦点和 reduced-motion；证据对话框关闭后返回触发控件。

## 验收

1. API 单测：无/错误 Token 拒绝，实例摘要不泄密、禁用/非法配置不开放诊断；原 API 回归。
2. 浏览器：真实 FastAPI + 内存测试适配器跑完整流程；补 partial/failed、401、429、网络错误、
   延迟/切换竞态、XSS 文本、反馈回读、重开任务、移动端与退出清理。
3. 本机实际 lab：五类实例读取；至少一次真实 MySQL 诊断、证据与反馈回读。
4. Ruff、mypy、pytest、浏览器测试、wheel 静态资源检查，记录实际结果及未覆盖范围。
