# M2：历史证据

仅补充现有 MySQL lab 诊断的历史上下文。默认 Null，不接入生产，不创建采集器，不改现有 ES 容器/卷/索引。

变更范围：HTTPX 运行依赖；Prometheus/Elasticsearch 只读适配器及统一 HTTP 限制；
ToolResult 的 kind/window 元数据；历史调用接入 Runner、规则和报告；Settings/Compose；
新建 lab 索引的映射及 create-only 样例脚本；HTTP 契约、失败/降级测试和本机验证脚本。

保持 Port 方法签名，扩展 ToolResult 可选字段，检查现有调用点。带 window 的任务追加
最多两个历史调用：固定指标与固定事件搜索；不带 window 仍是 M1 两个实时工具。
24 小时窗口上限、UTC、每个请求总超时 5 秒、响应 256 KiB、最多 301 个采样/50 条事件。
PromQL/DSL 不允许通过 REST API 输入，ES 仅查询 db-events-lab，instance 固定 mysql-lab-01。
显式配置 Prometheus instance 标签映射，不假定监控地址等于逻辑实例 ID。

历史事实只引用实际采样/事件，不能根据时间相关性升级根因；空搜索不能断言没有故障。
错误、部分分片、非法/超界数据、NaN、采样缺口、截断均显式标注；失败不抹去实时结论。
默认不传输日志 message/SQL 原文，只保留经过白名单验证的时间、类别、事件编码。

验收：Null/真实适配器输出同构报告；401/403/404/429/5xx/timeout/disconnect/invalid JSON；
UTC/窗口/采样缺口；持久化历史证据归属；固定请求内容和只读调用；现有 M1 回归。
HTTP mock 只能证明客户端契约，真实后端用专用只读验证脚本；沙箱无法联网/监听时明确记未实测。
索引样例使用 _create 和稳定 ID，不覆盖已有数据；已有非 DBIC 管理索引拒绝操作。
