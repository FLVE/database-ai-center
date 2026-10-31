# M4.2 MongoDB 诊断设计

用户在 M4 分批计划后授权开始。只实现 MongoDB lab；PostgreSQL/Doris 与网页界面后续推进。

## 范围与契约

- MongoDB 8.0.34，官方镜像锁定多架构摘要，支持 ARM64/AMD64；一个成员的 `dbic-rs`。
  不宣称复制冗余、故障切换或多节点延迟已验证；不连接 standalone、mongos 或任意目标。
- 实例固定 `mongodb-lab-01`，版本 `8.0`，地址 `mongodb-lab:27017`，authSource=admin，
  用户 diag。新增 PyMongo 4.x 异步客户端；directConnection=true、replicaSet=dbic-rs，
  禁用重试，连接/选服 1 秒、socket 2 秒、工具总超时 5 秒，工具结束关闭客户端。
- REST 新增问题 `activity_health`，固定顺序 `mongodb.current_ops_summary`、
  `mongodb.replica_health`。沿用创建/详情/证据/反馈端点和鉴权；拒绝 window、额外命令/连接参数、
  跨数据库问题。默认禁用，未授权返回 403，非法输入 422；无效请求零工具调用。
- MCP 新增对应两个下划线命名工具，仅接受 instance_id，每次归档一条证据。
  capabilities 标识已实现与启用状态；MongoDB 使用 NullAgent，不扩展 MySQL 模型建议白名单。

## 工具、权限与证据

diag 自定义角色仅授予 cluster resource 的 inprog、replSetGetStatus；不授予业务集合读写、
killop、复制集配置或管理员角色。初始化管理员只存在数据库/隔离测试容器，API 不接收。

工具先固定 buildInfo、hello 校验 8.0.x 和 dbic-rs；当前操作采用 admin 聚合：
`$currentOp(allUsers=true,idleConnections=false,idleCursors=false,idleSessions=false)`，
过滤 active 且排除本诊断客户端，服务端投影仅保留 op、secs_running、waitingForLock，
最多 51 条（50 条样本加一条截断探测），maxTimeMS=1500、allowDiskUse=false。
不接收用户 pipeline；不传输查询正文、命名空间、客户端地址、用户和 opid 到证据层。
只保存已知操作类别、非负时长、锁等待标志；缺失字段保留缺失，未知类别归 OTHER。
报告只描述当前有限样本；运行较久不等于慢查询，getMore 等待也不自动成为故障。

复制集固定 replSetGetStatus，验证 set、成员数和本成员身份；只保存成员编号、数值状态对应
的枚举、health 布尔值和本机状态。丢弃地址、errmsg、原始 BSON 和日志。
单成员状态不用于推断复制延迟；健康读取成功与成员本身不健康分开表示。

证据只用 UTC 当前快照，核心纯 Schema/规则验证归属、时效（90 秒/未来容差 5 秒）、
类型和状态一致性。每条结论有 evidence_ids，不输出 confirmed_root_cause。
没有样本为 empty；权限不足 forbidden，断连/不支持 unavailable，超时 timeout，
不合法响应 error。有限采样和单成员限制必须保留，任务通常 partial，不强求 completed。
原始驱动异常仅记录类型，不记录连接串/响应正文。证据沿用 64 KiB 上限；不宣称驱动解码前硬限流。

## 实施与验收

修改策略、Planner、Registry、工具分派、Runner、REST/MCP、Settings 和装配层；新增
core MongoDB Schema/规则、plugins/mongodb、两个 Playbook 工具、默认关闭的 lab profile、
私有凭据/复制集初始化脚本、只读验收脚本及单元/真实集成测试。不新增迁移。
保留先前文档修改，不改现有远程服务；本批先本地实现与双架构验证，部署另按已有升级流程。

每个测试 MongoDB 独立于运行中的 lab，独立 verification 网络；数据操作仅用于 fixture。
至少两个可重复场景：空闲快照、有限等待中的真实 getMore；附加正常复制集、撤销观察权限并恢复、
诊断账号无业务集合读写权限、错版本/拓扑、无凭据、超时、坏结构、敏感字段、截断、旧证据。
真实 PostgreSQL 重复迁移、REST 归档/新应用回读/反馈/审计、MCP 一条证据一致性均须通过。
所有 fixture 有 finally 清理，不执行 killOp、重启或复制集重配作为诊断 API。
完整旧套件、ruff/mypy/lock/Compose 配置检查通过；跳过项与远程未验收项分别说明。

## 协议依据

- [$currentOp 8.0](https://www.mongodb.com/docs/v8.0/reference/operator/aggregation/currentop/)：admin 聚合和 inprog 权限。
- [权限动作 8.0](https://www.mongodb.com/docs/v8.0/reference/privilege-actions/)：自定义最小角色。
- [replSetGetStatus](https://www.mongodb.com/docs/v8.0/reference/command/replSetGetStatus/)：本节点视角的成员状态。
- [PyMongo 异步客户端](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/connect/mongoclient/)：异步连接与超时。
