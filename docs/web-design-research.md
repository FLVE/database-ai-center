# DBIC 工作台设计研究与改版

2026-10-31。用户要求先分析 GitHub 同类项目，再重新设计。范围限于已授权的网页 V1，
保持 API、诊断引擎、鉴权及只读能力不变。本文件替代 `web-v1-plan.md` 中原先的视觉布局选择。

## 研究依据

检查公开源码与官方仓库引用的截图，不把 README 宣传图当作实际产品界面。
showcode 服务无法连接，本次使用 GitHub 连接器读取源码、直接查看官方图片。

| 项目与证据 | 观察 | DBIC 的取舍 |
| --- | --- | --- |
| [Bytebase 任务标题源码](https://github.com/bytebase/bytebase/blob/fcb123688b7cf02a43774da9befcd90655d7c0aa/frontend/src/routes/project/issue-detail/components/IssueDetailHeader.tsx)、[任务内容源码](https://github.com/bytebase/bytebase/blob/fcb123688b7cf02a43774da9befcd90655d7c0aa/frontend/src/routes/project/issue-detail/components/IssueDetailDatabaseChangeView.tsx) | 标题、状态和动作集中；用标签与上下文面板分离复杂内容。本次未以产品截图验证其完整页面。 | 采用紧凑任务上下文与标签页；不引入数据库变更审批或执行能力。 |
| [SigNoz 官方追踪详情截图](https://github.com/SigNoz/signoz/blob/b37231f31a9503aa0bfb319cebec474ddc06cd9e/docs/readme-assets/monitor/distributed-tracing.png)、[详情标签源码](https://github.com/SigNoz/signoz/blob/b37231f31a9503aa0bfb319cebec474ddc06cd9e/frontend/src/pages/TraceDetailsV3/TraceDetailsHeader/TraceDetailsTabs.tsx) | 窄导航、紧凑上下文、可展开调查详情；火焰图和瀑布图服务真实追踪数据。 | 采用检查明细和按需查看证据；不搬入 DBIC 没有时序数据支持的图表。 |
| [CloudBeaver 仓库](https://github.com/dbeaver/cloudbeaver/tree/39951ecfb321f85d7e90bc12a164fc0d9e89bf48)、[README 引用的工作区截图](https://raw.githubusercontent.com/wiki/dbeaver/cloudbeaver/images/sql-editor-demo.png) | 左侧连接树提供稳定上下文，右侧工作区用标签分开编辑与结果。Wiki 图片未固定版本。 | 采用实例导航和报告工作区；不提供 SQL 编辑器、任意查询或数据库管理动作。 |

不复制上游样式、代码或品牌素材；只借鉴信息组织方式。截图下载到忽略的
`dist/design-references/` 供本地研究，不随产品分发。

## 现状与选择

原版固定创建表单占用报告宽度，标题口号占据首屏，报告、步骤、反馈纵向叠加过长。
本次面向排障工程师，页面的单一目标是完成“选择实例 → 调查 → 对照证据 → 反馈”。

比较方案：保留双栏表单能快速连续提交，但挤占日常阅读；独立新建页面会打断报告上下文。
选用可关闭的新建对话框，报告用三个标签页，证据用右侧抽屉。

```text
实例导航       工作空间 / 数据库调查       就绪 · 刷新 · 退出
MySQL          调查工作台                              新建诊断
Redis          实例概览，或任务标题 / 状态 / ID / 时间
MongoDB        [调查报告] [检查与证据] [人工反馈]
PostgreSQL     结论 + 证据编号     | 覆盖限制与后续建议
Doris          点击编号 → 右侧证据抽屉（来源、时间、限制、payload）
本次会话
任务 ID 查找
```

设计系统：纸白 `#ffffff`、雾灰 `#f5f6f8`、石墨 `#242b3d`、靛蓝 `#4c50b8`、
证据青 `#14776c`、限制赭 `#925f0c`；错误使用语义红。标题使用系统 Avenir Next / 中文黑体，
正文使用系统中文无衬线，ID、工具名及证据编号使用 SFMono / Consolas。无外部字体或 CDN。
左侧 232px 稳定导航、紧凑分隔线、6–8px 圆角；避免大面积卡片堆叠、装饰渐变和虚假 KPI。
标志性元素是贯穿结论和证据列表的青色编号引用：编号对应本任务接口返回顺序，绝非严重程度排名。

自评修正：仅换成常见管理台颜色无法解决问题，因此重点变为阅读模式与创建模式分开，
报告和反馈分区，缺失项仍常显，非关键的时间线/工具信息折叠。没有依据的正常/健康结论不增加。

## 文件与验收

- `src/dbic/web/index.html`、`styles.css`：布局、创建对话框、标签页、证据抽屉和响应式。
- `app.js`、`ui.js`：真实能力概览、标签与焦点、原有报告分组；保持安全渲染、轮询与竞态保护。
- `tests/web/workspace.spec.js`、`scripts/verify_web.mjs`：新交互路径、键盘与窄屏验收。
- `docs/web-v1-plan.md`、`docs/web-v1-validation.md`：更新入口与实际验证记录。

验收保持原 V1 全流程，另验证新建对话框关闭/错误提示、标签键盘导航、切换实例上下文、
轮询不切走活动标签、抽屉焦点返回和桌面/手机截图。使用既有测试服务器及本机 lab，
不改 API 契约、不新增依赖、不执行生产操作、不提交/推送或远程发布。
