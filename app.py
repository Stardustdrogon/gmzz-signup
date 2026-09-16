# 诡秘之主 活动报名系统 - 后端服务
# 支持 SQLite（默认）和 Postgres（DATABASE_URL 环境变量时自动切换）
# 固定两场活动：周四 霜陨领主 / 周六 猎城战
# 功能：成员报名、SSE 实时推送、批量导入、总名单、导出、周数管理、每周日自动归档重置

import json
import time
import os
import re
import threading
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, Response, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder='public', static_url_path='')
CORS(app)

# 全局错误处理
@app.errorhandler(Exception)
def handle_exception(e):
    import traceback
    print(f'[ERROR] {type(e).__name__}: {e}')
    print(traceback.format_exc())
    return jsonify({'error': str(e)}), 500

# ========== 数据库抽象层 ==========
# 自动检测：有 DATABASE_URL 用 Postgres，否则用 SQLite

DATABASE_URL = os.environ.get('DATABASE_URL', '')
USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras
    print('🐘 使用 Postgres 数据库')
else:
    import sqlite3
    DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(__file__), 'data', 'signup.db'))
    print('📦 使用 SQLite 数据库')

PORT = int(os.environ.get('PORT', 3000))
PROFESSIONS = ['战士', '占卜家', '窥秘人', '观众（奶）', '学徒', '歌颂者']
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')

EVENTS = {
    'thursday': {
        'key': 'thursday', 'title': '霜陨领主',
        'day': '周四', 'time': '晚上 8:00', 'color': '#8b5cf6'
    },
    'saturday': {
        'key': 'saturday', 'title': '猎城战',
        'day': '周六', 'time': '晚上 8:00', 'color': '#3b82f6'
    }
}

sse_clients = set()
sse_lock = threading.Lock()


# ===== 数据库连接 =====
def get_db():
    if USE_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL)
        conn.cursor_factory = psycopg2.extras.RealDictCursor
        conn.autocommit = False
        return conn
    else:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn


def db_execute(conn, query, params=None):
    """执行查询，返回 cursor"""
    cur = conn.cursor()
    cur.execute(query, params or ())
    return cur


def db_fetchone(cur):
    if USE_POSTGRES:
        row = cur.fetchone()
        return dict(row) if row else None
    else:
        row = cur.fetchone()
        return dict(row) if row else None


def db_fetchall(cur):
    if USE_POSTGRES:
        return [dict(r) for r in cur.fetchall()]
    else:
        return [dict(r) for r in cur.fetchall()]


def db_now_ts():
    """获取当前时间戳的 SQL 表达式"""
    return "EXTRACT(EPOCH FROM NOW())" if USE_POSTGRES else "strftime('%s', 'now')"


# ===== 数据库初始化 =====
def init_db():
    conn = get_db()
    cur = conn.cursor()

    if USE_POSTGRES:
        cur.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS members (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                profession TEXT NOT NULL CHECK (profession IN ('战士','占卜家','窥秘人','观众（奶）','学徒','歌颂者')),
                attend_thursday INTEGER NOT NULL DEFAULT 0,
                attend_saturday INTEGER NOT NULL DEFAULT 0,
                remark TEXT DEFAULT '',
                created_at INTEGER NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW()),
                updated_at INTEGER NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())
            );

            CREATE TABLE IF NOT EXISTS archives (
                id SERIAL PRIMARY KEY,
                week_num INTEGER NOT NULL,
                member_name TEXT NOT NULL,
                profession TEXT NOT NULL,
                attend_thursday INTEGER NOT NULL DEFAULT 0,
                attend_saturday INTEGER NOT NULL DEFAULT 0,
                remark TEXT DEFAULT '',
                archived_at INTEGER NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())
            );

            CREATE INDEX IF NOT EXISTS idx_members_profession ON members(profession);
            CREATE INDEX IF NOT EXISTS idx_members_thursday ON members(attend_thursday);
            CREATE INDEX IF NOT EXISTS idx_members_saturday ON members(attend_saturday);
            CREATE INDEX IF NOT EXISTS idx_archives_week ON archives(week_num);
        ''')
    else:
        import os
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        cur.execute('PRAGMA journal_mode=DELETE')
        cur.executescript('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                profession TEXT NOT NULL CHECK (profession IN ('战士','占卜家','窥秘人','观众（奶）','学徒','歌颂者')),
                attend_thursday INTEGER NOT NULL DEFAULT 0,
                attend_saturday INTEGER NOT NULL DEFAULT 0,
                remark TEXT DEFAULT '',
                created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
                updated_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
            );

            CREATE TABLE IF NOT EXISTS archives (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                week_num INTEGER NOT NULL,
                member_name TEXT NOT NULL,
                profession TEXT NOT NULL,
                attend_thursday INTEGER NOT NULL DEFAULT 0,
                attend_saturday INTEGER NOT NULL DEFAULT 0,
                remark TEXT DEFAULT '',
                archived_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
            );

            CREATE INDEX IF NOT EXISTS idx_members_profession ON members(profession);
            CREATE INDEX IF NOT EXISTS idx_members_thursday ON members(attend_thursday);
            CREATE INDEX IF NOT EXISTS idx_members_saturday ON members(attend_saturday);
            CREATE INDEX IF NOT EXISTS idx_archives_week ON archives(week_num);
        ''')

    conn.commit()
    conn.close()
    print('✅ 数据库已就绪')


# ===== 工具函数 =====
def row_to_dict(row):
    if row is None:
        return None
    d = dict(row) if not isinstance(row, dict) else row
    d['attendThursday'] = bool(d.get('attend_thursday', 0))
    d['attendSaturday'] = bool(d.get('attend_saturday', 0))
    d.pop('attend_thursday', None)
    d.pop('attend_saturday', None)
    d['attendBoth'] = d['attendThursday'] and d['attendSaturday']
    return d


def broadcast(event, data):
    msg = f'event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'
    with sse_lock:
        dead = []
        for q in sse_clients:
            try:
                q.put(msg)
            except Exception:
                dead.append(q)
        for q in dead:
            sse_clients.discard(q)


# ===== 周数计算 =====
def get_current_week():
    now_ts = int(time.time())

    # 读设置
    conn = get_db()
    cur = db_execute(conn, "SELECT value FROM settings WHERE key = 'start_date'")
    row = db_fetchone(cur)

    if not row:
        # 初始化：以上一个周日为起点
        now = datetime.now()
        days_since_sunday = (now.weekday() + 1) % 7
        sunday = now - timedelta(days=days_since_sunday)
        sunday = sunday.replace(hour=0, minute=0, second=0, microsecond=0)
        start_date = int(sunday.timestamp())

        db_execute(conn, "INSERT INTO settings (key, value) VALUES ('start_date', %s)" if USE_POSTGRES else "INSERT INTO settings (key, value) VALUES (?, ?)",
                   (str(start_date),) if USE_POSTGRES else ('start_date', str(start_date)))
        conn.commit()
    else:
        start_date = int(row['value'])

    conn.close()

    week = 1 + (now_ts - start_date) // (7 * 24 * 3600)
    return week


def get_next_reset_time():
    now = datetime.now()
    days_until_sunday = (6 - now.weekday()) % 7
    next_sunday = now + timedelta(days=days_until_sunday)
    next_sunday = next_sunday.replace(hour=0, minute=0, second=0, microsecond=0)
    # 如果今天就是周日且还没过0点（理论不会发生，因为过了就进下一周了）
    if next_sunday <= now:
        next_sunday = next_sunday + timedelta(days=7)
    return int(next_sunday.timestamp())


# ===== 周日自动归档 =====
def do_weekly_reset():
    week = get_current_week()
    conn = get_db()

    # 1. 归档
    cur = db_execute(conn, 'SELECT * FROM members')
    members = db_fetchall(cur)

    for m in members:
        if USE_POSTGRES:
            db_execute(conn,
                '''INSERT INTO archives (week_num, member_name, profession, attend_thursday, attend_saturday, remark)
                   VALUES (%s, %s, %s, %s, %s, %s)''',
                (week, m['name'], m['profession'], m['attend_thursday'], m['attend_saturday'], m.get('remark', '')))
        else:
            db_execute(conn,
                '''INSERT INTO archives (week_num, member_name, profession, attend_thursday, attend_saturday, remark)
                   VALUES (?, ?, ?, ?, ?, ?)''',
                (week, m['name'], m['profession'], m['attend_thursday'], m['attend_saturday'], m.get('remark', '')))

    # 2. 清空
    db_execute(conn, 'DELETE FROM members')
    conn.commit()
    conn.close()

    new_week = week + 1
    print(f'[周重置] 第{week}周已归档，共{len(members)}人。进入第{new_week}周')
    broadcast('week_reset', {'week': new_week, 'archivedCount': len(members)})
    return new_week


def weekly_reset_scheduler():
    while True:
        try:
            next_reset = get_next_reset_time()
            now = int(time.time())
            sleep_time = next_reset - now
            check_interval = min(sleep_time, 3600)
            if check_interval <= 0:
                do_weekly_reset()
                continue
            time.sleep(check_interval)
            now = int(time.time())
            if now >= get_next_reset_time():
                do_weekly_reset()
        except Exception as e:
            print(f'[周重置调度] 出错: {e}')
            time.sleep(60)


def start_weekly_scheduler():
    t = threading.Thread(target=weekly_reset_scheduler, daemon=True)
    t.start()
    week = get_current_week()
    next_ts = get_next_reset_time()
    next_str = datetime.fromtimestamp(next_ts).strftime('%Y-%m-%d %H:%M')
    print(f'📅 当前第 {week} 周，下次重置: {next_str}')


# ========== API 路由 ==========

# SSE
@app.route('/api/stream')
def stream():
    import queue
    q = queue.Queue(maxsize=32)
    with sse_lock:
        sse_clients.add(q)

    def generate():
        try:
            week = get_current_week()
            next_reset = get_next_reset_time()
            yield f'event: connected\ndata: {json.dumps({"status":"ok","week":week,"nextReset":next_reset}, ensure_ascii=False)}\n\n'
            while True:
                try:
                    msg = q.get(timeout=30)
                    yield msg
                except queue.Empty:
                    yield ': ping\n\n'
        except GeneratorExit:
            pass
        finally:
            with sse_lock:
                sse_clients.discard(q)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache',
                             'X-Accel-Buffering': 'no'})


# 周信息
@app.route('/api/week')
def get_week_info():
    week = get_current_week()
    next_reset = get_next_reset_time()
    return jsonify({
        'week': week,
        'nextReset': next_reset,
        'nextResetStr': datetime.fromtimestamp(next_reset).strftime('%Y-%m-%d %H:%M')
    })


# 归档历史
@app.route('/api/archives')
def get_archives():
    week = request.args.get('week')
    conn = get_db()

    if week:
        cur = db_execute(conn,
            'SELECT * FROM archives WHERE week_num = %s ORDER BY member_name' if USE_POSTGRES
            else 'SELECT * FROM archives WHERE week_num = ? ORDER BY member_name',
            (int(week),))
        rows = db_fetchall(cur)
        members = []
        for r in rows:
            d = dict(r)
            d['attendThursday'] = bool(d.get('attend_thursday', 0))
            d['attendSaturday'] = bool(d.get('attend_saturday', 0))
            d['attendBoth'] = d['attendThursday'] and d['attendSaturday']
            d['name'] = d['member_name']
            members.append(d)
        conn.close()
        return jsonify({'week': int(week), 'members': members})
    else:
        cur = db_execute(conn,
            'SELECT week_num, COUNT(*) as count, MAX(archived_at) as archived_at FROM archives GROUP BY week_num ORDER BY week_num DESC')
        rows = db_fetchall(cur)
        result = []
        for r in rows:
            archived_at = int(r['archived_at'])
            result.append({
                'week': r['week_num'],
                'count': r['count'],
                'archivedAt': archived_at,
                'archivedAtStr': datetime.fromtimestamp(archived_at).strftime('%Y-%m-%d')
            })
        conn.close()
        return jsonify({'weeks': result})


# 活动配置
@app.route('/api/events')
def get_events():
    week = get_current_week()
    return jsonify({'events': list(EVENTS.values()), 'week': week})


# 管理员密码验证
@app.route('/api/admin/verify', methods=['POST'])
def admin_verify():
    data = request.get_json()
    pwd = data.get('password', '')
    if pwd == ADMIN_PASSWORD:
        return jsonify({'success': True})
    return jsonify({'error': '密码错误'}), 401


# 删除成员（需要管理员密码）
@app.route('/api/members/<int:member_id>', methods=['DELETE'])
def delete_member(member_id):
    data = request.get_json(silent=True) or {}
    pwd = data.get('password', '')
    if pwd != ADMIN_PASSWORD:
        return jsonify({'error': '无权限操作，请输入管理员密码'}), 403

    conn = get_db()
    cur = db_execute(conn,
        'SELECT * FROM members WHERE id = %s' if USE_POSTGRES else 'SELECT * FROM members WHERE id = ?',
        (member_id,))
    member = db_fetchone(cur)
    if not member:
        conn.close()
        return jsonify({'error': '成员不存在'}), 404

    db_execute(conn,
        'DELETE FROM members WHERE id = %s' if USE_POSTGRES else 'DELETE FROM members WHERE id = ?',
        (member_id,))
    conn.commit()
    conn.close()

    broadcast('member_removed', {'id': member_id})
    return jsonify({'success': True})


# 名单
@app.route('/api/members')
def get_all_members():
    event_key = request.args.get('event')
    conn = get_db()

    params = []
    where = 'WHERE 1=1'
    if event_key == 'thursday':
        where += ' AND attend_thursday = 1'
    elif event_key == 'saturday':
        where += ' AND attend_saturday = 1'

    # 按职业排序
    order = 'ORDER BY CASE profession '
    for i, p in enumerate(PROFESSIONS):
        order += f"WHEN '{p}' THEN {i} "
    order += "ELSE 99 END, name"

    cur = db_execute(conn, f'SELECT * FROM members {where} {order}', tuple(params))
    rows = db_fetchall(cur)
    members = [row_to_dict(r) for r in rows]

    totals = {
        'total': len(members),
        'thursday': sum(1 for m in members if m['attendThursday']),
        'saturday': sum(1 for m in members if m['attendSaturday']),
        'both': sum(1 for m in members if m['attendBoth']),
        'onlyThursday': sum(1 for m in members if m['attendThursday'] and not m['attendSaturday']),
        'onlySaturday': sum(1 for m in members if m['attendSaturday'] and not m['attendThursday']),
    }

    stats = {}
    for p in PROFESSIONS:
        pm = [m for m in members if m['profession'] == p]
        stats[p] = {
            'total': len(pm),
            'thursday': sum(1 for m in pm if m['attendThursday']),
            'saturday': sum(1 for m in pm if m['attendSaturday']),
            'both': sum(1 for m in pm if m['attendBoth']),
        }

    week = get_current_week()
    next_reset = get_next_reset_time()
    conn.close()

    return jsonify({
        'week': week, 'nextReset': next_reset,
        'members': members, 'totals': totals, 'stats': stats
    })


# 报名
@app.route('/api/signup', methods=['POST'])
def signup():
    data = request.get_json()
    name = (data.get('name') or '').strip()
    profession = data.get('profession', '')
    event_key = data.get('event', 'both')
    remark = (data.get('remark') or '').strip()

    if not name:
        return jsonify({'error': '请输入姓名'}), 400
    if profession not in PROFESSIONS:
        return jsonify({'error': '请选择正确的职业'}), 400
    if event_key not in ['thursday', 'saturday', 'both']:
        return jsonify({'error': '请选择参加的活动'}), 400

    thu = 1 if event_key in ('thursday', 'both') else 0
    sat = 1 if event_key in ('saturday', 'both') else 0

    conn = get_db()

    # 查找是否已存在
    cur = db_execute(conn,
        'SELECT * FROM members WHERE name = %s' if USE_POSTGRES else 'SELECT * FROM members WHERE name = ?',
        (name,))
    existing = db_fetchone(cur)

    if existing:
        new_thu = existing['attend_thursday'] or thu
        new_sat = existing['attend_saturday'] or sat
        now_expr = db_now_ts()
        if USE_POSTGRES:
            db_execute(conn,
                f'''UPDATE members SET profession=%s, attend_thursday=%s, attend_saturday=%s,
                   remark=%s, updated_at={now_expr} WHERE id=%s''',
                (profession, new_thu, new_sat, remark, existing['id']))
        else:
            db_execute(conn,
                f'''UPDATE members SET profession=?, attend_thursday=?, attend_saturday=?,
                   remark=?, updated_at={now_expr} WHERE id=?''',
                (profession, new_thu, new_sat, remark, existing['id']))
        member_id = existing['id']
        is_new = False
    else:
        now_expr = db_now_ts()
        if USE_POSTGRES:
            cur = db_execute(conn,
                f'''INSERT INTO members (name, profession, attend_thursday, attend_saturday, remark)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id''',
                (name, profession, thu, sat, remark))
            member_id = db_fetchone(cur)['id']
        else:
            cur = db_execute(conn,
                f'''INSERT INTO members (name, profession, attend_thursday, attend_saturday, remark)
                   VALUES (?, ?, ?, ?, ?)''',
                (name, profession, thu, sat, remark))
            member_id = cur.lastrowid
        is_new = True

    conn.commit()

    # 读回完整数据
    cur = db_execute(conn,
        'SELECT * FROM members WHERE id = %s' if USE_POSTGRES else 'SELECT * FROM members WHERE id = ?',
        (member_id,))
    member = row_to_dict(db_fetchone(cur))
    conn.close()

    broadcast('member_updated', {'member': member})
    return jsonify({'member': member, 'isNew': is_new})


# 批量导入（需要管理员密码）
@app.route('/api/batch', methods=['POST'])
def batch_import():
    data = request.get_json()
    text = data.get('text', '')
    default_event = data.get('defaultEvent', 'both')
    pwd = data.get('password', '')
    if pwd != ADMIN_PASSWORD:
        return jsonify({'error': '无权限操作，请输入管理员密码'}), 403

    lines = [l.strip() for l in text.split('\n') if l.strip()]
    if not lines:
        return jsonify({'error': '请输入名单内容'}), 400

    conn = get_db()
    imported = []
    errors = []
    now_expr = db_now_ts()

    for line in lines:
        try:
            parts = [p for p in re.split(r'[\s,，、;；\t]+', line) if p]
            if len(parts) < 2:
                errors.append(f'格式错误: {line}')
                continue

            name = parts[0]
            prof_input = parts[1]

            profession = None
            for p in PROFESSIONS:
                if p == prof_input or prof_input in p or prof_input in p.replace('（奶）', ''):
                    profession = p
                    break
            if not profession:
                errors.append(f'无法识别职业「{prof_input}」: {line}')
                continue

            thu = sat = 0
            if len(parts) >= 3:
                t = parts[2].lower()
                if t in ['1', '是', 'y', 'yes', 'true', '✓', '周四', '四'] or re.search(r'周[四4]', t):
                    thu = 1
                if t in ['1', '是', 'y', 'yes', 'true', '✓', '周六', '六', '双', '都'] or re.search(r'周[六6]', t):
                    sat = 1
            if len(parts) >= 4:
                t = parts[3].lower()
                if t in ['1', '是', 'y', 'yes', 'true', '✓', '周四', '四'] or re.search(r'周[四4]', t):
                    thu = 1
                if t in ['1', '是', 'y', 'yes', 'true', '✓', '周六', '六', '双', '都'] or re.search(r'周[六6]', t):
                    sat = 1

            if thu == 0 and sat == 0:
                if default_event == 'thursday':
                    thu = 1
                elif default_event == 'saturday':
                    sat = 1
                else:
                    thu = sat = 1

            # 查找现有
            cur = db_execute(conn,
                'SELECT * FROM members WHERE name = %s' if USE_POSTGRES else 'SELECT * FROM members WHERE name = ?',
                (name,))
            existing = db_fetchone(cur)

            if existing:
                new_thu = existing['attend_thursday'] or thu
                new_sat = existing['attend_saturday'] or sat
                if USE_POSTGRES:
                    db_execute(conn,
                        f'''UPDATE members SET profession=%s, attend_thursday=%s, attend_saturday=%s,
                           updated_at={now_expr} WHERE id=%s''',
                        (profession, new_thu, new_sat, existing['id']))
                else:
                    db_execute(conn,
                        f'''UPDATE members SET profession=?, attend_thursday=?, attend_saturday=?,
                           updated_at={now_expr} WHERE id=?''',
                        (profession, new_thu, new_sat, existing['id']))
                mid = existing['id']
            else:
                if USE_POSTGRES:
                    cur = db_execute(conn,
                        f'''INSERT INTO members (name, profession, attend_thursday, attend_saturday)
                           VALUES (%s, %s, %s, %s) RETURNING id''',
                        (name, profession, thu, sat))
                    mid = db_fetchone(cur)['id']
                else:
                    cur = db_execute(conn,
                        f'''INSERT INTO members (name, profession, attend_thursday, attend_saturday)
                           VALUES (?, ?, ?, ?)''',
                        (name, profession, thu, sat))
                    mid = cur.lastrowid

            cur = db_execute(conn,
                'SELECT * FROM members WHERE id = %s' if USE_POSTGRES else 'SELECT * FROM members WHERE id = ?',
                (mid,))
            imported.append(row_to_dict(db_fetchone(cur)))
        except Exception as e:
            errors.append(f'{line}: {str(e)}')

    conn.commit()
    conn.close()

    broadcast('batch_imported', {'count': len(imported)})
    return jsonify({
        'imported': len(imported),
        'totalLines': len(lines),
        'members': imported,
        'errors': errors
    })


# 导出
@app.route('/api/export')
def export_list():
    fmt = request.args.get('format', 'text')
    event_key = request.args.get('event', '')
    week = get_current_week()

    conn = get_db()
    cur = db_execute(conn, 'SELECT * FROM members ORDER BY name')
    rows = db_fetchall(cur)
    members = [row_to_dict(r) for r in rows]
    conn.close()

    if event_key == 'thursday':
        members = [m for m in members if m['attendThursday']]
    elif event_key == 'saturday':
        members = [m for m in members if m['attendSaturday']]

    if fmt == 'json':
        return jsonify({'week': week, 'members': members})

    lines = []
    lines.append(f'===== 第 {week} 周 活动报名名单 =====')
    lines.append('')

    thu_members = [m for m in members if m['attendThursday']]
    lines.append(f'【周四 · 霜陨领主】共 {len(thu_members)} 人')
    for p in PROFESSIONS:
        p_list = [m['name'] for m in thu_members if m['profession'] == p]
        if p_list:
            lines.append(f'  {p}（{len(p_list)}）：' + '、'.join(p_list))
    lines.append('')

    sat_members = [m for m in members if m['attendSaturday']]
    lines.append(f'【周六 · 猎城战】共 {len(sat_members)} 人')
    for p in PROFESSIONS:
        p_list = [m['name'] for m in sat_members if m['profession'] == p]
        if p_list:
            lines.append(f'  {p}（{len(p_list)}）：' + '、'.join(p_list))
    lines.append('')

    both = [m for m in members if m['attendBoth']]
    lines.append(f'【双场都参加】共 {len(both)} 人')
    lines.append('  ' + '、'.join(m['name'] for m in both) if both else '  无')
    lines.append('')

    lines.append(f'总计：{len(members)} 人（周四 {len(thu_members)} / 周六 {len(sat_members)} / 双场 {len(both)}）')

    content = '\n'.join(lines)
    return jsonify({'week': week, 'content': content})


# 页面路由
@app.route('/')
def index():
    return send_from_directory('public', 'index.html')

@app.route('/event/<event_key>')
def event_page(event_key):
    return send_from_directory('public', 'event.html')

@app.route('/all')
def all_page():
    return send_from_directory('public', 'all.html')

@app.route('/history')
def history_page():
    return send_from_directory('public', 'history.html')

@app.route('/api/health')
def health():
    return jsonify({'status': 'ok', 'time': int(time.time()), 'week': get_current_week(), 'db': 'postgres' if USE_POSTGRES else 'sqlite'})


# ===== 启动 =====
if __name__ == '__main__':
    init_db()
    _ = get_current_week()
    start_weekly_scheduler()

    db_type = 'Postgres' if USE_POSTGRES else 'SQLite'
    week = get_current_week()
    print()
    print('╔═══════════════════════════════════════════╗')
    print(f'║  诡秘之主 · 活动报名系统 v4.0    第 {str(week).ljust(2)}周    ║')
    print(f'║  数据库: {db_type.ljust(29)}║')
    print('╠═══════════════════════════════════════════╣')
    print('║  周四 · 霜陨领主    /event/thursday       ║')
    print('║  周六 · 猎城战      /event/saturday       ║')
    print('║  总名单             /all                  ║')
    print('║  历史档案           /history              ║')
    print(f'║  地址: http://localhost:{str(PORT).ljust(22)}║')
    print('║  每周日 00:00 自动归档重置                 ║')
    print('╚═══════════════════════════════════════════╝')
    print()
    try:
        import waitress
        print(f'使用 waitress 启动服务，端口: {PORT}')
        waitress.serve(app, host='0.0.0.0', port=PORT, threads=8)
    except ImportError:
        print(f'使用 Flask 开发服务器，端口: {PORT}')
        app.run(host='0.0.0.0', port=PORT, threaded=True)
