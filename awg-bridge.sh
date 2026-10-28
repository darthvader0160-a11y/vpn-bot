#!/usr/bin/env bash
# Туннель AmneziaWG, сторона моста (Москва). Вызывается из bridge.sh.
# Отправляет свой публичный ключ боту в Нидерландах, получает его ключ и параметры маскировки,
# поднимает awg0 и проверяет связь. Код выхода 0 — туннель работает.
set -euo pipefail
NL=${1:?IP сервера в Нидерландах}
DIR=/etc/amnezia/amneziawg
CONF=$DIR/awg0.conf

if ! command -v awg >/dev/null; then
  echo "ставлю AmneziaWG"
  export DEBIAN_FRONTEND=noninteractive
  apt-get install -y -qq software-properties-common >/dev/null
  add-apt-repository -y ppa:amnezia/ppa >/dev/null
  apt-get update -qq
  apt-get install -y -qq "linux-headers-$(uname -r)" amneziawg amneziawg-tools python3 >/dev/null
fi

mkdir -p "$DIR" && chmod 700 "$DIR"
[ -f "$DIR/bridge.key" ] || { awg genkey > "$DIR/bridge.key"; chmod 600 "$DIR/bridge.key"; }
PUB=$(awg pubkey < "$DIR/bridge.key")

RESP=$(curl -fsS -m 15 -X POST --data "$PUB" "http://$NL:8899/awg-exchange") || {
  echo "Нидерланды пока не ответили на обмен ключами"; exit 1; }
j() { python3 -c "import json,sys;print(json.loads(sys.argv[1])['$1'])" "$RESP"; }

NEW=$(mktemp)
cat > "$NEW" <<EOF
[Interface]
Address = 10.66.0.2/30
PrivateKey = $(cat "$DIR/bridge.key")
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

[Peer]
PublicKey = $(j pubkey)
Endpoint = $NL:$(j port)
AllowedIPs = 10.66.0.1/32
PersistentKeepalive = 25
EOF

if ! cmp -s "$NEW" "$CONF"; then
  install -m 600 "$NEW" "$CONF"
  systemctl enable -q awg-quick@awg0
  systemctl restart awg-quick@awg0
fi
rm -f "$NEW"

# Нидерландам нужно до 2 минут, чтобы добавить наш ключ (их autoupdate) — ждём связь до 3 минут.
for _ in $(seq 1 36); do
  ping -c1 -W2 10.66.0.1 >/dev/null 2>&1 && { echo "туннель до Нидерландов работает"; exit 0; }
  sleep 5
done
echo "туннель поднят, но Нидерланды не отвечают"
exit 1
