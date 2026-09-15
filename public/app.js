// ===== 诡秘之主 · 活动报名系统 前端逻辑 =====

const PROFESSIONS = [
    { name: '战士', icon: '⚔️' },
    { name: '占卜家', icon: '🔮' },
    { name: '窥秘人', icon: '👁️' },
    { name: '观众（奶）', icon: '💚' },
    { name: '学徒', icon: '📖' },
    { name: '歌颂者', icon: '🎵' },
];

// 状态
let currentRoom = null;
let members = [];
let sseSource = null;

// DOM 元素
const $ = (id) => document.getElementById(id);

// ===== 工具函数 =====
function showToast(message, type = '') {
    const toast = $('toast');
    toast.textContent = message;
    toast.className = 'toast show ' + type;
    setTimeout(() => {
        toast.classList.remove('show');
    }, 2500);
}

function showPage(pageId) {
    document.querySelectorAll('.page').forEach((p) => p.classList.remove('active'));
    $(pageId).classList.add('active');
}

function getProfessionIcon(professionName) {
    const p = PROFESSIONS.find((x) => x.name === professionName);
    return p ? p.icon : '👤';
}

function copyToClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
        return navigator.clipboard.writeText(text);
    }
    // 降级方案
    return new Promise((resolve, reject) => {
        const textarea = document.createElement('textarea');
        textarea.value = text;
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        try {
            document.execCommand('copy');
            resolve();
        } catch (e) {
            reject(e);
        }
        document.body.removeChild(textarea);
    });
}

// ===== 首页逻辑 =====
function initHomePage() {
    $('btn-create').addEventListener('click', createRoom);
    $('btn-join').addEventListener('click', joinRoom);
    $('create-room-name').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') createRoom();
    });
    $('join-room-code').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') joinRoom();
    });
}

async function createRoom() {
    const roomName = $('create-room-name').value.trim();
    try {
        const res = await fetch('/api/rooms', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ roomName }),
        });
        const data = await res.json();
        if (data.success) {
            const code = data.room.roomCode;
            showToast('房间创建成功！');
            loadRoom(code);
        } else {
            showToast(data.error || '创建失败', 'error');
        }
    } catch (e) {
        showToast('网络错误，请重试', 'error');
    }
}

async function joinRoom() {
    const code = $('join-room-code').value.trim().toUpperCase();
    if (!code) {
        showToast('请输入房间号', 'error');
        return;
    }
    loadRoom(code);
}

// ===== 房间页面逻辑 =====
async function loadRoom(roomCode) {
    try {
        const res = await fetch(`/api/rooms/${roomCode}`);
        const data = await res.json();
        if (res.status === 404) {
            showToast('房间不存在', 'error');
            return;
        }
        if (!data.success) {
            showToast(data.error || '加载失败', 'error');
            return;
        }

        currentRoom = data.room;
        members = data.members;

        // 更新 URL
        history.pushState({ roomCode }, '', `#${roomCode}`);

        // 渲染页面
        renderRoom(data);
        showPage('room-page');

        // 连接 SSE
        connectSSE(roomCode);
    } catch (e) {
        console.error('加载房间失败:', e);
        showToast('加载失败，请重试', 'error');
    }
}

function renderRoom(data) {
    $('room-name').textContent = data.room.roomName;
    $('room-code').textContent = data.room.roomCode;

    // 统计
    $('stat-total').textContent = data.totals.total;
    $('stat-thu').textContent = data.totals.thursday;
    $('stat-sat').textContent = data.totals.saturday;
    $('member-count').textContent = `${data.totals.total} 人`;

    // 职业分布
    renderProfessionStats(data.stats);

    // 成员列表
    renderMembersList(data.members);
}

function renderProfessionStats(stats) {
    const container = $('profession-stats');
    container.innerHTML = '';

    PROFESSIONS.forEach((p) => {
        const s = stats[p.name] || { total: 0, thursday: 0, saturday: 0 };
        const div = document.createElement('div');
        div.className = 'profession-stat';
        div.innerHTML = `
            <div class="profession-icon">${p.icon}</div>
            <div class="profession-name">${p.name}</div>
            <div class="profession-count">
                ${s.total}
                <span class="sub-count">四${s.thursday}/六${s.saturday}</span>
            </div>
        `;
        container.appendChild(div);
    });
}

function renderMembersList(memberList) {
    const container = $('members-list');
    const emptyState = $('empty-state');

    if (memberList.length === 0) {
        container.innerHTML = '';
        emptyState.style.display = 'block';
        return;
    }

    emptyState.style.display = 'none';
    container.innerHTML = '';

    // 按职业分组排序
    const sorted = [...memberList].sort((a, b) => {
        const profOrder = PROFESSIONS.map((p) => p.name);
        const pa = profOrder.indexOf(a.profession);
        const pb = profOrder.indexOf(b.profession);
        if (pa !== pb) return pa - pb;
        return a.name.localeCompare(b.name, 'zh-CN');
    });

    sorted.forEach((member) => {
        const item = document.createElement('div');
        item.className = 'member-item';
        item.dataset.id = member.id;

        const thuClass = member.attendThursday ? 'thu' : 'inactive';
        const satClass = member.attendSaturday ? 'sat' : 'inactive';

        item.innerHTML = `
            <div class="member-avatar">${getProfessionIcon(member.profession)}</div>
            <div class="member-info">
                <div class="member-name">${escapeHtml(member.name)}</div>
                <div class="member-profession">${member.profession}</div>
            </div>
            <div class="member-days">
                <span class="day-badge ${thuClass}">周四</span>
                <span class="day-badge ${satClass}">周六</span>
            </div>
            <button class="member-delete" data-id="${member.id}" title="删除">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polyline points="3 6 5 6 21 6"/>
                    <path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/>
                </svg>
            </button>
        `;
        container.appendChild(item);
    });

    // 绑定删除事件
    container.querySelectorAll('.member-delete').forEach((btn) => {
        btn.addEventListener('click', (e) => {
            const id = btn.dataset.id;
            const member = members.find((m) => m.id == id);
            if (member && confirm(`确定要删除 ${member.name} 吗？`)) {
                deleteMember(id);
            }
        });
    });
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// 更新统计（在增删成员后）
function updateStats() {
    const total = members.length;
    const thu = members.filter((m) => m.attendThursday).length;
    const sat = members.filter((m) => m.attendSaturday).length;

    $('stat-total').textContent = total;
    $('stat-thu').textContent = thu;
    $('stat-sat').textContent = sat;
    $('member-count').textContent = `${total} 人`;

    // 更新职业分布
    const stats = {};
    PROFESSIONS.forEach((p) => {
        stats[p.name] = { total: 0, thursday: 0, saturday: 0 };
    });
    members.forEach((m) => {
        if (stats[m.profession]) {
            stats[m.profession].total++;
            if (m.attendThursday) stats[m.profession].thursday++;
            if (m.attendSaturday) stats[m.profession].saturday++;
        }
    });
    renderProfessionStats(stats);
}

// ===== 报名功能 =====
async function submitSignup() {
    const name = $('input-name').value.trim();
    const profession = $('select-profession').value;
    const attendThursday = $('check-thursday').checked;
    const attendSaturday = $('check-saturday').checked;

    if (!name) {
        showToast('请输入游戏昵称', 'error');
        return;
    }
    if (!profession) {
        showToast('请选择职业', 'error');
        return;
    }
    if (!attendThursday && !attendSaturday) {
        showToast('请至少选择一个活动时间', 'error');
        return;
    }

    try {
        const res = await fetch(`/api/rooms/${currentRoom.roomCode}/members`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, profession, attendThursday, attendSaturday }),
        });
        const data = await res.json();
        if (data.success) {
            showToast('报名成功！', 'success');
            // 清空输入
            $('input-name').value = '';
            $('select-profession').value = '';
            $('check-thursday').checked = false;
            $('check-saturday').checked = false;
            // 本地更新（SSE 也会推送）
            upsertMember(data.member);
            renderMembersList(members);
            updateStats();
        } else {
            showToast(data.error || '报名失败', 'error');
        }
    } catch (e) {
        showToast('网络错误', 'error');
    }
}

function upsertMember(member) {
    const idx = members.findIndex((m) => m.id === member.id);
    if (idx >= 0) {
        members[idx] = member;
    } else {
        // 也可能名字相同但ID不同（UPSERT），检查名字
        const nameIdx = members.findIndex((m) => m.name === member.name);
        if (nameIdx >= 0) {
            members[nameIdx] = member;
        } else {
            members.push(member);
        }
    }
}

// ===== 删除成员 =====
async function deleteMember(memberId) {
    try {
        const res = await fetch(`/api/rooms/${currentRoom.roomCode}/members/${memberId}`, {
            method: 'DELETE',
        });
        const data = await res.json();
        if (data.success) {
            showToast('已删除', 'success');
            members = members.filter((m) => m.id != memberId);
            renderMembersList(members);
            updateStats();
        } else {
            showToast(data.error || '删除失败', 'error');
        }
    } catch (e) {
        showToast('网络错误', 'error');
    }
}

// ===== 批量导入 =====
function openImportModal() {
    $('import-text').value = '';
    $('import-result').style.display = 'none';
    $('import-modal').classList.add('active');
}

function closeImportModal() {
    $('import-modal').classList.remove('active');
}

async function doBatchImport() {
    const text = $('import-text').value.trim();
    if (!text) {
        showToast('请输入名单文本', 'error');
        return;
    }

    const btn = $('btn-do-import');
    btn.disabled = true;
    btn.textContent = '导入中...';

    try {
        const res = await fetch(`/api/rooms/${currentRoom.roomCode}/members/batch`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text }),
        });
        const data = await res.json();

        const resultDiv = $('import-result');
        resultDiv.style.display = 'block';

        if (data.success) {
            resultDiv.className = 'import-result';
            let msg = `成功导入 ${data.imported} 人（共 ${data.totalLines} 行）`;
            if (data.errors && data.errors.length > 0) {
                msg += `\n失败 ${data.errors.length} 行：`;
                data.errors.slice(0, 3).forEach((e) => {
                    msg += `\n  第${e.line}行：${e.error}`;
                });
                if (data.errors.length > 3) {
                    msg += `\n  ...还有 ${data.errors.length - 3} 行错误`;
                }
            }
            resultDiv.textContent = msg;
            resultDiv.style.whiteSpace = 'pre-line';

            // 刷新列表
            await refreshMembers();
        } else {
            resultDiv.className = 'import-result error';
            resultDiv.textContent = data.error || '导入失败';
        }
    } catch (e) {
        const resultDiv = $('import-result');
        resultDiv.style.display = 'block';
        resultDiv.className = 'import-result error';
        resultDiv.textContent = '网络错误，请重试';
    } finally {
        btn.disabled = false;
        btn.textContent = '确认导入';
    }
}

// ===== 复制名单 =====
async function copyList() {
    try {
        const res = await fetch(`/api/rooms/${currentRoom.roomCode}/export?format=text`);
        const data = await res.json();
        if (data.success) {
            await copyToClipboard(data.content);
            showToast('名单已复制到剪贴板', 'success');
        } else {
            showToast(data.error || '复制失败', 'error');
        }
    } catch (e) {
        showToast('复制失败', 'error');
    }
}

// ===== 刷新成员列表 =====
async function refreshMembers() {
    try {
        const res = await fetch(`/api/rooms/${currentRoom.roomCode}`);
        const data = await res.json();
        if (data.success) {
            members = data.members;
            renderMembersList(members);
            updateStats();
        }
    } catch (e) {
        console.error('刷新失败:', e);
    }
}

// ===== SSE 实时同步 =====
function connectSSE(roomCode) {
    // 关闭旧连接
    if (sseSource) {
        sseSource.close();
        sseSource = null;
    }

    updateConnStatus('connecting');

    try {
        sseSource = new EventSource(`/api/rooms/${roomCode}/stream`);

        sseSource.addEventListener('connected', () => {
            updateConnStatus('connected');
        });

        sseSource.addEventListener('member_updated', (event) => {
            const data = JSON.parse(event.data);
            upsertMember(data.member);
            renderMembersList(members);
            updateStats();
        });

        sseSource.addEventListener('member_deleted', (event) => {
            const data = JSON.parse(event.data);
            members = members.filter((m) => m.id !== data.memberId);
            renderMembersList(members);
            updateStats();
        });

        sseSource.addEventListener('batch_imported', () => {
            // 批量导入后刷新完整列表
            refreshMembers();
        });

        sseSource.addEventListener('heartbeat', () => {
            // 心跳，保持连接
        });

        sseSource.onerror = () => {
            updateConnStatus('disconnected');
            // 尝试重连（EventSource 会自动重连）
            setTimeout(() => {
                if (sseSource && sseSource.readyState === EventSource.OPEN) {
                    updateConnStatus('connected');
                }
            }, 2000);
        };
    } catch (e) {
        console.error('SSE 连接失败:', e);
        updateConnStatus('disconnected');
    }
}

function updateConnStatus(status) {
    const dot = document.querySelector('.status-dot');
    if (!dot) return;
    dot.className = 'status-dot ' + (status === 'connected' ? '' : status);
}

// ===== 复制房间号 =====
function copyRoomCode() {
    if (currentRoom) {
        copyToClipboard(currentRoom.roomCode);
        showToast('房间号已复制', 'success');
    }
}

// ===== 返回首页 =====
function goHome() {
    if (sseSource) {
        sseSource.close();
        sseSource = null;
    }
    currentRoom = null;
    members = [];
    showPage('home-page');
    history.pushState({}, '', window.location.pathname);
}

// ===== 初始化 =====
function init() {
    initHomePage();

    // 房间页面按钮
    $('btn-back').addEventListener('click', goHome);
    $('btn-signup').addEventListener('click', submitSignup);
    $('btn-copy-code').addEventListener('click', copyRoomCode);
    $('btn-batch-import').addEventListener('click', openImportModal);
    $('btn-copy-list').addEventListener('click', copyList);
    $('btn-close-import').addEventListener('click', closeImportModal);
    $('btn-cancel-import').addEventListener('click', closeImportModal);
    $('btn-do-import').addEventListener('click', doBatchImport);

    // 点击遮罩关闭
    document.querySelector('.modal-overlay').addEventListener('click', closeImportModal);

    // 回车报名
    $('input-name').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') submitSignup();
    });

    // 浏览器前进后退
    window.addEventListener('popstate', (e) => {
        if (e.state && e.state.roomCode) {
            loadRoom(e.state.roomCode);
        } else {
            goHome();
        }
    });

    // 检查 URL hash 中的房间号
    const hash = window.location.hash.replace('#', '').trim().toUpperCase();
    if (hash && hash.length >= 4) {
        loadRoom(hash);
    }
}

// 启动
document.addEventListener('DOMContentLoaded', init);
