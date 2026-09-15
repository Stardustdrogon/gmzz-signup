# 诡秘之主 活动报名系统 - 后端服务 (Python + SQLite)
# 功能：房间管理、成员报名、SSE 实时推送、批量导入、名单导出、自动清理

import sqlite3
import json
import time
import os
import random
import string
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

# 配置
DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(__file__), 'data', 'signup.db'))
PORT = int(os.environ.get('PORT', 3000))

# 职业列表
PROFESSIONS = ['战士', '占卜家', '窥秘人', '观众（奶）', '学徒', '歌颂者']

# SSE 客户端：room_code -> set of queue
sse_clients = {}
sse_lock = threading.Lock()


# ===== 数据库初始化 =====
def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_code TEXT UNIQUE NOT NULL,
            room_name TEXT NOT NULL DEFAULT '活动报名',
            created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
            last_accessed_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
        );

        CREATE TABLE IF NOT EXISTS members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            profession TEXT NOT NULL CHECK (profession IN ('战士','占卜家','窥秘人','观众（奶）','学徒','歌颂者')),
            attend_thursday INTEGER NOT NULL DEFAULT 0,
            attend_saturday INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
            updated_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
            UNIQUE(room_id, name)
        );

        CREATE INDEX IF NOT EXISTS idx_rooms_code ON rooms(room_code);
        CREATE INDEX IF NOT EXISTS idx_rooms_last_accessed ON rooms(last_accessed_at);
        CREATE INDEX IF NOT EXISTS idx_members_room ON members(room_id);
        CREATE INDEX IF NOT EXISTS idx_members_profession ON members(profession);
    ''')
    conn.commit()
    conn.close()
    print(f'✅ 数据库已就绪: {DB_PATH}')


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def ts_to_iso(ts):
    return datetime.fromtimestamp(ts).isoformat() + 'Z'


# ===== SSE 工具 =====
def broadcast(room_code, event, data):
    """向房间内所有连接推送消息"""
    msg = f'event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'
    with sse_lock:
        clients = sse_clients.get(room_code, set())
        dead = []
        for q in clients:
            try:
                q.put(msg)
            except Exception:
                dead.append(q)
        for q in dead:
            clients.discard(q)
        if not clients:
            sse_clients.pop(room_code, None)


class ClientQueue:
    """简单的消息队列，用于 SSE 推送"""
    def __init__(self):
        import queue
        self.q = queue.Queue()

    def put(self, msg):
        self.q.put(msg)

    def get(self, timeout=30):
        return self.q.get(timeout=timeout)


# ===== 房间 API =====
def generate_room_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))


@app.route('/api/rooms', methods=['POST'])
def create_room():
    data = request.get_json() or {}
    room_name = data.get('roomName', '活动报名') or '活动报名'

    conn = get_db()
    try:
        for _ in range(10):
            code = generate_room_code()
            existing = conn.execute('SELECT id FROM rooms WHERE room_code = ?', (code,)).fetchone()
            if not existing:
                break
        else:
            return jsonify({'error': '无法生成唯一房间码'}), 500

        cur = conn.execute(
            'INSERT INTO rooms (room_code, room_name) VALUES (?, ?)',
            (code, room_name)
        )
        conn.commit()
        room_id = cur.lastrowid
        row = conn.execute('SELECT created_at FROM rooms WHERE id = ?', (room_id,)).fetchone()

        return jsonify({
            'success': True,
            'room': {
                'id': room_id,
                'roomCode': code,
                'roomName': room_name,
                'createdAt': ts_to_iso(row['created_at'])
            }
        })
    finally:
        conn.close()


@app.route('/api/rooms/<room_code>', methods=['GET'])
def get_room(room_code):
    conn = get_db()
    try:
        # 更新访问时间
        conn.execute(
            "UPDATE rooms SET last_accessed_at = strftime('%s', 'now') WHERE room_code = ?",
            (room_code,)
        )
        conn.commit()

        room = conn.execute(
            'SELECT id, room_code, room_name, created_at FROM rooms WHERE room_code = ?',
            (room_code,)
        ).fetchone()

        if not room:
            return jsonify({'error': '房间不存在'}), 404

        member_rows = conn.execute(
            '''SELECT id, name, profession, attend_thursday, attend_saturday,
               created_at, updated_at FROM members WHERE room_id = ?
               ORDER BY created_at ASC''',
            (room['id'],)
        ).fetchall()

        members = []
        stats = {p: {'total': 0, 'thursday': 0, 'saturday': 0} for p in PROFESSIONS}
        thu_total = 0
        sat_total = 0

        for m in member_rows:
            thu = bool(m['attend_thursday'])
            sat = bool(m['attend_saturday'])
            members.append({
                'id': m['id'],
                'name': m['name'],
                'profession': m['profession'],
                'attendThursday': thu,
                'attendSaturday': sat,
                'createdAt': ts_to_iso(m['created_at']),
                'updatedAt': ts_to_iso(m['updated_at'])
            })
            if m['profession'] in stats:
                stats[m['profession']]['total'] += 1
                if thu:
                    stats[m['profession']]['thursday'] += 1
                    thu_total += 1
                if sat:
                    stats[m['profession']]['saturday'] += 1
                    sat_total += 1

        return jsonify({
            'success': True,
            'room': {
                'id': room['id'],
                'roomCode': room['room_code'],
                'roomName': room['room_name'],
                'createdAt': ts_to_iso(room['created_at'])
            },
            'members': members,
            'stats': stats,
            'totals': {
                'total': len(members),
                'thursday': thu_total,
                'saturday': sat_total
            },
            'professions': PROFESSIONS
        })
    finally:
        conn.close()


# ===== SSE 实时推送 =====
@app.route('/api/rooms/<room_code>/stream')
def sse_stream(room_code):
    conn = get_db()
    try:
        room = conn.execute('SELECT id FROM rooms WHERE room_code = ?', (room_code,)).fetchone()
        if not room:
            return jsonify({'error': '房间不存在'}), 404

        conn.execute(
            "UPDATE rooms SET last_accessed_at = strftime('%s', 'now') WHERE room_code = ?",
            (room_code,)
        )
        conn.commit()
    finally:
        conn.close()

    def generate():
        client = ClientQueue()
        with sse_lock:
            if room_code not in sse_clients:
                sse_clients[room_code] = set()
            sse_clients[room_code].add(client)

        try:
            # 连接确认
            yield f'event: connected\ndata: {{"message":"已连接实时同步"}}\n\n'

            last_heartbeat = time.time()
            while True:
                try:
                    msg = client.get(timeout=15)
                    yield msg
                except Exception:
                    # 超时，发心跳
                    now = time.time()
                    if now - last_heartbeat >= 25:
                        yield f'event: heartbeat\ndata: {{"time":{int(now*1000)}}}\n\n'
                        last_heartbeat = now
        finally:
            with sse_lock:
                if room_code in sse_clients:
                    sse_clients[room_code].discard(client)
                    if not sse_clients[room_code]:
                        del sse_clients[room_code]

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no',
        }
    )


# ===== 成员报名 =====
@app.route('/api/rooms/<room_code>/members', methods=['POST'])
def add_member(room_code):
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    profession = data.get('profession') or ''
    attend_thursday = 1 if data.get('attendThursday') else 0
    attend_saturday = 1 if data.get('attendSaturday') else 0

    if not name:
        return jsonify({'error': '请输入姓名'}), 400
    if profession not in PROFESSIONS:
        return jsonify({'error': '请选择有效的职业'}), 400

    conn = get_db()
    try:
        room = conn.execute('SELECT id FROM rooms WHERE room_code = ?', (room_code,)).fetchone()
        if not room:
            return jsonify({'error': '房间不存在'}), 404

        room_id = room['id']

        # UPSERT
        conn.execute(
            '''INSERT INTO members (room_id, name, profession, attend_thursday, attend_saturday, updated_at)
               VALUES (?, ?, ?, ?, ?, strftime('%s', 'now'))
               ON CONFLICT(room_id, name) DO UPDATE SET
                 profession = excluded.profession,
                 attend_thursday = excluded.attend_thursday,
                 attend_saturday = excluded.attend_saturday,
                 updated_at = strftime('%s', 'now')''',
            (room_id, name, profession, attend_thursday, attend_saturday)
        )
        conn.commit()

        row = conn.execute(
            '''SELECT id, name, profession, attend_thursday, attend_saturday,
               created_at, updated_at FROM members WHERE room_id = ? AND name = ?''',
            (room_id, name)
        ).fetchone()

        member = {
            'id': row['id'],
            'name': row['name'],
            'profession': row['profession'],
            'attendThursday': bool(row['attend_thursday']),
            'attendSaturday': bool(row['attend_saturday']),
            'createdAt': ts_to_iso(row['created_at']),
            'updatedAt': ts_to_iso(row['updated_at'])
        }

        conn.execute(
            "UPDATE rooms SET last_accessed_at = strftime('%s', 'now') WHERE id = ?",
            (room_id,)
        )
        conn.commit()

        broadcast(room_code, 'member_updated', {'member': member})

        return jsonify({'success': True, 'member': member})
    finally:
        conn.close()


@app.route('/api/rooms/<room_code>/members/<int:member_id>', methods=['DELETE'])
def delete_member(room_code, member_id):
    conn = get_db()
    try:
        room = conn.execute('SELECT id FROM rooms WHERE room_code = ?', (room_code,)).fetchone()
        if not room:
            return jsonify({'error': '房间不存在'}), 404

        cur = conn.execute(
            'DELETE FROM members WHERE id = ? AND room_id = ?',
            (member_id, room['id'])
        )
        conn.commit()

        if cur.rowcount == 0:
            return jsonify({'error': '成员不存在'}), 404

        conn.execute(
            "UPDATE rooms SET last_accessed_at = strftime('%s', 'now') WHERE id = ?",
            (room['id'],)
        )
        conn.commit()

        broadcast(room_code, 'member_deleted', {'memberId': member_id})

        return jsonify({'success': True})
    finally:
        conn.close()


# ===== 批量导入 =====
@app.route('/api/rooms/<room_code>/members/batch', methods=['POST'])
def batch_import(room_code):
    data = request.get_json() or {}
    text = (data.get('text') or '').strip()

    if not text:
        return jsonify({'error': '请输入名单文本'}), 400

    conn = get_db()
    try:
        room = conn.execute('SELECT id FROM rooms WHERE room_code = ?', (room_code,)).fetchone()
        if not room:
            return jsonify({'error': '房间不存在'}), 404

        room_id = room['id']
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        imported = []
        errors = []

        for i, line in enumerate(lines):
            import re
            parts = [p for p in re.split(r'[\s,，、;；\t]+', line) if p]

            if len(parts) < 2:
                errors.append({'line': i + 1, 'content': line, 'error': '格式不正确，至少需要姓名和职业'})
                continue

            name = parts[0]
            prof_input = parts[1]

            # 匹配职业
            profession = None
            for p in PROFESSIONS:
                if p == prof_input or prof_input in p or prof_input in p.replace('（奶）', ''):
                    profession = p
                    break
            if not profession and prof_input in ['奶', '奶妈', '治疗', '观众']:
                profession = '观众（奶）'

            if not profession:
                errors.append({'line': i + 1, 'content': line, 'error': f'职业「{prof_input}」不匹配'})
                continue

            # 解析周四/周六
            thu = 0
            sat = 0
            if len(parts) >= 3:
                t = parts[2].lower()
                if t in ['1', '是', 'y', 'yes', 'true', '√', '✓', '周四', '四'] or re.search(r'周[四4]', t):
                    thu = 1
            if len(parts) >= 4:
                s = parts[3].lower()
                if s in ['1', '是', 'y', 'yes', 'true', '√', '✓', '周六', '六'] or re.search(r'周[六6]', s):
                    sat = 1
            if len(parts) == 2:
                if re.search(r'周[四4]', line):
                    thu = 1
                if re.search(r'周[六6]', line):
                    sat = 1

            try:
                conn.execute(
                    '''INSERT INTO members (room_id, name, profession, attend_thursday, attend_saturday, updated_at)
                       VALUES (?, ?, ?, ?, ?, strftime('%s', 'now'))
                       ON CONFLICT(room_id, name) DO UPDATE SET
                         profession = excluded.profession,
                         attend_thursday = excluded.attend_thursday,
                         attend_saturday = excluded.attend_saturday,
                         updated_at = strftime('%s', 'now')''',
                    (room_id, name, profession, thu, sat)
                )

                row = conn.execute(
                    '''SELECT id, name, profession, attend_thursday, attend_saturday
                       FROM members WHERE room_id = ? AND name = ?''',
                    (room_id, name)
                ).fetchone()

                imported.append({
                    'id': row['id'],
                    'name': row['name'],
                    'profession': row['profession'],
                    'attendThursday': bool(row['attend_thursday']),
                    'attendSaturday': bool(row['attend_saturday'])
                })
            except Exception as e:
                errors.append({'line': i + 1, 'content': line, 'error': str(e)})

        conn.commit()

        conn.execute(
            "UPDATE rooms SET last_accessed_at = strftime('%s', 'now') WHERE id = ?",
            (room_id,)
        )
        conn.commit()

        if imported:
            broadcast(room_code, 'batch_imported', {'count': len(imported)})

        return jsonify({
            'success': True,
            'imported': len(imported),
            'totalLines': len(lines),
            'errors': errors,
            'importedMembers': imported
        })
    finally:
        conn.close()


# ===== 导出名单 =====
@app.route('/api/rooms/<room_code>/export', methods=['GET'])
def export_room(room_code):
    fmt = request.args.get('format', 'text')
    conn = get_db()
    try:
        conn.execute(
            "UPDATE rooms SET last_accessed_at = strftime('%s', 'now') WHERE room_code = ?",
            (room_code,)
        )
        conn.commit()

        room = conn.execute(
            'SELECT id, room_name FROM rooms WHERE room_code = ?',
            (room_code,)
        ).fetchone()
        if not room:
            return jsonify({'error': '房间不存在'}), 404

        member_rows = conn.execute(
            '''SELECT name, profession, attend_thursday, attend_saturday
               FROM members WHERE room_id = ? ORDER BY profession, name''',
            (room['id'],)
        ).fetchall()

        members = []
        for m in member_rows:
            members.append({
                'name': m['name'],
                'profession': m['profession'],
                'attendThursday': bool(m['attend_thursday']),
                'attendSaturday': bool(m['attend_saturday'])
            })

        output = ''
        if fmt == 'csv':
            output = '姓名,职业,周四,周六\n'
            for m in members:
                output += f"{m['name']},{m['profession']},{'是' if m['attendThursday'] else '否'},{'是' if m['attendSaturday'] else '否'}\n"
        elif fmt == 'markdown':
            output = f"## {room['room_name']} 报名名单\n\n"
            output += f"共 {len(members)} 人报名\n\n"
            output += '| 姓名 | 职业 | 周四20:00 | 周六20:00 |\n'
            output += '|------|------|-----------|----------|\n'
            for m in members:
                thu = '✓' if m['attendThursday'] else '-'
                sat = '✓' if m['attendSaturday'] else '-'
                output += f"| {m['name']} | {m['profession']} | {thu} | {sat} |\n"
            output += '\n## 职业分布\n\n'
            for p in PROFESSIONS:
                pm = [m for m in members if m['profession'] == p]
                thu_c = sum(1 for m in pm if m['attendThursday'])
                sat_c = sum(1 for m in pm if m['attendSaturday'])
                output += f"- **{p}**：{len(pm)}人（周四{thu_c} / 周六{sat_c}）\n"
            thu_total = sum(1 for m in members if m['attendThursday'])
            sat_total = sum(1 for m in members if m['attendSaturday'])
            output += f"\n**周四总计：{thu_total}人 | 周六总计：{sat_total}人**\n"
        else:
            output = f"【{room['room_name']}】报名名单\n"
            output += f"共 {len(members)} 人报名\n"
            output += '=' * 30 + '\n\n'
            for p in PROFESSIONS:
                pm = [m for m in members if m['profession'] == p]
                if not pm:
                    continue
                output += f"【{p}】({len(pm)}人)\n"
                for m in pm:
                    days = []
                    if m['attendThursday']:
                        days.append('周四')
                    if m['attendSaturday']:
                        days.append('周六')
                    output += f"  {m['name']} - { '、'.join(days) or '未选'}\n"
                output += '\n'
            thu_total = sum(1 for m in members if m['attendThursday'])
            sat_total = sum(1 for m in members if m['attendSaturday'])
            output += '=' * 30 + '\n'
            output += f"周四 20:00：{thu_total} 人\n"
            output += f"周六 20:00：{sat_total} 人\n"

        return jsonify({
            'success': True,
            'content': output,
            'format': fmt
        })
    finally:
        conn.close()


# ===== 健康检查 =====
@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'time': datetime.now().isoformat() + 'Z'})


# ===== 静态文件 =====
@app.route('/')
def index():
    return send_from_directory('public', 'index.html')


# ===== 7 天自动清理 =====
def cleanup_worker():
    """后台线程：每小时清理一次过期房间"""
    while True:
        try:
            seven_days_ago = int(time.time()) - 7 * 24 * 3600
            conn = get_db()
            cur = conn.execute('DELETE FROM rooms WHERE last_accessed_at < ?', (seven_days_ago,))
            conn.commit()
            conn.close()
            if cur.rowcount > 0:
                print(f'[清理] 已删除 {cur.rowcount} 个过期房间')
        except Exception as e:
            print(f'[清理] 出错: {e}')
        time.sleep(3600)  # 1 小时


# ===== 启动 =====
if __name__ == '__main__':
    init_db()

    # 启动清理线程
    t = threading.Thread(target=cleanup_worker, daemon=True)
    t.start()

    print()
    print('╔══════════════════════════════════════╗')
    print('║  诡秘之主 活动报名系统 (SQLite)     ║')
    print('║  服务器已启动                        ║')
    print(f'║  地址: http://localhost:{str(PORT).ljust(19)}║')
    print('╚══════════════════════════════════════╝')
    print()

    # 使用 waitress 作为生产级 WSGI 服务器
    import waitress
    print(f'服务监听端口: {PORT}')
    waitress.serve(app, host='0.0.0.0', port=PORT, threads=8)
