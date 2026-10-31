# M4.4 Doris 诊断

与 M4.2/M4.3 一起完成验证后统一发布。只支持隔离的 Doris 3.0.8 单 FE/单 BE lab，
官方固定 digest 的 ARM64/AMD64 镜像，无宿主端口；不接入生产或修改已有服务。

新增 `cluster_health` 问题和两个固定只读工具：

- `doris.fe_be_health`：固定 FE `/api/health` 的在线/总 BE 数；仅当前 FE 可达和其视角，
  不宣称多 FE 高可用、磁盘或 tablet 健康。
- `doris.query_profile_summary`：固定 `/rest/v1/query_profile/`，
  普通 diag 用户读取列表后严格筛选 User=diag 的保留 Profile。只保留至多 20 条耗时及状态枚举，不持久化 SQL、
  用户、库名、地址或完整 Profile。空列表是 empty，不证明业务没有慢查询。

diag 不授予 ADMIN/NODE 或业务数据权限。凭据由本机生成到忽略的 dist 目录，
bootstrap 与诊断凭据分离。运行时固定端点、禁重定向/环境代理、响应限 256 KiB、工具总预算
5 秒，无重试；版本不符合、权限不足、响应损坏、超时均显式降级。core 不引入 HTTP/驱动依赖，
模型不参与本批报告。REST/MCP 沿用证据归档、反馈、父任务和受保护 OpenAPI。

真实验收：在线 FE/BE；diag 自己的测试 Profile 与空/缺失 Profile；普通用户权限边界，
REST/MCP 归档等价。测试 SQL 仅用于隔离 test-doris 的 fixture，不成为 API。
另测 HTTP 401/403/5xx、超时、超大/错误 JSON、字段缺失及脱敏。验证双架构，记录真实资源占用；
资源不足则保留未完成状态，不停其他服务、不放宽权限、不以 mock 冒充真实验收。

小型 lab 使用官方启动脚本的 SKIP_CHECK_ULIMIT 开关，避免修改宿主 vm.max_map_count/swap；
nofile 只在容器内设 65536，FE/BE 有内存上限。这不是生产容量配置，真实 fixture 和资源验证
仍须通过，不能据此承诺大规模 tablet/并发负载。
BE 关闭 `enable_java_support`：本批仅原生查询，不使用 Java UDF/JDBC/Hudi 等外部连接器，
避免无关 JNI 扫描器加载和内存开销。该开关是
[官方 BE 配置](https://doris.apache.org/docs/3.x/admin-manual/config/be-config/)，不是绕过诊断权限。

3.0.8 真实采样发现 v2 query_info 在生成 Profile 后返回内部空指针错误；采用经实测的
[v1 列表接口](https://doris.apache.org/docs/3.x/admin-manual/open-api/fe-http/query-profile-action-controller/)。
该接口可能返回其他用户元数据，应用在内存中严格按 User=diag 筛选，丢弃其余内容；不记录原始响应。
版本确认使用固定 `SELECT @@version_comment`，不使用要求 ADMIN 的版本 HTTP 接口。

依据：[Health API](https://doris.apache.org/docs/3.x/admin-manual/open-api/fe-http/health-action/)、
[Profile API](https://doris.apache.org/docs/3.x/admin-manual/open-api/fe-http/query-profile-action/)。
