# M2 远程 Linux 验证部署

目标：在指定的 Linux 验证主机部署 DBIC 自有 lab 服务，继续使用隔离的
MySQL 8.4 和 PostgreSQL；已有 ES/Kibana 服务、索引及业务数据库不随部署修改。
不扩大诊断能力，不进入 M3。

## 当前执行状态

代理环境尝试 SSH 只读检查时，在认证前被网络沙箱阻止：

```text
ssh: connect to host <target> port 22: Operation not permitted
```

未实际登录、上传或启动任何远程服务。SSH 地址、别名和私钥路径保留在部署者本机，
不写入公开仓库。后续可以在服务器直接通过 HTTPS 拉取本仓库，无需传输本机私钥。
远程 OS、资源余量、Docker 权限、ES 是否已启动、真实端口与认证尚未确认。
不能把本地打包和测试结果视为部署成功。

已准备：

- `deploy/compose.linux.yaml`：仅给 DBIC API 添加 Linux `host-gateway` 映射。
- `scripts/deploy_linux_lab.py`：默认只做部署前检查，`--deploy` 才创建新凭据及部署。
- `scripts/package_release.py`：按源码目录及文件类型打包，拒绝符号链接；排除 `.env`、
  `.venv`、缓存、Git、本机私钥。`.env.example` 只含配置键，不含真实凭据。
- `scripts/verify_m2.py --require-search`：要求真实 ES 返回事件，允许 Prometheus 关闭。
- `tests/unit/test_remote_release.py`：打包边界、凭据权限与防覆盖、部署顺序和资源冲突。

本地实测：`pytest -q` 为 204 passed、21 skipped、1 warning；ruff、格式检查、mypy、
compileall 通过，基础与 Linux override 合并后的 `docker compose config --quiet` 通过。
远程部署新增用例及 ES 单后端验收回归已执行；Linux 容器启动仍因 SSH 阻断未实测。

## 部署包

仓库根目录运行（同名包已存在会停止，不覆盖）：

```bash
python3 scripts/package_release.py dist/dbic-m2-linux-20261031.tar.gz
```

包中包含锁文件和测试，Docker 会在目标机器按其原生架构构建 Python 3.12 镜像，
不上传 Mac arm64 虚拟环境或本机实验库数据。脚本输出包的 SHA256。
以后添加业务配置到源码目录时，仍须审阅内容是否适合部署；文件过滤不等于通用密钥扫描。

## 构建下载受限时

Dockerfile 使用同版本的官方 uv 二进制镜像，依据
[uv Docker 集成说明](https://docs.astral.sh/uv/guides/integration/docker/)。
若 BuildKit 访问 Docker Hub 的认证端点超时，可以先使用 Docker daemon 拉取基础镜像：

```bash
docker pull python:3.12-slim
docker pull ghcr.io/astral-sh/uv:0.11.7
```

如果服务器从 PyPI 下载依赖很慢，可以在网络正常的开发机为目标架构构建镜像，再传输
到服务器。以下示例针对 Linux x86_64，不能直接使用 Mac arm64 镜像：

```bash
mkdir -p dist
docker build --platform linux/amd64 --target runtime -t dbic-remote-runtime:m2-amd64 -f deploy/Dockerfile .
docker build --platform linux/amd64 --target verification -t dbic-remote-validation:m2-amd64 -f deploy/Dockerfile .
docker image save -o dist/dbic-m2-amd64-images.tar dbic-remote-runtime:m2-amd64 dbic-remote-validation:m2-amd64
gzip dist/dbic-m2-amd64-images.tar
```

构建前核对工作区和目标提交，不包含 `.env`。
通过 SSH/SCP 传输归档并核对两端 SHA256。服务器载入后，只给 DBIC 自有镜像打标签：

```bash
docker load -i dist/dbic-m2-amd64-images.tar.gz
docker tag dbic-remote-runtime:m2-amd64 dbic-lab-dbic-api:latest
docker tag dbic-remote-runtime:m2-amd64 dbic-lab-dbic-migrate:latest
docker tag dbic-remote-validation:m2-amd64 dbic-lab-dbic-tests:latest
```

若首次安装已生成 `.env`、启动两个运行库并创建 diag，但中断在 API 构建阶段，
保留已有凭据和账号，从这里恢复；不要重复执行首次初始化或 `setup_mysql_lab.py`：

```bash
docker compose --env-file .env -f deploy/compose.yaml -f deploy/compose.linux.yaml up -d --no-build --pull never --wait --wait-timeout 180 dbic-api
python3 scripts/verify_m2.py --skip-suite
docker compose --env-file .env -f deploy/compose.yaml --profile verify run --rm --pull never dbic-tests
```

最后一条使用已经载入的测试镜像，因此不能附加 `--build`。它仍在隔离的真实数据库和
HTTP socket 上执行测试，并不以本机单测代替远程验收。

## 首次部署与基础验证

需要可访问该 SSH 主机的终端。服务器须已有本地 Docker Engine、支持 `up --wait` 的
Compose v2、Python 3.11+（建议 3.12）；脚本不自动安装系统软件或重启 Docker。
登录用户须有本地 Docker socket 权限。先检查磁盘和内存余量，足以同时运行现有 ES
与 DBIC 的运行库、隔离测试库和构建；未取得资源信息前本会话没有启动这些负载。

在服务器终端执行以下命令。公开仓库可通过 HTTPS 克隆，不需要 GitHub 私钥或 Token。
只在全新目录部署，任一步失败会停止后续步骤：

```bash
git clone https://github.com/FLVE/database-ai-center.git "$HOME/database-ai-center" &&
cd "$HOME/database-ai-center" &&
python3 scripts/deploy_linux_lab.py --deploy
```

如果目标目录已经存在，先检查其中的代码和 `.env`，不要覆盖。上述命令是待执行入口，
尚未由代理远程运行。`--deploy` 会依次：

1. 确认 Linux、宿主 Python 和 Docker/Compose；Docker endpoint 必须是本机 Unix socket。
2. 若有 `dbic-lab` 容器/卷/网络、已有 `.env` 或 8086 被占用，立即停止，不清理或接管。
3. 生成新的四个随机凭据，`.env` 权限 0600；不复制 Mac 上的任何密码。
4. 启动独立 PostgreSQL/MySQL lab，创建仅有七张 Performance Schema 表 SELECT 权限的 diag。
5. 构建 API、执行迁移、等待健康，再运行 M2 API 验收和隔离测试套件。

默认两个历史后端都是 Null，因此该步骤只证明基础部署与降级流程。
中途失败会保留已创建的 `.env` 和 lab 资源；不得靠删除文件/卷重试。应检查报错后针对性
恢复，原有 diag 不会被自动重置。脚本不修改防火墙、不开放公网 8086、不清理其他容器。

后续更新时，在工作区没有未提交修改的前提下执行 `git pull --ff-only`，审阅变更后使用
两份 Compose 配置重建对应服务并运行 `scripts/verify_m2.py`。不要重复运行首次安装脚本，
不要重新生成或覆盖已经使用的 `.env`。公开仓库仅保存源码、锁文件、样例和测试。

## ES 接入与单后端验证

先确认已有 ES 地址、认证方式及权限，再编辑服务器的 `.env`：
`DBIC_SEARCH_BACKEND=elastic`、`DBIC_ELASTIC_URL`、`DBIC_ELASTIC_API_KEY`，需要时指定
公开 CA 证书路径。API 使用仅针对 `db-events-lab` 的 read key；不把管理 key 放进 API。
Prometheus 未配置时继续保持 `DBIC_METRICS_BACKEND=null`。

同机部署优先使用 `http(s)://host.docker.internal:<实际端口>`，无需把公网 IP 加入应用
目标白名单。Linux override 把该名称映射到 Docker host gateway；**若 ES 只监听宿主
127.0.0.1，此映射不能让 bridge 容器访问它**。须先检查监听地址和网络，再选择连接方案，
不为通过验收把 ES 改成公网无认证，也不自动改已有 ES 的网络或容器。
依据：[Docker Compose 网络说明](https://docs.docker.com/compose/how-tos/networking/)。

配置确认后，仅重建 DBIC API：

```bash
docker compose --env-file .env -f deploy/compose.yaml -f deploy/compose.linux.yaml up -d --build --wait dbic-api
python3 scripts/verify_m2.py --require-search --start 2026-10-31T00:00:00Z --end 2026-10-31T00:30:00Z
```

替换示例窗口为 lab 事件实际覆盖的区间（最长 24 小时）。`--require-search` 要求锁等待
与慢查询两个场景都检索到事件；索引缺失、无匹配事件、权限/网络失败均不能通过。
不会自动创建索引/注入事件。若需合成样例验证，应单独使用专用 lab 索引和 create-only
脚本，参见 [M2 验收文档](m2-validation.md)；合成事件不代表真实故障。
Prometheus 与 ES 都需严格验证时仍用 `--require-history`。

从 Mac 访问远程 API 可使用独立本地端口，避免与本机 8086 冲突：

```bash
ssh -N -L 127.0.0.1:18086:127.0.0.1:8086 YOUR_SSH_ALIAS
curl --noproxy '*' --fail http://127.0.0.1:18086/healthz
```

将 YOUR_SSH_ALIAS 替换成部署者本机配置的 SSH 别名。SSH 转发需保持运行；curl 在另一
终端执行。受保护 API 仍需要服务器专用 Token。
停止本项目用两份 Compose 配置执行 `stop`，保留数据；不要执行 `down -v`。
