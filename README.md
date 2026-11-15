# 播客选题库（Podcast Topic Library）

一个部署在极空间 NAS 上、仅通过 Tailscale 私有网络访问的播客选题记录网站。

## 功能

- **选题 CRUD**：每条选题包含标题 + 备注，三个内容分区均可自由增删改：
  - 👍 我喜欢它什么（绿）
  - 👎 我不喜欢它什么（红）
  - 💭 它让我想到什么（蓝）
- **搜索**：按标题 / 备注 / 内容全文过滤
- **设置**
  - 访问密码（首次打开时设置，可修改；连续错 10 次锁定 5 分钟）
  - 列表排序（最近更新 / 创建时间 / 标题 × 升降序，即时生效）
  - 数据导出（JSON 下载）/ 导入（追加或替换两种模式）
- **默认黑色主题**，手机 / 桌面自适应布局
- 会话保持 30 天，登录后有独立会话令牌

## 技术说明

- 后端：Python 3 标准库单文件（`app/server.py`），SQLite 存储，PBKDF2 密码哈希 + HMAC 签名会话 Cookie，**零第三方依赖**
- 前端：原生 HTML/CSS/JS（`app/static/`），无框架无构建
- 容器：`python:3.12-alpine`，数据目录挂载 `/data`

## 访问方式

- Tailscale 网络内任何设备：`http://<NAS的Tailscale IP>:8390`
- 首次打开会让你设置访问密码

## NAS 部署配置示例（极空间 / arm64）

| 项目 | 值 |
|---|---|
| 容器名 | `podcast-topics` |
| 镜像 | `podcast-topics:latest`（启动命令已内置） |
| 端口映射 | 本机 8390 → 容器 8390 |
| 数据目录（NAS） | `我的文件/播客选题库/data`（容器内 `/data`，SQLite 数据库 topics.db 在这里） |
| 源码备份（NAS） | `我的文件/播客选题库/app/` |
| 开机自启 | 建议开启 |
| 内存限制 | 可按 NAS 资源设置，例如 1024MB |

### 部署方式说明（无 SSH / 无 Docker 环境）

本机没有 Docker、NAS 无法通过 SSH 执行命令时，可以使用以下流程：
1. `deploy/build_image.py`：纯 Python 从国内镜像源(docker.m.daocloud.io)拉取 `python:3.12-alpine` arm64 层，
   叠加应用层后手工组装成 `docker load` 兼容的 tar（`deploy/validate_image.py` 校验结构）
2. `deploy/smb_upload.py`：通过 SMB（impacket）把镜像 tar 和源码上传到 NAS 个人空间
3. 极空间 Docker 界面：导入镜像 → 创建容器（配置见上表）
4. 重打镜像：`IMAGE_ARCH=arm64 python deploy/build_image.py`（换架构改环境变量）

### 本地连接配置与 Git 提交

复制 `deploy/config.example.json` 为 `deploy/config.local.json`，在本地填写 NAS 地址、账号、密码和共享名。示例域名仅作占位，需替换后才能连接。已有本地配置时保留原文件。

- 只提交示例配置；`config.local.json`、环境变量文件和私钥已加入 `.gitignore`。
- `data/`、数据库及其日志文件、测试 Cookie、`build/` 和 `podcast-topics-*.json` 导出文件不入库。自定义名称的导出或备份请放到 `data/` 或仓库外。
- 数据库不仅包含选题，还存有访问密码哈希及会话签名密钥；不要上传数据库或包含它的备份。
- 提交前检查 `git status --short` 和 `git diff --cached`；不要对私人文件使用 `git add -f`。
- `.gitignore` 不会移除已跟踪文件或历史记录中的信息。历史中若出现真实凭据，需要更换凭据并另行清理历史。

### 忘记密码 / 重置数据

1. 极空间 → Docker → 停止 podcast-topics 容器
2. 删除 `我的文件/播客选题库/data/topics.db`
3. 启动容器 → 打开网站重新设置密码（数据清空）

### 更新网站代码

1. 本地改 `app/` 下的代码 → 重跑 `build_image.py`（arm64）→ `smb_upload.py` 上传覆盖 tar
2. 极空间 Docker：删除旧容器 → 导入新 tar（同名镜像会覆盖）→ 重新创建容器（配置同上）
   （数据在 `/data` 卷里，不受影响）

## 目录结构

```
PodCastResourceLib/
├── app\
│   ├── server.py          # 后端（标准库单文件）
│   └── static\            # 前端
│       ├── index.html
│       ├── style.css
│       └── app.js
├── build\                 # 镜像 tar 输出目录
├── data\                  # 本地开发时的数据目录（NAS 上在 我的文件/播客选题库/data）
├── tests\api_test.sh      # API 集成测试（42 项断言）
├── deploy\
│   ├── build_image.py     # 无 Docker 手工构建镜像（registry API 拉层 + 组装 tar）
│   ├── validate_image.py  # 镜像 tar 结构校验
│   ├── config.example.json # 可提交的连接配置模板
│   ├── config.local.json  # 私人连接配置（忽略，不提交）
│   └── smb_upload.py      # SMB 上传到 NAS
└── Dockerfile             # 备用（有 Docker 环境时可用）
```

## 本地开发

```bash
cd app
DATA_DIR=../data PORT=8390 python server.py
# 浏览器打开 http://127.0.0.1:8390
```

## 数据与备份

- 所有数据在 NAS 的 `我的文件/播客选题库/data/topics.db`（SQLite 单文件）
- 网站内：设置 → 导出全部数据（JSON）
