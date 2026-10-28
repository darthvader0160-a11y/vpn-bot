"""Самопроверка: собирает клиентские конфиги Xray для первого активного пользователя.

python -m bot.selftest  →  /data/selftest-<подключение>.json (socks5 на 127.0.0.1:1080N).
selftest.sh запускает их рядом с сервером и проверяет, ходит ли трафик через VPN.
"""
import asyncio
import json
import os

from . import config
from .db import DB
from .panel import Panel
from .vpn import VPN, Node


def client_config(node: Node, user_uuid: str, host: str, socks_port: int) -> dict:
    stream = {
        "network": node.network,
        "security": "reality",
        "realitySettings": {
            "serverName": node.sni,
            "fingerprint": node.fp,
            "publicKey": node.pbk,
            "shortId": node.sid,
            "spiderX": "/",
        },
    }
    if node.network == "xhttp":
        stream["xhttpSettings"] = {"path": node.path or "/", "mode": "auto"}
    user = {"id": user_uuid, "encryption": "none"}
    if node.flow:
        user["flow"] = node.flow
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [{"listen": "127.0.0.1", "port": socks_port, "protocol": "socks", "settings": {"udp": False}}],
        "outbounds": [
            {
                "protocol": "vless",
                "settings": {"vnext": [{"address": host, "port": node.port, "users": [user]}]},
                "streamSettings": stream,
            }
        ],
    }


async def main():
    cfg = config.load()
    db = DB(cfg.db_path)
    panel = Panel(cfg.panel_url, cfg.panel_token)
    vpn = VPN(cfg, db, panel)
    try:
        await vpn.ensure_nodes()
    finally:
        await panel.close()
    users = db.list_users(("active",))
    if not users:
        raise SystemExit("Нет активных пользователей: сначала напиши боту /start")
    uuids = {c["node"]: c["uuid"] for c in db.clients(users[0]["tg_id"])}
    # Сначала напрямую, потом через мост (если он настроен) — так видно, рабочий ли сам мост.
    hosts = [("", cfg.server_host)] + ([("bridge-", cfg.bridge_host)] if cfg.bridge_host else [])
    n = 0
    for prefix, host in hosts:
        for node in vpn.nodes:
            if node.key not in uuids:
                continue
            name = f"{prefix}{node.key}"
            path = os.path.join(cfg.data_dir, f"selftest-{name}.json")
            with open(path, "w") as f:
                json.dump(client_config(node, uuids[node.key], host, 10800 + n), f, indent=2)
            print(f"{name} {10800 + n} via={host} port={node.port}")
            n += 1


if __name__ == "__main__":
    asyncio.run(main())
