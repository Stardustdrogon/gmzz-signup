# 诡秘之主 活动报名系统 - 后端服务 (Python + SQLite)
# 固定两场活动：周四 霜陨领主 / 周六 猎城战
# 功能：成员报名、SSE 实时推送、批量导入、总名单、导出、周数管理、每周日自动归档重置

import sqlite3
import json
import time
import os
import re
import threading
import shutil
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

# 配置
DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(__file__), 'data', 'signup.db'))
BACKUP_DIR = os.environ.get('BACKUP_DIR', os.path.join(os.path.dirname(__file__), 'data', 'backups'))
PORT = int(os.environ.get('PORT', 3000))

# 职业列表
PROFESSIONS = ['战士', '占卜家', '窥秘人', '观众（奶）', '学徒', '歌颂者']

# 活动配置
EVENTS = {
    'thursday': {
        'key': 'thursday',
        'title': '霜陨领主',
        'day': '周四',
        'time': '晚上 8:00',
        'color': '#8b5cf6'
    },
    'saturday': {
        'key': 'saturday',
        'title': '猎城战',
        'day': '周六',
        'time': '晚上 8:00',
        'color': '#3b82f6'
    }
}

# SSE 客户端集合
sse_clients = set()
sse_lock = threading.Lock()


# ===== 周数计算 =====
def get_current_week():
    """计算当前是第几周（以第一周周日0点为起点，每7天+1）"""
    conn = get_db()
    row = conn.execute('SELECT value FROM settings WHERE key = ?', ('start_week',)).fetchone()
    conn.close()

    if row:
        start_week = int(row['value'])
    else:
        # 默认从第1周开始
        start_week = 1
        conn = get_db()
        conn.execute("INSERT INTO settings (key, value) VALUES ('start_week', '1')")
        conn.commit()
        conn.close()

    # 获取基准日期（start_date）
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key = 'start_date'").fetchone()
    conn.close()

    if row:
        start_date = int(row['value'])
    else:
        # 以最近一个周日0点为基准
        now = datetime.now()
        # 找到上一个周日
        days_since_sunday = now.weekday() + 1  # 周日=0...周六=6 -> weekday周一=0,周日=6
        if days_since_sunday >= 7:
            days_since_sunday = 0
        sunday = now - timedelta(days=days_since_sunday)
        sunday = sunday.replace(hour=0, minute=0, second=0, microsecond=0)
        start_date = int(sunday.timestamp())
        conn = get_db()
        conn.execute("INSERT INTO settings (key, value) VALUES ('start_date', ?)", (str(start_date),))
        conn.commit()
        conn.close()

    now_ts = int(time.time())
    week = start_week + (now_ts - start_date) // (7 * 24 * 3600)
    return week


def get_next_reset_time():
    """计算下一个周日 00:00 的时间戳"""
    now = datetime.now()
    days_until_sunday = (6 - now.weekday()) % 7  # 周日=6
    if days_until_sunday == 0 and now.hour >= 0:
        # 如果今天是周日且已经过了0点，那下一个周日是7天后
        if now.hour > 0 or now.minute > 0 or now.second > 0:
            days_until_sunday = 7

    next_sunday = now + timedelta(days=days_until_sunday)
    next_sunday = next_sunday.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(next_sunday.timestamp())


# ===== 数据库初始化 =====
def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=DELETE')
    conn.executescript('''
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
    print(f'✅ 数据库已就绪: {DB_PATH}')


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def row_to_dict(row):
    if row is None:
        return None
    d = dict(row)
    d['attendThursday'] = bool(d.get('attend_thursday', 0))
    d['attendSaturday'] = bool(d.get('attend_saturday', 0))
    d.pop('attend_thursday', None)
    d.pop('attend_saturday', None)
    d['attendBoth'] = d['attendThursday'] and d['attendSaturday']
    return d


# ===== SSE 工具 =====
def broadcast(event, data):
    """向所有连接推送消息"""
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


# ===== 周日自动归档 & 重置 =====
def do_weekly_reset():
    """执行周归档：把当前名单存到archives，清空members，周数+1"""
    week = get_current_week()
    conn = get_db()

    # 1. 把当前成员归档到 archives
    members = conn.execute('SELECT * FROM members').fetchall()
    for m in members:
        conn.execute(
            '''INSERT INTO archives (week_num, member_name, profession, attend_thursday, attend_saturday, remark)
               VALUES (?, ?, ?, ?, ?, ?)''',
            (week, m['name'], m['profession'], m['attend_thursday'], m['attend_saturday'], m['remark'])
        )

    # 2. 备份数据库文件
    backup_name = f'week_{week}_{datetime.now().strftime("%Y%m%d")}.db'
    backup_path = os.path.join(BACKUP_DIR, backup_name)
    try:
        # 先关闭连接再备份
        conn.commit()
        conn.close()
        shutil.copy2(DB_PATH, backup_path)
        conn = get_db()
    except Exception as e:
        print(f'[备份] 文件备份失败: {e}')
        conn = get_db()

    # 3. 清空当前成员
    conn.execute('DELETE FROM members')
    conn.commit()
    conn.close()

    new_week = week + 1
    print(f'[周重置] 第{week}周已归档，共{len(members)}人。进入第{new_week}周')

    # 推送通知
    broadcast('week_reset', {'week': new_week, 'archivedCount': len(members)})

    return new_week


def weekly_reset_scheduler():
    """后台线程：检测是否到了周日零点，执行归档重置"""
    while True:
        try:
            next_reset = get_next_reset_time()
            now = int(time.time())
            sleep_time = next_reset - now

            if sleep_time <= 0:
                # 已经过了，立即执行（理论上不会发生）
                do_weekly_reset()
                continue

            # 每隔一小时检查一次，避免时间漂移
            check_interval = min(sleep_time, 3600)
            time.sleep(check_interval)

            # 重新计算，确认真的到点了
            now = int(time.time())
            if now >= get_next_reset_time():
                do_weekly_reset()
        except Exception as e:
            print(f'[周重置调度] 出错: {e}')
            time.sleep(60)


def start_weekly_scheduler():
    """启动周重置后台线程"""
    t = threading.Thread(target=weekly_reset_scheduler, daemon=True)
    t.start()
    week = get_current_week()
    next_ts = get_next_reset_time()
    next_str = datetime.fromtimestamp(next_ts).strftime('%Y-%m-%d %H:%M')
    print(f'📅 当前第 {week} 周，下次重置: {next_str}')


# ===== SSE 流 =====
@app.route('/api/stream')
def stream():
    import queue
    q = queue.Queue(maxsize=32)
    with sse_lock:
        sse_clients.add(q)

    def generate():
        try:
            # 发送初始化事件，带上周数
            week = get_current_week()
            next_reset = get_next_reset_time()
            yield f'event: connected\ndata: {json.dumps({"status":"ok","week":week,"nextReset":next_reset}, ensure_ascii=False)}\n\n'
            # 心跳
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
                             'X-Accel-Buffering': 'no',
                             'Connection': 'keep-alive'})


# ===== 周信息 =====
@app.route('/api/week')
def get_week_info():
    week = get_current_week()
    next_reset = get_next_reset_time()
    return jsonify({
        'week': week,
        'nextReset': next_reset,
        'nextResetStr': datetime.fromtimestamp(next_reset).strftime('%Y-%m-%d %H:%M')
    })


# ===== 归档历史 =====
@app.route('/api/archives')
def get_archives():
    week = request.args.get('week')
    conn = get_db()

    if week:
        rows = conn.execute(
            'SELECT * FROM archives WHERE week_num = ? ORDER BY member_name',
            (int(week),)
        ).fetchall()
    else:
        # 返回所有周的列表
        rows = conn.execute(
            'SELECT week_num, COUNT(*) as count, MAX(archived_at) as archived_at FROM archives GROUP BY week_num ORDER BY week_num DESC'
        ).fetchall()
        result = []
        for r in rows:
            result.append({
                'week': r['week_num'],
                'count': r['count'],
                'archivedAt': r['archived_at'],
                'archivedAtStr': datetime.fromtimestamp(r['archived_at']).strftime('%Y-%m-%d')
            })
        conn.close()
        return jsonify({'weeks': result})

    members = []
    for r in rows:
        d = dict(r)
        d['attendThursday'] = bool(d['attend_thursday'])
        d['attendSaturday'] = bool(d['attend_saturday'])
        d['attendBoth'] = d['attendThursday'] and d['attendSaturday']
        d['name'] = d['member_name']
        d.pop('attend_thursday', None)
        d.pop('attend_saturday', None)
        d.pop('member_name', None)
        members.append(d)

    conn.close()
    return jsonify({'week': int(week) if week else 0, 'members': members})


# ===== 活动配置 =====
@app.route('/api/events')
def get_events():
    week = get_current_week()
    return jsonify({'events': list(EVENTS.values()), 'week': week})


# ===== 获取总名单 =====
@app.route('/api/members')
def get_all_members():
    event_key = request.args.get('event')

    conn = get_db()
    query = 'SELECT * FROM members WHERE 1=1'
    params = []

    if event_key == 'thursday':
        query += ' AND attend_thursday = 1'
    elif event_key == 'saturday':
        query += ' AND attend_saturday = 1'

    query += ' ORDER BY '
    query += "CASE profession "
    for i, p in enumerate(PROFESSIONS):
        query += f"WHEN '{p}' THEN {i} "
    query += "ELSE 99 END, name"

    rows = conn.execute(query, params).fetchall()
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
        p_members = [m for m in members if m['profession'] == p]
        stats[p] = {
            'total': len(p_members),
            'thursday': sum(1 for m in p_members if m['attendThursday']),
            'saturday': sum(1 for m in p_members if m['attendSaturday']),
            'both': sum(1 for m in p_members if m['attendBoth']),
        }

    week = get_current_week()
    next_reset = get_next_reset_time()
    conn.close()

    return jsonify({
        'week': week,
        'nextReset': next_reset,
        'members': members,
        'totals': totals,
        'stats': stats
    })


# ===== 报名 =====
@app.route('/api/signup', methods=['POST'])
def signup():
    data = request.get_json()
    name = (data.get('name') or '').strip()
    profession = data.get('profession', '')
    event_key = data.get('event', 'both')  # thursday / saturday / both
    remark = (data.get('remark') or '').strip()

    if not name:
        return jsonify({'error': '请输入姓名'}), 400
    if profession not in PROFESSIONS:
        return jsonify({'error': '请选择正确的职业'}), 400
    if event_key not in ['thursday', 'saturday', 'both']:
        return jsonify({'error': '请选择参加的活动'}), 400

    conn = get_db()
    existing = conn.execute('SELECT * FROM members WHERE name = ?', (name,)).fetchone()

    if existing:
        thu = 1 if (existing['attend_thursday'] or event_key in ('thursday', 'both')) else 0
        sat = 1 if (existing['attend_saturday'] or event_key in ('saturday', 'both')) else 0
        conn.execute(
            '''UPDATE members SET profession = ?, attend_thursday = ?, attend_saturday = ?,
               remark = ?, updated_at = strftime('%s', 'now') WHERE id = ?''',
            (profession, thu, sat, remark, existing['id'])
        )
        member_id = existing['id']
    else:
        thu = 1 if event_key in ('thursday', 'both') else 0
        sat = 1 if event_key in ('saturday', 'both') else 0
        cur = conn.execute(
            '''INSERT INTO members (name, profession, attend_thursday, attend_saturday, remark)
               VALUES (?, ?, ?, ?, ?)''',
            (name, profession, thu, sat, remark)
        )
        member_id = cur.lastrowid

    conn.commit()
    member = row_to_dict(conn.execute('SELECT * FROM members WHERE id = ?', (member_id,)).fetchone())
    conn.close()

    broadcast('member_updated', {'member': member})
    return jsonify({'member': member, 'isNew': existing is None})


# ===== 取消报名某场 =====
@app.route('/api/members/<int:member_id>/cancel', methods=['POST'])
def cancel_member(member_id):
    data = request.get_json()
    event_key = data.get('event')

    if event_key not in ['thursday', 'saturday']:
        return jsonify({'error': '无效的活动'}), 400

    conn = get_db()
    member = conn.execute('SELECT * FROM members WHERE id = ?', (member_id,)).fetchone()
    if not member:
        conn.close()
        return jsonify({'error': '成员不存在'}), 404

    if event_key == 'thursday':
        conn.execute('UPDATE members SET attend_thursday = 0, updated_at = strftime("%s", "now") WHERE id = ?', (member_id,))
    else:
        conn.execute('UPDATE members SET attend_saturday = 0, updated_at = strftime("%s", "now") WHERE id = ?', (member_id,))

    conn.commit()

    updated = conn.execute('SELECT * FROM members WHERE id = ?', (member_id,)).fetchone()
    if updated['attend_thursday'] == 0 and updated['attend_saturday'] == 0:
        conn.execute('DELETE FROM members WHERE id = ?', (member_id,))
        conn.commit()
        conn.close()
        broadcast('member_removed', {'id': member_id})
        return jsonify({'removed': True})

    member_dict = row_to_dict(updated)
    conn.close()
    broadcast('member_updated', {'member': member_dict})
    return jsonify({'member': member_dict, 'removed': False})


# ===== 删除成员 =====
@app.route('/api/members/<int:member_id>', methods=['DELETE'])
def delete_member(member_id):
    conn = get_db()
    member = conn.execute('SELECT * FROM members WHERE id = ?', (member_id,)).fetchone()
    if not member:
        conn.close()
        return jsonify({'error': '成员不存在'}), 404

    conn.execute('DELETE FROM members WHERE id = ?', (member_id,))
    conn.commit()
    conn.close()

    broadcast('member_removed', {'id': member_id})
    return jsonify({'success': True})


# ===== 批量导入 =====
@app.route('/api/batch', methods=['POST'])
def batch_import():
    data = request.get_json()
    text = data.get('text', '')
    default_event = data.get('defaultEvent', 'both')

    lines = [l.strip() for l in text.split('\n') if l.strip()]
    if not lines:
        return jsonify({'error': '请输入名单内容'}), 400

    conn = get_db()
    imported = []
    errors = []

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

            existing = conn.execute('SELECT * FROM members WHERE name = ?', (name,)).fetchone()
            if existing:
                new_thu = existing['attend_thursday'] or thu
                new_sat = existing['attend_saturday'] or sat
                conn.execute(
                    '''UPDATE members SET profession = ?, attend_thursday = ?, attend_saturday = ?,
                       updated_at = strftime('%s', 'now') WHERE id = ?''',
                    (profession, new_thu, new_sat, existing['id'])
                )
                row = conn.execute('SELECT * FROM members WHERE id = ?', (existing['id'],)).fetchone()
            else:
                cur = conn.execute(
                    '''INSERT INTO members (name, profession, attend_thursday, attend_saturday)
                       VALUES (?, ?, ?, ?)''',
                    (name, profession, thu, sat)
                )
                row = conn.execute('SELECT * FROM members WHERE id = ?', (cur.lastrowid,)).fetchone()

            imported.append(row_to_dict(row))
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


# ===== 导出名单 =====
@app.route('/api/export')
def export_list():
    fmt = request.args.get('format', 'text')
    event_key = request.args.get('event', '')
    week = get_current_week()

    conn = get_db()
    rows = conn.execute('SELECT * FROM members ORDER BY name').fetchall()
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


# ===== 页面路由 =====
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
    return jsonify({'status': 'ok', 'time': int(time.time()), 'week': get_current_week()})


# ===== 启动 =====
if __name__ == '__main__':
    init_db()
    week = get_current_week()  # 确保 settings 初始化
    start_weekly_scheduler()
    print()
    print('╔═══════════════════════════════════════════╗')
    print(f'║  诡秘之主 · 活动报名系统 v3.0    第 {str(week).ljust(2)}周    ║')
    print('╠═══════════════════════════════════════════╣')
    print('║  周四 · 霜陨领主    /event/thursday       ║')
    print('║  周六 · 猎城战      /event/saturday       ║')
    print('║  总名单             /all                  ║')
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
