#!/usr/bin/env bash
# Самопроверка VPN прямо на сервере: подключается к своим же подключениям как клиент V2Box.
# Запуск: bash /opt/vpn-bot/selftest.sh
cd "$(dirname "$0")"
XRAY=$(docker exec 3xui sh -c 'ls /app/bin/xray-linux-* 2>/dev/null | head -1')
[ -n "$XRAY" ] || { echo "Не нашёл Xray в контейнере 3xui"; exit 1; }

echo "== Порты =="
ss -tlnp | grep -E ':(443|8443) ' || echo "Xray не слушает 443/8443!"

echo "== Сайт маскировки =="
SNI=$(grep -E '^REALITY_SNI=' .env | cut -d= -f2)
curl -sS -o /dev/null -m 10 -w "https://$SNI → %{http_code}, TLS ok\n" "https://$SNI" || echo "Сервер не может открыть $SNI — Reality не будет работать"

echo "== Подключения =="
docker compose exec -T bot python -m bot.selftest | while read -r key port rest; do
  docker cp "data/selftest-$key.json" "3xui:/tmp/selftest-$key.json" >/dev/null
  docker exec -d 3xui "$XRAY" run -c "/tmp/selftest-$key.json"
  sleep 3
  code=$(curl -sS -o /dev/null -m 15 -w '%{http_code}' --socks5-hostname "127.0.0.1:$port" https://www.google.com/generate_204 2>&1)
  if [ "$code" = "204" ]; then echo "✅ $key ($rest): трафик идёт"; else echo "❌ $key ($rest): $code"; fi
  docker exec 3xui pkill -f "selftest-$key.json" 2>/dev/null
done

echo "== Последние ошибки Xray =="
docker logs --since 2m 3xui 2>&1 | grep -iE 'error|reality|invalid|failed' | tail -10
