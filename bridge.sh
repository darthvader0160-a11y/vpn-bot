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
echo iptables-persistent iptables-persistent/autosave_v4 boolean true | debconf-set-selections
echo iptables-persistent iptables-persistent/autosave_v6 boolean true | debconf-set-selections
apt-get update -qq
apt-get install -y -qq iptables iptables-persistent >/dev/null

cat > /etc/sysctl.d/99-bridge.conf <<'EOF'
net.ipv4.ip_forward=1
net.core.default_qdisc=fq
net.ipv4.tcp_congestion_control=bbr
EOF
sysctl --system >/dev/null

# Свои правила держим в отдельных цепочках, чтобы повторный запуск ничего не дублировал.
iptables -t nat -N VPNBRIDGE 2>/dev/null || iptables -t nat -F VPNBRIDGE
iptables -t nat -N VPNBRIDGE_OUT 2>/dev/null || iptables -t nat -F VPNBRIDGE_OUT
iptables -t nat -C PREROUTING -j VPNBRIDGE 2>/dev/null || iptables -t nat -A PREROUTING -j VPNBRIDGE
iptables -t nat -C POSTROUTING -j VPNBRIDGE_OUT 2>/dev/null || iptables -t nat -A POSTROUTING -j VPNBRIDGE_OUT
for p in $PORTS; do
  iptables -t nat -A VPNBRIDGE -p tcp --dport "$p" -j DNAT --to-destination "$NL:$p"
done
iptables -t nat -A VPNBRIDGE_OUT -d "$NL" -p tcp -j MASQUERADE
iptables -P FORWARD ACCEPT
netfilter-persistent save >/dev/null

echo
echo "✅ Мост готов: порты $PORTS → $NL"
echo "IP этого сервера: $(curl -4fsS --max-time 10 https://api.ipify.org || hostname -I | awk '{print $1}')"
