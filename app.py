# 诡秘之主 活动报名系统 - 后端服务
# 支持 SQLite（默认）和 Postgres（DATABASE_URL 环境变量时自动切换）
# 固定两场活动：周四 霜陨领主 19:00 / 周六 猎城战 19:00
# 报名截止：活动当天 17:30
# 功能：成员报名、SSE 实时推送、批量导入、总名单、Excel导出、周数管理、每周日自动重置

import json
import time
import os
import re
import threading
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, Response, send_from_directory, send_file
from flask_cors import CORS
from io import BytesIO

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
DATABASE_URL = os.environ.get('DATABASE_URL', '')
USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras
    print('🐘 使用 Postgres 数据库')
else:
    import sqlite3
    DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(__file__), 'data', 'signup.db'))
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    print('📦 使用 SQLite 数据库')

PORT = int(os.environ.get('PORT', 3000))
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')

PROFESSIONS = ['战士', '占卜家', '窥秘人', '观众（奶）', '学徒', '歌颂者']
PROF_COLORS = {
    '战士': 'EF4444',
    '占卜家': '8B5CF6',
    '窥秘人': '6366F1',
    '观众（奶）': '22C55E',
    '学徒': 'F59E0B',
    '歌颂者': 'EC4899',
}

EVENTS = {
    'thursday': {
        'key': 'thursday', 'title': '霜陨领主',
        'day': '周四', 'time': '晚上 7:00', 'color': '#8b5cf6',
        'weekday': 3  # 0=周一, 3=周四
    },
    'saturday': {
        'key': 'saturday', 'title': '猎城战',
        'day': '周六', 'time': '晚上 7:00', 'color': '#3b82f6',
        'weekday': 5  # 5=周六
    }
}

# 报名截止时间：活动当天 17:30
DEADLINE_HOUR = 17
DEADLINE_MINUTE = 30

# SSE 相关
sse_clients = set()
sse_lock = threading.Lock()

# Excel 存储目录
EXCEL_DIR = os.environ.get('EXCEL_DIR', os.path.join(os.path.dirname(__file__), 'data', 'excel'))
os.makedirs(EXCEL_DIR, exist_ok=True)

# ========== 数据库工具函数 ==========
def get_db():
    if USE_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL)
        conn.cursor_factory = psycopg2.extras.RealDictCursor
        return conn
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

def db_execute(conn, sql, params=()):
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur

def db_fetchone(cur):
    return cur.fetchone()

def db_fetchall(cur):
    return cur.fetchall()

def db_now_ts():
    return 'EXTRACT(EPOCH FROM NOW())' if USE_POSTGRES else "strftime('%s', 'now')"

def row_to_dict(row):
    if row is None:
        return None
    d = dict(row)
    d['attendThursday'] = bool(d.get('attend_thursday', 0))
    d['attendSaturday'] = bool(d.get('attend_saturday', 0))
    d['attendBoth'] = d['attendThursday'] and d['attendSaturday']
    # 兼容字段名
    if 'member_name' in d and 'name' not in d:
        d['name'] = d['member_name']
    return d

# ========== 初始化数据库 ==========
def init_db():
    conn = get_db()
    cur = db_execute(conn, '''
        CREATE TABLE IF NOT EXISTS members (
            id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
            name TEXT NOT NULL UNIQUE,
            profession TEXT NOT NULL,
            attend_thursday INTEGER NOT NULL DEFAULT 0,
            attend_saturday INTEGER NOT NULL DEFAULT 0,
            remark TEXT DEFAULT '',
            created_at BIGINT NOT NULL DEFAULT 0,
            updated_at BIGINT NOT NULL DEFAULT 0
        )
    ''' if USE_POSTGRES else '''
        CREATE TABLE IF NOT EXISTS members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            profession TEXT NOT NULL,
            attend_thursday INTEGER NOT NULL DEFAULT 0,
            attend_saturday INTEGER NOT NULL DEFAULT 0,
            remark TEXT DEFAULT '',
            created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
            updated_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
        )
    ''')
    conn.commit()
    conn.close()
    print('✅ 数据库已就绪')

# ========== 周数与截止时间 ==========
def get_current_week():
    """计算当前是第几周（从起始周日算起）"""
    now_ts = time.time()
    # 找到最近一个周日 00:00 作为基准
    now = datetime.fromtimestamp(now_ts)
    # weekday(): 周一=0, 周日=6
    days_since_sunday = (now.weekday() + 1) % 7
    this_sunday = (now - timedelta(days=days_since_sunday, hours=now.hour,
                                    minutes=now.minute, seconds=now.second)).replace(hour=0, minute=0, second=0, microsecond=0)
    start_sunday_ts = os.environ.get('START_SUNDAY_TS', '')
    if start_sunday_ts:
        start_ts = int(start_sunday_ts)
    else:
        # 默认：从 2026-08-23 周日开始算第1周
        start_ts = int(datetime(2026, 8, 23, 0, 0, 0).timestamp())
    diff_days = (this_sunday.timestamp() - start_ts) / 86400
    week = int(diff_days / 7) + 1
    return max(1, week)

def get_next_reset_time():
    """下次周日 00:00 的时间戳"""
    now = datetime.now()
    days_until_sunday = (6 - now.weekday()) % 7  # 6=周日
    if days_until_sunday == 0 and (now.hour, now.minute) >= (0, 0):
        days_until_sunday = 7
    next_sunday = (now + timedelta(days=days_until_sunday)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return int(next_sunday.timestamp())

def get_deadline_info(event_key):
    """获取指定活动的下次截止时间和活动时间"""
    event = EVENTS[event_key]
    target_weekday = event['weekday']
    now = datetime.now()
    days_ahead = (target_weekday - now.weekday() + 7) % 7

    # 如果今天就是活动日但已过截止时间，算到下周
    if days_ahead == 0:
        if now.hour > DEADLINE_HOUR or (now.hour == DEADLINE_HOUR and now.minute >= DEADLINE_MINUTE):
            days_ahead = 7

    deadline_date = (now + timedelta(days=days_ahead)).replace(
        hour=DEADLINE_HOUR, minute=DEADLINE_MINUTE, second=0, microsecond=0)
    event_date = deadline_date + timedelta(hours=1, minutes=30)  # 19:00

    return {
        'deadline_ts': int(deadline_date.timestamp()),
        'deadline_str': deadline_date.strftime('%m-%d %H:%M'),
        'event_time_str': event_date.strftime('%m-%d %H:%M'),
        'is_closed': days_ahead == 0  # 今天是活动日且已过截止
    }

def is_signup_closed(event_key):
    """判断指定活动报名是否已截止"""
    info = get_deadline_info(event_key)
    return info['is_closed']

# ========== SSE 广播 ==========
def broadcast(event_name, data):
    msg = f'event: {event_name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'
    with sse_lock:
        dead = set()
        for q in sse_clients:
            try:
                q.put_nowait(msg)
            except:
                dead.add(q)
        for q in dead:
            sse_clients.discard(q)

# ========== Excel 生成 ==========
def generate_excel(week_num):
    """生成指定周的 Excel 文件，返回文件路径"""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    conn = get_db()
    rows = db_fetchall(db_execute(conn, 'SELECT * FROM members ORDER BY name'))
    members = [row_to_dict(r) for r in rows]
    conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = f'第{week_num}周'

    # 样式定义
    header_font = Font(name='微软雅黑', size=11, bold=True, color='FFFFFF')
    cell_font = Font(name='微软雅黑', size=10)
    center_align = Alignment(horizontal='center', vertical='center')
    thin_border = Border(
        left=Side(style='thin', color='D0D0D0'),
        right=Side(style='thin', color='D0D0D0'),
        top=Side(style='thin', color='D0D0D0'),
        bottom=Side(style='thin', color='D0D0D0'),
    )

    # 按职业分组
    prof_members = {}
    for p in PROFESSIONS:
        prof_members[p] = [m for m in members if m['profession'] == p and (m['attendThursday'] or m['attendSaturday'])]

    # 标题行：职业名 | 非凡评分（保留列但没数据，参考原表排版）
    headers = []
    for p in PROFESSIONS:
        headers.append(p)
        headers.append('')  # 原表有评分列，这里留空保持排版
    ws.append(headers)

    # 表头样式
    for col_idx, p in enumerate(PROFESSIONS):
        name_col = col_idx * 2 + 1
        score_col = col_idx * 2 + 2
        color = PROF_COLORS[p]
        fill = PatternFill(start_color=color, end_color=color, fill_type='solid')

        name_cell = ws.cell(row=1, column=name_col, value=p)
        name_cell.font = header_font
        name_cell.fill = fill
        name_cell.alignment = center_align
        name_cell.border = thin_border

        score_cell = ws.cell(row=1, column=score_col, value='')
        score_cell.fill = fill
        score_cell.border = thin_border

    # 填充成员，最多列数作为行数
    max_count = max(len(v) for v in prof_members.values()) if prof_members else 0

    for row_idx in range(max_count):
        row_data = []
        for p in PROFESSIONS:
            plist = prof_members[p]
            if row_idx < len(plist):
                m = plist[row_idx]
                # 名字后面标参加场次
                tags = []
                if m['attendThursday']:
                    tags.append('四')
                if m['attendSaturday']:
                    tags.append('六')
                display_name = m['name']
                if len(tags) == 1:
                    display_name += f'（{tags[0]}）'
                row_data.append(display_name)
                row_data.append('')  # 评分列留空
            else:
                row_data.append('')
                row_data.append('')
        ws.append(row_data)

    # 设置列宽和边框
    for col_idx in range(1, len(PROFESSIONS) * 2 + 1):
        col_letter = get_column_letter(col_idx)
        if col_idx % 2 == 1:
            ws.column_dimensions[col_letter].width = 15  # 名字列
        else:
            ws.column_dimensions[col_letter].width = 8  # 空列

    for row in ws.iter_rows(min_row=1, max_row=max_count + 1, min_col=1, max_col=len(PROFESSIONS) * 2):
        for cell in row:
            cell.alignment = center_align
            cell.border = thin_border
            if cell.row > 1:
                cell.font = cell_font

    # 行高
    ws.row_dimensions[1].height = 28
    for r in range(2, max_count + 2):
        ws.row_dimensions[r].height = 22

    # 保存文件
    filename = f'第{week_num}周报名名单.xlsx'
    filepath = os.path.join(EXCEL_DIR, filename)
    wb.save(filepath)
    return filepath, filename

# ========== 每周重置 ==========
def do_weekly_reset():
    """周日0点执行：生成Excel归档，清空名单，进入新一周"""
    old_week = get_current_week()

    # 生成 Excel 归档
    try:
        filepath, filename = generate_excel(old_week)
        print(f'[周重置] Excel已生成: {filename}')
    except Exception as e:
        print(f'[周重置] Excel生成失败: {e}')

    # 清空当前名单
    conn = get_db()
    db_execute(conn, 'DELETE FROM members')
    conn.commit()
    conn.close()

    new_week = old_week + 1
    print(f'[周重置] 第{old_week}周已归档，进入第{new_week}周')

    broadcast('week_reset', {'newWeek': new_week, 'message': f'进入第 {new_week} 周，名单已重置'})
    return new_week

# 周重置调度器（每小时检查一次，到点执行）
def weekly_reset_scheduler():
    last_reset_week = 0
    while True:
        try:
            current_week = get_current_week()
            if current_week != last_reset_week and last_reset_week > 0:
                # 周数变了，执行重置
                do_weekly_reset()
            last_reset_week = current_week
        except Exception as e:
            print(f'[周调度] 错误: {e}')
        time.sleep(3600)  # 每小时检查一次

# ========== 启动 ==========
def start_server():
    init_db()

    # 启动周重置调度线程
    t = threading.Thread(target=weekly_reset_scheduler, daemon=True)
    t.start()
    week = get_current_week()
    next_ts = get_next_reset_time()
    next_str = datetime.fromtimestamp(next_ts).strftime('%Y-%m-%d %H:%M')
    print(f'📅 当前第 {week} 周，下次重置: {next_str}')

    # 用 waitress 启动生产服务器
    try:
        from waitress import serve
        print(f'🚀 服务启动于 http://0.0.0.0:{PORT}')
        serve(app, host='0.0.0.0', port=PORT, threads=16)
    except ImportError:
        print(f'🚀 (开发模式) 服务启动于 http://0.0.0.0:{PORT}')
        app.run(host='0.0.0.0', port=PORT, debug=False)

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
            thu_info = get_deadline_info('thursday')
            sat_info = get_deadline_info('saturday')
            yield f'event: connected\ndata: {json.dumps({"status":"ok","week":week,"nextReset":next_reset,"thursdayDeadline":thu_info["deadline_ts"],"saturdayDeadline":sat_info["deadline_ts"]}, ensure_ascii=False)}\n\n'
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


# 周信息 + 截止时间
@app.route('/api/week')
def get_week_info():
    week = get_current_week()
    next_reset = get_next_reset_time()
    thu_info = get_deadline_info('thursday')
    sat_info = get_deadline_info('saturday')
    # 本周日期范围（周日到周六）
    now = datetime.now()
    days_since_sunday = (now.weekday() + 1) % 7
    this_sunday = (now - timedelta(days=days_since_sunday)).replace(hour=0, minute=0, second=0, microsecond=0)
    this_saturday = this_sunday + timedelta(days=6)
    week_range_str = this_sunday.strftime('%m/%d') + '-' + this_saturday.strftime('%m/%d')
    return jsonify({
        'week': week,
        'weekRange': week_range_str,
        'weekLabel': f'第 {week} 周（{week_range_str}）',
        'nextReset': next_reset,
        'nextResetStr': datetime.fromtimestamp(next_reset).strftime('%Y-%m-%d %H:%M'),
        'thursdayDeadline': thu_info['deadline_ts'],
        'thursdayDeadlineStr': thu_info['deadline_str'],
        'saturdayDeadline': sat_info['deadline_ts'],
        'saturdayDeadlineStr': sat_info['deadline_str'],
    })


# 活动配置
@app.route('/api/events')
def get_events():
    week = get_current_week()
    thu_info = get_deadline_info('thursday')
    sat_info = get_deadline_info('saturday')
    events = list(EVENTS.values())
    # 加上截止时间信息
    for e in events:
        info = get_deadline_info(e['key'])
        e['deadline'] = info['deadline_ts']
        e['deadlineStr'] = info['deadline_str']
        e['eventTimeStr'] = info['event_time_str']
        e['isClosed'] = info['is_closed']
    return jsonify({'events': events, 'week': week})


# 名单
@app.route('/api/members')
def get_all_members():
    event_key = request.args.get('event')
    conn = get_db()

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

    cur = db_execute(conn, f'SELECT * FROM members {where} {order}')
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
    if event_key not in ('thursday', 'saturday', 'both'):
        return jsonify({'error': '无效的活动选择'}), 400

    # 截止时间校验（双场时只报没截止的那场）
    thu_closed = is_signup_closed('thursday')
    sat_closed = is_signup_closed('saturday')

    if event_key == 'thursday' and thu_closed:
        return jsonify({'error': '周四活动报名已截止'}), 403
    if event_key == 'saturday' and sat_closed:
        return jsonify({'error': '周六活动报名已截止'}), 403
    if event_key == 'both' and thu_closed and sat_closed:
        return jsonify({'error': '两场活动报名均已截止'}), 403

    # 计算实际报名场次（已截止的就不报了）
    if event_key == 'both':
        thu = 0 if thu_closed else 1
        sat = 0 if sat_closed else 1
        if thu == 0 and sat == 0:
            return jsonify({'error': '两场活动报名均已截止'}), 403
    else:
        thu = 1 if event_key == 'thursday' else 0
        sat = 1 if event_key == 'saturday' else 0

    conn = get_db()
    now_expr = db_now_ts()

    # 检查是否已存在
    cur = db_execute(conn,
        'SELECT * FROM members WHERE name = %s' if USE_POSTGRES else 'SELECT * FROM members WHERE name = ?',
        (name,))
    existing = db_fetchone(cur)
    is_new = not existing

    if is_new:
        # 新成员（thu/sat 已在前面计算好）
        cur = db_execute(conn,
            'INSERT INTO members (name, profession, attend_thursday, attend_saturday, remark, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, ' + now_expr + ', ' + now_expr + ') RETURNING id' if USE_POSTGRES
            else 'INSERT INTO members (name, profession, attend_thursday, attend_saturday, remark) VALUES (?, ?, ?, ?, ?)',
            (name, profession, thu, sat, remark))
        if USE_POSTGRES:
            member_id = cur.fetchone()['id']
        else:
            member_id = cur.lastrowid
    else:
        # 更新成员：合并场次选择（只能加不能减，且已截止的场次不能加）
        e = row_to_dict(existing)
        new_thu = e['attendThursday']
        new_sat = e['attendSaturday']
        if event_key == 'thursday' and not thu_closed:
            new_thu = True
        elif event_key == 'saturday' and not sat_closed:
            new_sat = True
        elif event_key == 'both':
            if not thu_closed:
                new_thu = True
            if not sat_closed:
                new_sat = True
        thu = 1 if new_thu else 0
        sat = 1 if new_sat else 0

        cur = db_execute(conn,
            'UPDATE members SET profession = %s, attend_thursday = %s, attend_saturday = %s, remark = %s, updated_at = ' + now_expr + ' WHERE id = %s' if USE_POSTGRES
            else 'UPDATE members SET profession = ?, attend_thursday = ?, attend_saturday = ?, remark = ?, updated_at = ' + now_expr + ' WHERE id = ?',
            (profession, 1 if thu else 0, 1 if sat else 0, remark, existing['id']))
        member_id = existing['id']

    conn.commit()

    # 读回完整数据
    cur = db_execute(conn,
        'SELECT * FROM members WHERE id = %s' if USE_POSTGRES else 'SELECT * FROM members WHERE id = ?',
        (member_id,))
    member = row_to_dict(db_fetchone(cur))
    conn.close()

    broadcast('member_updated', {'member': member})
    return jsonify({'member': member, 'isNew': is_new})


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

            # 职业模糊匹配
            profession = None
            for p in PROFESSIONS:
                if p == prof_input or prof_input in p or prof_input in p.replace('（奶）', ''):
                    profession = p
                    break
            if not profession:
                errors.append(f'职业不匹配: {prof_input}')
                continue

            # 解析场次：第三第四列
            thu = sat = 0
            if default_event == 'both':
                thu = sat = 1
            elif default_event == 'thursday':
                thu = 1
            elif default_event == 'saturday':
                sat = 1

            if len(parts) >= 3:
                t = parts[2].lower()
                if t in ['1', '是', 'y', 'yes', 'true', '√', '✓', '周四', '四'] or re.search(r'周[四4]', t):
                    thu = 1
                elif t in ['0', '否', 'n', 'no', 'false', '×', '✗']:
                    thu = 0
            if len(parts) >= 4:
                t = parts[3].lower()
                if t in ['1', '是', 'y', 'yes', 'true', '√', '✓', '周六', '六'] or re.search(r'周[六6]', t):
                    sat = 1
                elif t in ['0', '否', 'n', 'no', 'false', '×', '✗']:
                    sat = 0

            # 插入或更新
            cur = db_execute(conn,
                'SELECT id FROM members WHERE name = %s' if USE_POSTGRES else 'SELECT id FROM members WHERE name = ?',
                (name,))
            existing = db_fetchone(cur)

            if existing:
                db_execute(conn,
                    'UPDATE members SET profession = %s, attend_thursday = %s, attend_saturday = %s, updated_at = ' + now_expr + ' WHERE id = %s' if USE_POSTGRES
                    else 'UPDATE members SET profession = ?, attend_thursday = ?, attend_saturday = ?, updated_at = ' + now_expr + ' WHERE id = ?',
                    (profession, thu, sat, existing['id']))
            else:
                db_execute(conn,
                    'INSERT INTO members (name, profession, attend_thursday, attend_saturday, created_at, updated_at) VALUES (%s, %s, %s, %s, ' + now_expr + ', ' + now_expr + ')' if USE_POSTGRES
                    else 'INSERT INTO members (name, profession, attend_thursday, attend_saturday) VALUES (?, ?, ?, ?)',
                    (name, profession, thu, sat))

            row = db_fetchone(db_execute(conn,
                'SELECT id, name, profession, attend_thursday, attend_saturday FROM members WHERE name = %s' if USE_POSTGRES
                else 'SELECT id, name, profession, attend_thursday, attend_saturday FROM members WHERE name = ?',
                (name,)))
            imported.append(row_to_dict(row))
        except Exception as e:
            errors.append(f'{line}: {str(e)}')

    conn.commit()
    conn.close()

    broadcast('batch_imported', {'count': len(imported)})
    return jsonify({'imported': len(imported), 'totalLines': len(lines), 'errors': errors})


# 导出文本
@app.route('/api/export')
def export_list():
    fmt = request.args.get('format', 'text')
    week = get_current_week()
    conn = get_db()
    cur = db_execute(conn, 'SELECT * FROM members ORDER BY name')
    rows = db_fetchall(cur)
    members = [row_to_dict(r) for r in rows]
    conn.close()

    if fmt == 'text':
        lines = [f'📅 第 {week} 周 · 活动报名名单', '=' * 30]

        lines.append(f'\n【周四 · 霜陨领主】({sum(1 for m in members if m["attendThursday"])}人)')
        for p in PROFESSIONS:
            pm = [m for m in members if m['profession'] == p and m['attendThursday']]
            if pm:
                lines.append(f'\n{p}（{len(pm)}人）：')
                for m in pm:
                    tag = '【双场】' if m['attendBoth'] else ''
                    lines.append(f'  {m["name"]}{tag}')

        lines.append(f'\n【周六 · 猎城战】({sum(1 for m in members if m["attendSaturday"])}人)')
        for p in PROFESSIONS:
            pm = [m for m in members if m['profession'] == p and m['attendSaturday']]
            if pm:
                lines.append(f'\n{p}（{len(pm)}人）：')
                for m in pm:
                    tag = '【双场】' if m['attendBoth'] else ''
                    lines.append(f'  {m["name"]}{tag}')

        total = len(members)
        thu_n = sum(1 for m in members if m['attendThursday'])
        sat_n = sum(1 for m in members if m['attendSaturday'])
        both_n = sum(1 for m in members if m['attendBoth'])
        lines.append(f'\n' + '=' * 30)
        lines.append(f'总计: {total}人 | 周四: {thu_n}人 | 周六: {sat_n}人 | 双场: {both_n}人')

        return jsonify({'content': '\n'.join(lines), 'week': week})

    return jsonify({'error': '不支持的格式'}), 400


# 下载 Excel
@app.route('/api/excel')
def download_excel():
    week = get_current_week()
    try:
        filepath, filename = generate_excel(week)
        return send_file(filepath, as_attachment=True, download_name=filename)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# 立即生成Excel（管理员手动触发，方便测试）
@app.route('/api/excel/generate', methods=['POST'])
def generate_excel_now():
    data = request.get_json(silent=True) or {}
    pwd = data.get('password', '')
    if pwd != ADMIN_PASSWORD:
        return jsonify({'error': '无权限操作'}), 403
    week = get_current_week()
    filepath, filename = generate_excel(week)
    return jsonify({'success': True, 'filename': filename, 'week': week})


# 健康检查
@app.route('/api/health')
def health():
    return jsonify({'status': 'ok', 'time': int(time.time())})


# 首页
@app.route('/')
def index():
    return send_from_directory('public', 'index.html')


if __name__ == '__main__':
    start_server()
