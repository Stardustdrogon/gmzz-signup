#!/bin/bash
# 内网隧道自动重连脚本
APP_DIR="/Coze/Drive/诡秘之主/活动报名系统"
LOG_FILE="$APP_DIR/tunnel.log"
PID_FILE="$APP_DIR/tunnel.pid"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

# 杀掉旧进程
[ -f "$PID_FILE" ] && kill $(cat "$PID_FILE") 2>/dev/null
pkill -f "a.pinggy.io" 2>/dev/null
sleep 1

# 生成 SSH key（首次运行）
if [ ! -f /tmp/serveo_key ]; then
    ssh-keygen -t rsa -f /tmp/serveo_key -N "" -q 2>/dev/null
fi

# 循环重连
while true; do
    log "=== 启动 pinggy 隧道 ==="
    ssh -i /tmp/serveo_key \
        -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile=/dev/null \
        -o ServerAliveInterval=30 \
        -o ServerAliveCountMax=3 \
        -o ExitOnForwardFailure=yes \
        -p 443 -N -T \
        -R 80:localhost:3000 a.pinggy.io \
        >> "$LOG_FILE" 2>&1
    
    log "隧道断开，5秒后重连..."
    sleep 5
done
