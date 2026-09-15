// 活动报名系统 - 前端逻辑
// 支持活动详情页 (/event/xxx) 和总名单页 (/all)

const PROFESSIONS = ['战士', '占卜家', '窥秘人', '观众（奶）', '学徒', '歌颂者'];
const PROF_ICONS = {
    '战士': '⚔️',
    '占卜家': '🔮',
    '窥秘人': '👁️',
    '观众（奶）': '💚',
    '学徒': '📖',
    '歌颂者': '🎵'
};

const EVENT_CONFIG = {
    thursday: { key: 'thursday', title: '霜陨领主', day: '周四', time: '晚上 8:00', color: '#8b5cf6' },
    saturday: { key: 'saturday', title: '猎城战', day: '周六', time: '晚上 8:00', color: '#3b82f6' }
};

let currentEvent = null; // 'thursday' / 'saturday' / 'all'
let currentFilter = 'all';
let membersCache = [];
let selectedProfession = null;

// ============ 活动详情页初始化 ============
function initEventPage(eventKey) {
    currentEvent = eventKey;
    const evt = EVENT_CONFIG[eventKey];
    if (!evt) {
        document.body.innerHTML = '<p>活动不存在</p>';
        return;
    }

    // 设置页面标题
    document.getElementById('pageTitle').textContent = `${evt.day} ${evt.title} · 报名`;
    document.getElementById('eventBadge').textContent = `${evt.day} · ${evt.title}`;
    document.getElementById('eventBadge').style.background = evt.color + '20';
    document.getElementById('eventBadge').style.color = evt.color;
    document.getElementById('eventTitle').textContent = evt.title;
    document.getElementById('eventMeta').textContent = `${evt.day} · ${evt.time}`;
    document.getElementById('eventHeader').style.background = `linear-gradient(135deg, ${evt.color}30, transparent)`;

    // 默认勾选当前活动
    document.getElementById('checkThursday').checked = (eventKey === 'thursday');
    document.getElementById('checkSaturday').checked = (eventKey === 'saturday');

    // 显示双场统计
    document.getElementById('bothStat').style.display = 'flex';

    // 显示筛选按钮
    document.getElementById('listFilter').style.display = 'flex';

    // 筛选事件
    document.querySelectorAll('.filter-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentFilter = btn.dataset.filter;
            renderMemberList();
        });
    });

    // 职业选择
    document.querySelectorAll('.prof-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.prof-btn').forEach(b => b.classList.remove('selected'));
            btn.classList.add('selected');
            selectedProfession = btn.dataset.prof;
        });
    });

    // 表单提交
    document.getElementById('signupForm').addEventListener('submit', handleSignupSubmit);

    // 加载数据 + SSE
    loadMembers();
    connectSSE();
}

// ============ 总名单页初始化 ============
function initAllPage() {
    currentEvent = 'all';

    // 筛选事件
    document.querySelectorAll('.filter-bar .filter-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.filter-bar .filter-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentFilter = btn.dataset.filter;
            renderMemberList();
        });
    });

    loadMembers();
    connectSSE();
}

// ============ 加载成员数据 ============
function loadMembers() {
    let url = '/api/members';
    if (currentEvent === 'thursday' || currentEvent === 'saturday') {
        url += '?event=' + currentEvent;
    }

    fetch(url)
        .then(r => r.json())
        .then(data => {
            membersCache = data.members;
            renderAll(data);
        })
        .catch(err => {
            showToast('加载失败，请刷新', 'error');
        });
}

// ============ 渲染全部 ============
function renderAll(data) {
    // 统计数字
    if (currentEvent === 'all') {
        document.getElementById('totalNum').textContent = data.totals.total;
        document.getElementById('thuNum').textContent = data.totals.thursday;
        document.getElementById('satNum').textContent = data.totals.saturday;
        document.getElementById('bothNum').textContent = data.totals.both;
    } else {
        document.getElementById('totalNum').textContent = data.totals.total;
        document.getElementById('bothNum').textContent = data.totals.both;
    }

    // 职业统计
    renderProfStats(data.stats);

    // 名单
    renderMemberList();
}

// ============ 渲染职业统计 ============
function renderProfStats(stats) {
    const container = document.getElementById('profStats');
    container.innerHTML = '';
    for (const prof of PROFESSIONS) {
        const s = stats[prof] || { total: 0 };
        const bar = document.createElement('div');
        bar.className = 'prof-stat-item';
        const percent = s.total > 0 ? Math.max(s.total * 8, 30) : 0;
        bar.innerHTML = `
            <div class="prof-stat-head">
                <span class="prof-stat-name">${PROF_ICONS[prof] || ''} ${prof}</span>
                <span class="prof-stat-num">${s.total} 人</span>
            </div>
            <div class="prof-stat-bar">
                <div class="prof-stat-fill" style="width:${percent}%"></div>
            </div>
        `;
        container.appendChild(bar);
    }
}

// ============ 渲染成员列表 ============
function renderMemberList() {
    const listEl = document.getElementById('memberList');
    const countEl = document.getElementById('listCount');
    if (!listEl) return;

    // 筛选
    let filtered = [...membersCache];
    if (currentFilter === 'both') {
        filtered = filtered.filter(m => m.attendBoth);
    } else if (currentFilter === 'thursday' && currentEvent === 'all') {
        filtered = filtered.filter(m => m.attendThursday && !m.attendSaturday);
    } else if (currentFilter === 'saturday' && currentEvent === 'all') {
        filtered = filtered.filter(m => m.attendSaturday && !m.attendThursday);
    }

    if (countEl) countEl.textContent = filtered.length;

    if (filtered.length === 0) {
        listEl.innerHTML = `
            <div class="empty-state">
                <div class="empty-icon">📭</div>
                <p>暂无报名，来第一个报名吧！</p>
            </div>
        `;
        return;
    }

    // 按职业分组
    const grouped = {};
    for (const prof of PROFESSIONS) grouped[prof] = [];
    filtered.forEach(m => {
        if (grouped[m.profession]) grouped[m.profession].push(m);
    });

    let html = '';
    for (const prof of PROFESSIONS) {
        const list = grouped[prof];
        if (list.length === 0) continue;

        html += `<div class="prof-group">
            <div class="prof-group-header">
                <span>${PROF_ICONS[prof] || ''} ${prof}</span>
                <span class="prof-group-count">${list.length} 人</span>
            </div>
            <div class="prof-members">`;

        list.forEach(m => {
            const bothTag = currentEvent === 'all'
                ? (m.attendBoth
                    ? '<span class="tag tag-both">双场</span>'
                    : (m.attendThursday
                        ? '<span class="tag tag-thu">周四</span>'
                        : '<span class="tag tag-sat">周六</span>'))
                : (m.attendBoth ? '<span class="tag tag-both">双场</span>' : '');

            html += `<div class="member-card ${m.attendBoth ? 'is-both' : ''}" data-id="${m.id}">
                <div class="member-avatar" style="background: ${getProfColor(m.profession)}">
                    ${m.name.charAt(0)}
                </div>
                <div class="member-info">
                    <div class="member-name">${m.name} ${bothTag}</div>
                    ${m.remark ? `<div class="member-remark">${m.remark}</div>` : ''}
                </div>
                <button class="member-delete" onclick="deleteMember(${m.id})" title="删除">×</button>
            </div>`;
        });

        html += `</div></div>`;
    }

    listEl.innerHTML = html;
}

function getProfColor(prof) {
    const colors = {
        '战士': '#ef4444',
        '占卜家': '#8b5cf6',
        '窥秘人': '#6366f1',
        '观众（奶）': '#22c55e',
        '学徒': '#f59e0b',
        '歌颂者': '#ec4899'
    };
    return colors[prof] || '#6b7280';
}

// ============ 报名提交 ============
function handleSignupSubmit(e) {
    e.preventDefault();
    const name = document.getElementById('nameInput').value.trim();
    const remark = document.getElementById('remarkInput').value.trim();
    const thu = document.getElementById('checkThursday').checked;
    const sat = document.getElementById('checkSaturday').checked;

    if (!name) { showToast('请输入昵称', 'error'); return; }
    if (!selectedProfession) { showToast('请选择职业', 'error'); return; }
    if (!thu && !sat) { showToast('至少选一场活动', 'error'); return; }

    let event_val;
    if (thu && sat) event_val = 'both';
    else if (thu) event_val = 'thursday';
    else event_val = 'saturday';

    const btn = document.getElementById('submitBtn');
    btn.disabled = true;
    btn.textContent = '报名中...';

    fetch('/api/signup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, profession: selectedProfession, event: event_val, remark })
    })
    .then(r => r.json())
    .then(data => {
        if (data.error) {
            showToast(data.error, 'error');
        } else {
            showToast(data.isNew ? '🎉 报名成功！' : '✅ 已更新报名信息', 'success');
            document.getElementById('nameInput').value = '';
            document.getElementById('remarkInput').value = '';
            document.querySelectorAll('.prof-btn').forEach(b => b.classList.remove('selected'));
            selectedProfession = null;
            loadMembers();
        }
    })
    .catch(() => showToast('网络错误', 'error'))
    .finally(() => {
        btn.disabled = false;
        btn.textContent = '确认报名';
    });
}

// ============ 删除成员 ============
function deleteMember(id) {
    if (!confirm('确定要删除这个成员吗？')) return;
    fetch('/api/members/' + id, { method: 'DELETE' })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                showToast('已删除', 'success');
                loadMembers();
            } else {
                showToast(data.error || '删除失败', 'error');
            }
        });
}

// ============ 批量导入 ============
function showBatchModal() {
    document.getElementById('batchModal').classList.add('show');
}
function hideBatchModal() {
    document.getElementById('batchModal').classList.remove('show');
}
function doBatchImport() {
    const text = document.getElementById('batchText').value.trim();
    if (!text) { showToast('请输入名单内容', 'error'); return; }

    fetch('/api/batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, defaultEvent: currentEvent === 'thursday' ? 'thursday' : (currentEvent === 'saturday' ? 'saturday' : 'both') })
    })
    .then(r => r.json())
    .then(data => {
        if (data.error) {
            showToast(data.error, 'error');
        } else {
            const msg = `✅ 成功导入 ${data.imported}/${data.totalLines} 人`;
            showToast(msg, 'success');
            if (data.errors && data.errors.length > 0) {
                setTimeout(() => {
                    alert('以下行导入失败：\n• ' + data.errors.slice(0, 5).join('\n• ') + (data.errors.length > 5 ? '\n...' : ''));
                }, 300);
            }
            hideBatchModal();
            document.getElementById('batchText').value = '';
            loadMembers();
        }
    });
}

// ============ 复制名单 ============
function copyList() {
    let eventParam = '';
    if (currentEvent === 'thursday' || currentEvent === 'saturday') {
        eventParam = '&event=' + currentEvent;
    }
    fetch('/api/export?format=text' + eventParam)
        .then(r => r.json())
        .then(data => {
            navigator.clipboard.writeText(data.content).then(() => {
                showToast('📋 名单已复制到剪贴板', 'success');
            }).catch(() => {
                // 降级方案
                const ta = document.createElement('textarea');
                ta.value = data.content;
                document.body.appendChild(ta);
                ta.select();
                document.execCommand('copy');
                document.body.removeChild(ta);
                showToast('📋 名单已复制', 'success');
            });
        });
}

// ============ SSE 实时推送 ============
function connectSSE() {
    const es = new EventSource('/api/stream');

    es.addEventListener('connected', () => {
        console.log('SSE 已连接');
    });

    es.addEventListener('member_updated', (e) => {
        // 成员新增/更新 -> 刷新
        loadMembers();
    });

    es.addEventListener('member_removed', (e) => {
        loadMembers();
    });

    es.addEventListener('batch_imported', (e) => {
        loadMembers();
        showToast('📥 有新的批量导入', 'info');
    });

    es.onerror = () => {
        console.log('SSE 断开，自动重连...');
    };
}

// ============ Toast ============
let toastTimer = null;
function showToast(msg, type = 'info') {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.className = 'toast show ' + type;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove('show'), 2500);
}
