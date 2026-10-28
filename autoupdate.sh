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
