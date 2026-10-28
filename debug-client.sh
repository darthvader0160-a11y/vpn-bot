#!/usr/bin/env bash
# На минуту включает подробный журнал Xray и показывает, что сервер видит при подключении клиента.
# Запуск на нидерландском сервере: bash /opt/vpn-bot/debug-client.sh [IP моста или клиента]
# WAIT=180 — сколько секунд ждать подключения (по умолчанию 60).
cd "$(dirname "$0")"
SRC=${1:-$(grep -E '^BRIDGE_HOST=' .env | cut -d= -f2)}
docker compose exec -T bot python -m bot.xraylog debug
START=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo
WAIT=${WAIT:-60}
echo "▶ ПОДКЛЮЧАЙСЯ СЕЙЧАС в приложении и открой любой сайт. Жду $WAIT секунд…"
sleep "$WAIT"
echo "== Что видел сервер (подключения с ${SRC:-любых адресов}) =="
docker logs --since "$START" 3xui 2>&1 | grep -E "${SRC:-.}" | grep -viE "non-443|as the target" | tail -30
docker compose exec -T bot python -m bot.xraylog warning
