#!/usr/bin/env bash
# 自检/拉起：已经跑着就什么都不做
set -u
cd "$(dirname "$0")"
ADDR=$(python3 -c 'import config;print(f"{config.HOST}:{config.PORT}")')
if curl -s -m 2 "http://$ADDR/api/status" >/dev/null 2>&1; then
  echo "已经在跑：http://$ADDR"
  exit 0
fi
setsid nohup python3 server.py >> river.log 2>&1 < /dev/null &
sleep 2
if curl -s -m 2 "http://$ADDR/api/status" >/dev/null 2>&1; then
  echo "已拉起：http://$ADDR"
else
  echo "起不来，看 river.log"; tail -5 river.log
  exit 1
fi
