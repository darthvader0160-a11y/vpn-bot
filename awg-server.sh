#!/usr/bin/env bash
# Туннель AmneziaWG, сторона Нидерландов. Запускается autoupdate.sh каждые 2 минуты, безопасен при повторе.
# Мост в Москве присылает свой публичный ключ боту (порт 8899, только с IP моста),
# бот кладёт его в data/awg-peer.pub, а этот скрипт добавляет его в туннель.
set -euo pipefail
cd /opt/vpn-bot
BRIDGE=$(grep -E '^BRIDGE_HOST=' .env | cut -d= -f2 || true)
[ -n "$BRIDGE" ] || exit 0

DIR=/etc/amnezia/amneziawg
CONF=$DIR/awg0.conf
STATE=data/awg-server.json
PEER=data/awg-peer.pub

if ! command -v awg >/dev/null; then
  echo "ставлю AmneziaWG"
  export DEBIAN_FRONTEND=noninteractive
  apt-get install -y -qq software-properties-common >/dev/null
  add-apt-repository -y ppa:amnezia/ppa >/dev/null
  apt-get update -qq
  apt-get install -y -qq "linux-headers-$(uname -r)" amneziawg amneziawg-tools >/dev/null
fi

mkdir -p "$DIR" && chmod 700 "$DIR"
if [ ! -f "$DIR/server.key" ] || [ ! -f "$STATE" ]; then
  awg genkey > "$DIR/server.key"; chmod 600 "$DIR/server.key"
  r() { shuf -i "$1"-"$2" -n 1; }
  # Параметры маскировки должны совпадать на обеих сторонах — мост получает их при обмене ключами.
  cat > "$STATE" <<EOF
{"pubkey": "$(awg pubkey < "$DIR/server.key")", "port": $(r 30000 60000),
 "jc": 4, "jmin": 40, "jmax": 70, "s1": $(r 15 150), "s2": $(r 15 150),
 "h1": $(r 100000 999999999), "h2": $(r 1000000000 1999999999),
 "h3": $(r 2000000000 2999999999), "h4": $(r 3000000000 4000000000)}
EOF
fi
j() { python3 -c "import json,sys;print(json.load(open('$STATE'))['$1'])"; }

NEW=$(mktemp)
cat > "$NEW" <<EOF
[Interface]
Address = 10.66.0.1/30
ListenPort = $(j port)
PrivateKey = $(cat "$DIR/server.key")
MTU = 1380
Jc = $(j jc)
Jmin = $(j jmin)
Jmax = $(j jmax)
S1 = $(j s1)
S2 = $(j s2)
H1 = $(j h1)
H2 = $(j h2)
H3 = $(j h3)
H4 = $(j h4)
EOF
if [ -s "$PEER" ]; then
  printf '\n[Peer]\nPublicKey = %s\nAllowedIPs = 10.66.0.2/32\n' "$(tr -d '\n' < "$PEER")" >> "$NEW"
fi

ufw allow "$(j port)"/udp >/dev/null
ufw allow in on awg0 >/dev/null
ufw allow from "$BRIDGE" to any port 8899 proto tcp >/dev/null

if ! cmp -s "$NEW" "$CONF"; then
  install -m 600 "$NEW" "$CONF"
  systemctl enable -q awg-quick@awg0
  systemctl restart awg-quick@awg0 && echo "туннель AmneziaWG (Нидерланды) обновлён"
fi
rm -f "$NEW"
