# 诡秘之主 活动报名系统 - 后端服务 (Python + SQLite)
# 固定两场活动：周四 霜陨领主 / 周六 猎城战
# 功能：成员报名、SSE 实时推送、批量导入、总名单、导出

import sqlite3
import json
import time
import os
import re
import threading
from datetime import datetime
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


# ===== 数据库初始化 =====
def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=DELETE')
    conn.executescript('''
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

        CREATE INDEX IF NOT EXISTS idx_members_profession ON members(profession);
        CREATE INDEX IF NOT EXISTS idx_members_thursday ON members(attend_thursday);
        CREATE INDEX IF NOT EXISTS idx_members_saturday ON members(attend_saturday);
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
    # 双场标记
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


# ===== SSE 流 =====
@app.route('/api/stream')
def stream():
    import queue
    q = queue.Queue(maxsize=32)
    with sse_lock:
        sse_clients.add(q)

    def generate():
        try:
            # 先发送初始化事件
            yield 'event: connected\ndata: {"status":"ok"}\n\n'
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


# ===== 活动配置 =====
@app.route('/api/events')
def get_events():
    return jsonify({'events': list(EVENTS.values())})


# ===== 获取总名单 =====
@app.route('/api/members')
def get_all_members():
    event_key = request.args.get('event')  # thursday / saturday / 空=全部

    conn = get_db()
    query = 'SELECT * FROM members WHERE 1=1'
    params = []

    if event_key == 'thursday':
        query += ' AND attend_thursday = 1'
    elif event_key == 'saturday':
        query += ' AND attend_saturday = 1'

    query += ' ORDER BY '
    # 按职业顺序排序
    query += "CASE profession "
    for i, p in enumerate(PROFESSIONS):
        query += f"WHEN '{p}' THEN {i} "
    query += "ELSE 99 END, name"

    rows = conn.execute(query, params).fetchall()
    members = [row_to_dict(r) for r in rows]

    # 统计
    totals = {
        'total': len(members),
        'thursday': sum(1 for m in members if m['attendThursday']),
        'saturday': sum(1 for m in members if m['attendSaturday']),
        'both': sum(1 for m in members if m['attendBoth']),
        'onlyThursday': sum(1 for m in members if m['attendThursday'] and not m['attendSaturday']),
        'onlySaturday': sum(1 for m in members if m['attendSaturday'] and not m['attendThursday']),
    }

    # 职业分布
    stats = {}
    for p in PROFESSIONS:
        p_members = [m for m in members if m['profession'] == p]
        stats[p] = {
            'total': len(p_members),
            'thursday': sum(1 for m in p_members if m['attendThursday']),
            'saturday': sum(1 for m in p_members if m['attendSaturday']),
            'both': sum(1 for m in p_members if m['attendBoth']),
        }

    conn.close()
    return jsonify({
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
    event_key = data.get('event')  # thursday / saturday / both
    remark = (data.get('remark') or '').strip()

    if not name:
        return jsonify({'error': '请输入姓名'}), 400
    if profession not in PROFESSIONS:
        return jsonify({'error': '请选择正确的职业'}), 400
    if event_key not in ['thursday', 'saturday', 'both']:
        return jsonify({'error': '请选择参加的活动'}), 400

    conn = get_db()

    # 查找是否已存在
    existing = conn.execute('SELECT * FROM members WHERE name = ?', (name,)).fetchone()

    if existing:
        # 更新：保留另一场的报名状态，更新当前场
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

    # 推送
    broadcast('member_updated', {'member': member})

    return jsonify({'member': member, 'isNew': existing is None})


# ===== 取消报名某场 =====
@app.route('/api/members/<int:member_id>/cancel', methods=['POST'])
def cancel_member(member_id):
    data = request.get_json()
    event_key = data.get('event')  # thursday / saturday

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

    # 如果两场都不参加了，删除记录
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
    default_event = data.get('defaultEvent', 'both')  # 默认导入到哪场

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

            # 匹配职业
            profession = None
            for p in PROFESSIONS:
                if p == prof_input or prof_input in p or prof_input in p.replace('（奶）', ''):
                    profession = p
                    break
            if not profession:
                errors.append(f'无法识别职业「{prof_input}」: {line}')
                continue

            # 判断参加哪几场
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

            # 如果没有明确指定，用默认
            if thu == 0 and sat == 0:
                if default_event == 'thursday':
                    thu = 1
                elif default_event == 'saturday':
                    sat = 1
                else:
                    thu = sat = 1

            # 插入或更新
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

    # 推送刷新通知
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
    event_key = request.args.get('event', '')  # thursday/saturday/空=全部

    conn = get_db()
    rows = conn.execute('SELECT * FROM members ORDER BY name').fetchall()
    members = [row_to_dict(r) for r in rows]
    conn.close()

    if event_key == 'thursday':
        members = [m for m in members if m['attendThursday']]
    elif event_key == 'saturday':
        members = [m for m in members if m['attendSaturday']]

    if fmt == 'json':
        return jsonify({'members': members})

    # 文本格式
    lines = []

    # 周四
    thu_members = [m for m in members if m['attendThursday']]
    lines.append(f'【周四 · 霜陨领主】共 {len(thu_members)} 人')
    for p in PROFESSIONS:
        p_list = [m['name'] for m in thu_members if m['profession'] == p]
        if p_list:
            lines.append(f'  {p}（{len(p_list)}）：' + '、'.join(p_list))
    lines.append('')

    # 周六
    sat_members = [m for m in members if m['attendSaturday']]
    lines.append(f'【周六 · 猎城战】共 {len(sat_members)} 人')
    for p in PROFESSIONS:
        p_list = [m['name'] for m in sat_members if m['profession'] == p]
        if p_list:
            lines.append(f'  {p}（{len(p_list)}）：' + '、'.join(p_list))
    lines.append('')

    # 双场
    both = [m for m in members if m['attendBoth']]
    lines.append(f'【双场都参加】共 {len(both)} 人')
    lines.append('  ' + '、'.join(m['name'] for m in both) if both else '  无')
    lines.append('')

    lines.append(f'总计：{len(members)} 人（周四 {len(thu_members)} / 周六 {len(sat_members)} / 双场 {len(both)}）')

    content = '\n'.join(lines)
    return jsonify({'content': content})


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

@app.route('/api/health')
def health():
    return jsonify({'status': 'ok', 'time': int(time.time())})


# ===== 启动 =====
if __name__ == '__main__':
    init_db()
    print()
    print('╔═══════════════════════════════════════════╗')
    print('║  诡秘之主 · 活动报名系统 v2.0            ║')
    print('╠═══════════════════════════════════════════╣')
    print('║  周四 · 霜陨领主    /event/thursday       ║')
    print('║  周六 · 猎城战      /event/saturday       ║')
    print('║  总名单             /all                  ║')
    print(f'║  地址: http://localhost:{str(PORT).ljust(22)}║')
    print('╚═══════════════════════════════════════════╝')
    print()
    try:
        import waitress
        print(f'使用 waitress 启动服务，端口: {PORT}')
        waitress.serve(app, host='0.0.0.0', port=PORT, threads=8)
    except ImportError:
        print(f'使用 Flask 开发服务器，端口: {PORT}')
        app.run(host='0.0.0.0', port=PORT, threaded=True)
