#!/bin/bash
cd /Coze/Drive/诡秘之主/活动报名系统
nohup /usr/bin/python3 app.py > /Coze/Drive/诡秘之主/活动报名系统/app.log 2>&1 &
echo $! > /Coze/Drive/诡秘之主/活动报名系统/app.pid
echo "服务已启动，PID: $(cat /Coze/Drive/诡秘之主/活动报名系统/app.pid)"
