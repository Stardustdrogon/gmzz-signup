# 诡秘之主 · 活动报名系统 v2.0

帮会活动报名工具，固定两场活动：周四 · 霜陨领主 / 周六 · 猎城战。
深色紫蓝游戏风格，手机端友好，支持实时同步。

## ✨ 功能

- **三场入口首页**：周四霜陨领主 / 周六猎城战 / 总名单
- **成员报名**：游戏昵称 + 职业，可同时报名两场
- **6 种职业**：战士、占卜家、窥秘人、观众（奶）、学徒、歌颂者
- **实时同步**：SSE 实时推送，所有人看到最新名单
- **批量导入**：粘贴文本批量导入，智能识别职业
- **一键复制名单**：格式化文本，按职业分组
- **总名单视图**：标记双场 / 仅周四 / 仅周六 用户
- **职业分布统计**：可视化各职业人数
- **7x24 运行**：SQLite 文件数据库，零配置

## 🚀 快速启动

### 方式一：Python 直接运行（最简单）

```bash
pip install -r requirements.txt
python app.py
```

浏览器打开 http://localhost:3000

### 方式二：Render 一键部署（免费公网访问）

1. Fork 本仓库到你的 GitHub
2. 打开 https://dashboard.render.com/
3. New → Web Service → 选择 gmzz-signup 仓库
4. Runtime 选 Python 3
5. Build Command: `pip install -r requirements.txt`
6. Start Command: `python app.py`
7. 选 Free 套餐 → Create

等待 1-2 分钟即可通过 `https://你的名称.onrender.com` 访问。

### 方式三：VPS 部署 + systemd 自启

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 创建 systemd 服务
cat > /etc/systemd/system/gmzz-signup.service << 'EOF'
[Unit]
Description=诡秘之主活动报名
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/活动报名系统
ExecStart=/usr/bin/python3 app.py
Environment=PORT=3000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

# 3. 启动
systemctl daemon-reload
systemctl enable gmzz-signup
systemctl start gmzz-signup
```

### 方式四：Docker

```bash
docker build -t gmzz-signup .
docker run -d -p 3000:3000 -v $(pwd)/data:/app/data gmzz-signup
```

## 📂 目录结构

```
活动报名系统/
├── app.py               # 后端 (Flask + SQLite + SSE)
├── requirements.txt     # Python 依赖
├── render.yaml          # Render 部署配置
├── Dockerfile           # Docker 镜像
└── public/
    ├── index.html       # 首页（三入口）
    ├── event.html       # 活动详情页
    ├── all.html         # 总名单页
    ├── style.css        # 深色紫蓝主题
    └── app.js           # 前端逻辑
```

## 🎮 API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/events` | 获取活动配置 |
| GET | `/api/members?event=` | 获取名单（event=thursday/saturday/空=全部） |
| POST | `/api/signup` | 报名 |
| POST | `/api/batch` | 批量导入 |
| DELETE | `/api/members/:id` | 删除成员 |
| GET | `/api/export?format=text&event=` | 导出名单 |
| GET | `/api/stream` | SSE 实时推送 |
| GET | `/api/health` | 健康检查 |

## 环境变量

- `PORT` - 监听端口，默认 3000
- `DB_PATH` - 数据库文件路径，默认 `data/signup.db`

## 🎨 技术栈

- **后端**：Python 3 + Flask + SQLite + SSE
- **前端**：原生 HTML/CSS/JS，零依赖
- **服务器**：waitress（生产级 WSGI）
- **风格**：深色紫蓝游戏主题
