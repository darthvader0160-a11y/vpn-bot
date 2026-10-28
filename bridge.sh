#!/usr/bin/env bash
# Мост на российском сервере: принимает VPN-подключения и пересылает их как есть на сервер в Нидерландах.
# Шифрование и ключи остаются прежними, сам мост ничего не расшифровывает.
# Запуск на российском сервере:  bash bridge.sh <IP сервера в Нидерландах>
set -euo pipefail
NL=${1:?Укажи IP сервера в Нидерландах: bash bridge.sh 1.2.3.4}
PORTS="443 8443 2083 39443 47443"

[ "$(id -u)" = 0 ] || { echo "Нужны права root"; exit 1; }
echo "$NL" > /etc/vpn-bridge.conf   # для autoupdate.sh
export DEBIAN_FRONTEND=noninteractive
if ! command -v netfilter-persistent >/dev/null; then
  echo iptables-persistent iptables-persistent/autosave_v4 boolean true | debconf-set-selections
  echo iptables-persistent iptables-persistent/autosave_v6 boolean true | debconf-set-selections
  apt-get -o DPkg::Lock::Timeout=900 update -qq
  apt-get -o DPkg::Lock::Timeout=900 install -y -qq iptables iptables-persistent >/dev/null
fi

cat > /etc/sysctl.d/99-bridge.conf <<'EOF'
net.ipv4.ip_forward=1
net.core.default_qdisc=fq
net.ipv4.tcp_congestion_control=bbr
EOF
sysctl --system >/dev/null

# Если поднялся туннель AmneziaWG — пересылаем через него: ТСПУ на выходе из Москвы видит только шум.
# Не поднялся — по-старому напрямую, VPN не пропадает. autoupdate.sh повторяет попытку каждые 2 минуты.
if bash "$(dirname "$0")/awg-bridge.sh" "$NL"; then
  TARGET=10.66.0.1; VIA="туннель AmneziaWG"
else
  TARGET=$NL; VIA="напрямую"
fi

# Свои правила держим в отдельных цепочках, чтобы повторный запуск ничего не дублировал.
iptables -t nat -N VPNBRIDGE 2>/dev/null || iptables -t nat -F VPNBRIDGE
iptables -t nat -N VPNBRIDGE_OUT 2>/dev/null || iptables -t nat -F VPNBRIDGE_OUT
iptables -t nat -C PREROUTING -j VPNBRIDGE 2>/dev/null || iptables -t nat -A PREROUTING -j VPNBRIDGE
iptables -t nat -C POSTROUTING -j VPNBRIDGE_OUT 2>/dev/null || iptables -t nat -A POSTROUTING -j VPNBRIDGE_OUT
for p in $PORTS; do
  iptables -t nat -A VPNBRIDGE -p tcp --dport "$p" -j DNAT --to-destination "$TARGET:$p"
done
iptables -t nat -A VPNBRIDGE_OUT -d "$TARGET" -p tcp -j MASQUERADE
iptables -P FORWARD ACCEPT
# Внутри туннеля пакет меньше — подрезаем размер TCP-сегментов, иначе крупные страницы зависают.
iptables -t mangle -C FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null ||
  iptables -t mangle -A FORWARD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
netfilter-persistent save >/dev/null

echo
echo "✅ Мост готов ($VIA): порты $PORTS → $TARGET"
echo "IP этого сервера: $(curl -4fsS --max-time 10 https://api.ipify.org || hostname -I | awk '{print $1}')"
