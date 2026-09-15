# 诡秘之主 · 活动报名系统

塔罗会帮会活动报名工具，支持周四/周六活动双选报名、实时同步、批量导入。

## 功能特性

- 🎮 **成员报名**：输入姓名 + 选择职业，勾选周四/周六活动，可双选
- ⚔️ **6 种职业**：战士、占卜家、窥秘人、观众（奶）、学徒、歌颂者
- 🔄 **实时同步**：基于 SSE 实时推送，房间内所有人秒级看到最新名单
- 📋 **批量导入**：粘贴名单文本一键导入，支持多种分隔符
- 📝 **复制名单**：一键复制格式化名单（文本/Markdown/CSV）
- 🧹 **自动清理**：7 天无人访问的房间自动删除
- 📱 **手机端友好**：响应式设计，移动端完美适配
- 🎨 **深色游戏风格**：紫色/蓝色神秘主题

## 技术栈

- **后端**：Python 3 + Flask + Waitress
- **数据库**：SQLite（零配置，单文件）
- **实时推送**：Server-Sent Events (SSE)
- **前端**：原生 HTML + CSS + JavaScript（无框架依赖）

## 快速开始

### 1. 环境要求

- Python 3.8+

### 2. 安装依赖

```bash
cd 活动报名系统
pip install -r requirements.txt
```

### 3. 启动服务

```bash
./start.sh
# 或直接
python3 app.py
```

服务启动后访问：http://localhost:3000

### 4. 环境变量（可选）

```bash
export PORT=3000          # 监听端口
export DB_PATH=./data/signup.db  # 数据库文件路径
```

## 部署到服务器

### 使用 nohup 后台运行

```bash
nohup python3 app.py > server.log 2>&1 &
```

### 使用 systemd（推荐）

```ini
# /etc/systemd/system/gmzz-signup.service
[Unit]
Description=诡秘之主活动报名系统
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/path/to/活动报名系统
ExecStart=/usr/bin/python3 app.py
Environment=PORT=3000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable gmzz-signup
systemctl start gmzz-signup
```

### Nginx 反向代理（可选）

建议配置 HTTPS 和反向代理：

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE 需要的配置
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
        proxy_http_version 1.1;
        chunked_transfer_encoding off;
    }
}
```

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/rooms` | 创建房间 |
| GET | `/api/rooms/:roomCode` | 获取房间信息和成员列表 |
| GET | `/api/rooms/:roomCode/stream` | SSE 实时推送连接 |
| POST | `/api/rooms/:roomCode/members` | 添加/更新成员（按姓名 UPSERT） |
| DELETE | `/api/rooms/:roomCode/members/:id` | 删除成员 |
| POST | `/api/rooms/:roomCode/members/batch` | 批量导入成员 |
| GET | `/api/rooms/:roomCode/export?format=text` | 导出格式化名单 |
| GET | `/api/health` | 健康检查 |

## 批量导入格式

支持以下格式（每行一个成员）：

```
张三 战士 是 是
李四 占卜家 1 0
王五 观众（奶） 周四 周六
赵六,学徒,是,否
钱七、歌颂者、周四
```

分隔符支持：空格、逗号、顿号、分号、制表符

周四/周六标识支持：`1/0`、`是/否`、`y/n`、`true/false`、`周四/周六`

职业模糊匹配：输入「奶」「奶妈」「治疗」自动匹配为「观众（奶）」

## 项目结构

```
活动报名系统/
├── app.py               # 后端主服务 (Flask + SQLite + SSE)
├── requirements.txt     # Python 依赖
├── start.sh             # 一键启动脚本
├── data/
│   └── signup.db        # SQLite 数据库（自动生成）
├── public/
│   ├── index.html       # 前端页面
│   ├── style.css        # 样式
│   └── app.js           # 前端逻辑
└── README.md            # 说明文档
```

## 自动清理机制

- 服务每小时执行一次过期房间清理
- 清理条件：`last_accessed_at` 超过 7 天
- 清理会级联删除房间下所有成员数据
- 启动后 1 分钟执行首次清理
