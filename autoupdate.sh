#!/usr/bin/env bash
# Автообновление с GitHub. Ставится один раз:  bash /opt/vpn-bot/autoupdate.sh install
# Дальше cron каждые 2 минуты запускает его без аргументов: если на GitHub новый код —
# забирает его и перезапускает то, что работает на этом сервере (бот или мост).
set -uo pipefail
DIR=/opt/vpn-bot
LOG=/var/log/vpn-autoupdate.log

if [ "${1:-}" = "install" ]; then
  cat > /etc/cron.d/vpn-autoupdate <<EOF
*/2 * * * * root bash $DIR/autoupdate.sh >> $LOG 2>&1
EOF
  chmod 644 /etc/cron.d/vpn-autoupdate
  echo "✅ Автообновление включено: сервер проверяет GitHub каждые 2 минуты. Журнал: $LOG"
  exit 0
fi

cd "$DIR" || exit 1
exec 9>/run/vpn-autoupdate.lock
flock -n 9 || exit 0   # предыдущее обновление ещё идёт

# Туннель между серверами: Нидерланды подхватывают ключ моста, мост переподключается, если связь пропала.
if [ -f .env ] && grep -q '^BRIDGE_HOST=.' .env; then
  bash awg-server.sh || echo "$(date '+%F %T') awg-server.sh: ошибка"
fi
# Повтор не чаще раза в 15 минут, чтобы не дёргать мост, если UDP до Нидерландов закрыт.
RETRY=/run/vpn-bridge-retry
if [ -f /etc/vpn-bridge.conf ] && ! ping -c1 -W2 10.66.0.1 >/dev/null 2>&1 &&
   [ -z "$(find "$RETRY" -mmin -15 2>/dev/null)" ]; then
  touch "$RETRY"
  bash bridge.sh "$(cat /etc/vpn-bridge.conf)" | sed "s/^/$(date '+%F %T') мост: /" | tail -2
fi

git fetch -q origin main || exit 0
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] && exit 0

echo "$(date '+%F %T') обновляю $(git rev-parse --short HEAD) → $(git rev-parse --short origin/main)"
git reset -q --hard origin/main   # .env, data/ и прочие файлы сервера в git не входят и не трогаются

if [ -f .env ] && grep -q '^BOT_TOKEN=' .env; then
  docker compose up -d --build bot && echo "  бот перезапущен"
fi
if [ -f /etc/vpn-bridge.conf ]; then
  bash bridge.sh "$(cat /etc/vpn-bridge.conf)" >/dev/null && echo "  мост обновлён"
fi
