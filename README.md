# 诡秘之主 · 活动报名系统 v3.0

帮会活动报名工具，固定两场活动：周四 · 霜陨领主 / 周六 · 猎城战。
深色紫蓝游戏风格，手机端友好，支持实时同步、周数管理、历史档案。

## ✨ 功能

- **四宫格首页**：周四霜陨领主 / 周六猎城战 / 总名单 / 历史档案
- **成员报名**：游戏昵称 + 职业，进入对应活动页直接报名
- **6 种职业**：战士、占卜家、窥秘人、观众（奶）、学徒、歌颂者
- **实时同步**：SSE 实时推送，所有人看到最新名单
- **周数管理**：自动计算当前第几周
- **每周日自动归档**：周日 00:00 自动备份+清空，进入新一周
- **历史档案**：查看过往所有周次的报名记录
- **总名单**：批量导入、一键复制、双场标记、职业统计
- **SQLite 文件数据库**：零配置，数据全部存本地

## 💾 关于数据持久化（重要！）

### ⚠️ Render 免费版的问题
Render 免费套餐的文件系统是**临时的**，每次重新部署后本地文件会丢失（数据库、备份都会没）。

### 解决方案（按推荐度排序）：

#### 方案 1：Render + 升级套餐（最简单）
升级到 **Starter 套餐（7 美元/月）**，就有持久化磁盘了，数据不会丢。

#### 方案 2：使用 Postgres（最稳）
把 SQLite 换成 Render 的 **Postgres 数据库**（免费版有 1GB，完全够用）：
1. Render 控制台 → New → PostgreSQL → 创建数据库
2. 复制 `Internal Database URL`
3. 在 Web Service 里加环境变量 `DATABASE_URL`，值就是上面的连接字符串
4. 代码会自动检测并使用 Postgres（目前是 SQLite 版，需要我改成支持 Postgres 的话说一声）

#### 方案 3：自己的 VPS
部署在你自己的服务器上，数据想存多久存多久。配合 systemd 自启 + 定期备份最稳。

#### 方案 4：每周手动导出备份
每周日重置前，去「总名单」点「复制名单」保存一下文本。简单粗暴但管用。

### 备份文件位置
- 数据库：`data/signup.db`
- 每周备份：`data/backups/week_N_YYYYMMDD.db`
- 归档数据：存在 `archives` 表（可通过 `/api/archives` 查询）

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
