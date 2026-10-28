#!/usr/bin/env bash
# Установка VPN-сервера с ботом на чистый Ubuntu 22.04/24.04 или Debian 12.
# Запуск на сервере из папки бота:  bash install.sh
# Повторный запуск безопасен: настройки из .env сохраняются, бот пересобирается.
set -euo pipefail
trap 'printf "\n\033[1;31m✖ Установка прервалась на строке %s. Пришли этот экран разработчику.\033[0m\n" "$LINENO"' ERR
cd "$(dirname "$0")"

say()  { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
fail() { printf '\n\033[1;31m✖ %s\033[0m\n' "$*"; exit 1; }
rnd()  { tr -dc 'a-zA-Z0-9' </dev/urandom | head -c "$1"; }
port() { shuf -i 20000-60000 -n 1; }

[ "$(id -u)" = 0 ] || fail "Нужны права root: выполни  sudo bash install.sh"
command -v apt-get >/dev/null || fail "Скрипт рассчитан на Ubuntu или Debian"

# ---------- настройки ----------
touch .env && chmod 600 .env
get() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- || true; }
put() { if grep -qE "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi; }

BOT_TOKEN=${BOT_TOKEN:-$(get BOT_TOKEN)}
ADMIN_IDS=${ADMIN_IDS:-$(get ADMIN_IDS)}
[ -n "$BOT_TOKEN" ] || read -rp "Токен бота от @BotFather: " BOT_TOKEN
[ -n "$ADMIN_IDS" ] || read -rp "Твой Telegram ID (узнать у @userinfobot): " ADMIN_IDS
[[ "$BOT_TOKEN" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]] || fail "Токен бота выглядит неверно"
[[ "$ADMIN_IDS" =~ ^[0-9,\ ]+$ ]] || fail "Telegram ID — это только цифры"
put BOT_TOKEN "$BOT_TOKEN"
put ADMIN_IDS "$ADMIN_IDS"

[ -n "$(get BRAND)" ]       || put BRAND "Наш VPN"
[ -n "$(get SERVER_NAME)" ] || put SERVER_NAME "🇳🇱 Нидерланды"
[ -n "$(get MAX_USERS)" ]   || put MAX_USERS 50
[ -n "$(get REALITY_SNI)" ] || put REALITY_SNI "www.samsung.com"
[ -n "$(get XHTTP_PORT)" ]  || put XHTTP_PORT 443
[ -n "$(get TCP_PORT)" ]    || put TCP_PORT 8443
[ -n "$(get SUB_PORT)" ]    || put SUB_PORT "$(port)"
[ -n "$(get SUB_PATH)" ]    || put SUB_PATH "$(rnd 12)"
[ -n "$(get PANEL_PORT)" ]  || put PANEL_PORT "$(port)"
[ -n "$(get PANEL_PATH)" ]  || put PANEL_PATH "$(rnd 14)"
[ -n "$(get PANEL_USER)" ]  || put PANEL_USER "adm$(rnd 6)"
[ -n "$(get PANEL_PASS)" ]  || put PANEL_PASS "$(rnd 20)"

# ---------- система ----------
say "Обновляю систему и ставлю Docker (2–5 минут)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq curl ca-certificates ufw >/dev/null
command -v docker >/dev/null || curl -fsSL https://get.docker.com | sh
systemctl enable --now docker >/dev/null

say "Включаю ускорение сети (BBR)"
cat > /etc/sysctl.d/99-vpn.conf <<'EOF'
net.core.default_qdisc=fq
net.ipv4.tcp_congestion_control=bbr
net.ipv4.tcp_fastopen=3
EOF
sysctl --system >/dev/null

PUBLIC_IP=$(curl -4fsS --max-time 10 https://api.ipify.org || curl -4fsS --max-time 10 https://ifconfig.me || true)
[ -n "$PUBLIC_IP" ] || fail "Не удалось узнать внешний IP сервера"
[ -n "$(get SERVER_HOST)" ] || put SERVER_HOST "$PUBLIC_IP"

# ---------- файрвол: снаружи видны только SSH, VPN и подписки ----------
say "Настраиваю файрвол"
SSH_PORT=$( (sshd -T 2>/dev/null || /usr/sbin/sshd -T 2>/dev/null || true) | awk '/^port /{print $2; exit}' || true)
SSH_PORT=${SSH_PORT:-22}
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow "$SSH_PORT"/tcp >/dev/null
ufw allow "$(get XHTTP_PORT)"/tcp >/dev/null
ufw allow "$(get TCP_PORT)"/tcp >/dev/null
ufw allow "$(get SUB_PORT)"/tcp >/dev/null
for p in 2053 2083 39443 47443; do ufw allow "$p"/tcp >/dev/null; done   # запасные варианты (bot/vpn.py NODE_SPECS)
ufw --force enable >/dev/null

# ---------- панель 3x-ui ----------
say "Запускаю панель 3x-ui"
mkdir -p xui-db xui-cert data
docker compose pull -q 3xui
docker compose up -d 3xui
for i in $(seq 1 30); do
  docker exec 3xui /app/x-ui setting -show >/dev/null 2>&1 && break
  sleep 2
done
# Панель слушает только 127.0.0.1: из интернета её не видно, вход через SSH-туннель.
docker exec 3xui /app/x-ui setting \
  -username "$(get PANEL_USER)" -password "$(get PANEL_PASS)" \
  -port "$(get PANEL_PORT)" -webBasePath "/$(get PANEL_PATH)/" -listenIP 127.0.0.1 >/dev/null
if [ -z "$(get PANEL_TOKEN)" ]; then
  TOKEN=$(docker exec 3xui /app/x-ui setting -getApiToken 2>/dev/null | awk '/apiToken:/{print $2}' | tail -1 || true)
  [ -n "$TOKEN" ] || fail "Панель не выдала API-токен. Посмотри: docker compose logs 3xui"
  put PANEL_TOKEN "$TOKEN"
fi
put PANEL_URL "http://127.0.0.1:$(get PANEL_PORT)/$(get PANEL_PATH)"
docker restart 3xui >/dev/null

# ---------- бот ----------
say "Собираю и запускаю бота"
docker compose up -d --build bot
sleep 15
docker compose logs --tail 15 bot

cat > ДОСТУПЫ.txt <<EOF
Сервер: $(get SERVER_HOST)
Бот: напиши ему /start в Telegram

Панель 3x-ui (из интернета закрыта, только через SSH-туннель):
  1) На своём компьютере в Терминале:  ssh -L 8080:127.0.0.1:$(get PANEL_PORT) root@$(get SERVER_HOST)
  2) Не закрывая Терминал, открой в браузере:  http://127.0.0.1:8080/$(get PANEL_PATH)/
  Логин:  $(get PANEL_USER)
  Пароль: $(get PANEL_PASS)
EOF
chmod 600 ДОСТУПЫ.txt

say "Готово!"
cat ДОСТУПЫ.txt
echo
echo "Эти данные сохранены в $(pwd)/ДОСТУПЫ.txt"
