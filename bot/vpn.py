"""Подключения на сервере, выдача ключей и сборка ссылок vless:// для V2Box.

Источник правды о подключениях — сама панель: бот ищет там inbound с примечанием vpn-<ключ>,
создаёт его при первом запуске и читает из него параметры Reality (ключ, SNI, shortId).
Поэтому если поменять SNI в панели, ссылки в подписке обновятся сами.
"""
import base64
import json
import logging
import secrets
import uuid
from dataclasses import dataclass
from urllib.parse import quote, urlencode

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

from .config import Config
from .db import DB
from .panel import Panel, PanelError

log = logging.getLogger(__name__)
VISION = "xtls-rprx-vision"


@dataclass
class Node:
    key: str            # xhttp | tcp
    title: str          # подпись в V2Box
    inbound_id: int
    port: int
    network: str
    flow: str
    sni: str
    pbk: str
    sid: str
    fp: str
    path: str = ""


# Подключения, которые бот держит на сервере. Порядок = порядок в подписке (первое — основное).
# Без port/sni берутся XHTTP_PORT/TCP_PORT и REALITY_SNI из настроек. Порты всех вариантов открывает install.sh.
NODE_SPECS = [
    {"key": "xhttp", "title": "Основной", "network": "xhttp", "flow": ""},
    {"key": "tcp", "title": "Резервный", "network": "tcp", "flow": VISION},
    {"key": "t-apple", "title": "Вариант 4", "network": "tcp", "flow": VISION, "port": 2083, "sni": "www.apple.com"},
    {"key": "x-high", "title": "Вариант 5", "network": "xhttp", "flow": "", "port": 39443, "sni": "ya.ru"},
    {"key": "t-high", "title": "Вариант 6", "network": "tcp", "flow": VISION, "port": 47443, "sni": "www.samsung.com"},
]
# Эти подключения дублируются в подписке по IPv6, если задан SERVER_HOST6.
IPV6_KEYS = ("xhttp", "x-high", "t-high")
# Варианты через мост в России (проверено 28.10.2026: из РФ проходит tcp на 47443).
# Третий элемент — отпечаток TLS: V2Box с "chrome" шлёт большой ClientHello (постквантовый ключ),
# который по дороге режется ("failed to read client hello"), с "safari" он короче и проходит.
BRIDGE_VARIANTS = [
    ("t-apple", "🇷🇺 Основной", None),       # 2083: прошёл самопроверку через мост 28.10 17:48
    ("t-high", "🇷🇺 Резервный 1", None),     # 47443
    ("x-high", "🇷🇺 Резервный 2", None),     # 39443
    ("t-apple", "🇷🇺 Для V2Box", "safari"),
]


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def x25519_pair() -> tuple[str, str]:
    """Пара ключей Reality в том же формате, что выдаёт `xray x25519`."""
    key = X25519PrivateKey.generate()
    priv = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    pub = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return _b64url(priv), _b64url(pub)


def inbound_payload(spec: dict, port: int, sni: str) -> dict:
    priv, pub = x25519_pair()
    reality = {
        "show": False,
        "xver": 0,
        "target": f"{sni}:443",
        "serverNames": [sni],
        "privateKey": priv,
        "minClientVer": "",
        "maxClientVer": "",
        "maxTimediff": 0,
        "shortIds": [secrets.token_hex(4), secrets.token_hex(8)],
        "settings": {"publicKey": pub, "fingerprint": "chrome", "serverName": "", "spiderX": "/"},
    }
    stream = {"network": spec["network"], "security": "reality", "externalProxy": [], "realitySettings": reality}
    if spec["network"] == "xhttp":
        stream["xhttpSettings"] = {
            "path": "/" + secrets.token_hex(6),
            "host": "",
            "headers": {},
            "mode": "auto",
            "xPaddingBytes": "100-1000",
            "noSSEHeader": False,
            "scMaxBufferedPosts": 30,
            "scMaxEachPostBytes": "1000000",
        }
    else:
        stream["tcpSettings"] = {"acceptProxyProtocol": False, "header": {"type": "none"}}
    return {
        "remark": f"vpn-{spec['key']}",
        "enable": True,
        "listen": "",
        "port": port,
        "protocol": "vless",
        "expiryTime": 0,
        "total": 0,
        "up": 0,
        "down": 0,
        "settings": json.dumps({"clients": [], "decryption": "none", "fallbacks": []}),
        "streamSettings": json.dumps(stream),
        "sniffing": json.dumps(
            {"enabled": True, "destOverride": ["http", "tls", "quic"], "metadataOnly": False, "routeOnly": True}
        ),
    }


def node_from_inbound(spec: dict, inbound: dict) -> Node:
    stream = inbound["streamSettings"]
    if isinstance(stream, str):
        stream = json.loads(stream)
    r = stream["realitySettings"]
    rs = r.get("settings") or {}
    sni = (r.get("serverNames") or [""])[0]
    return Node(
        key=spec["key"],
        title=spec["title"],
        inbound_id=inbound["id"],
        port=inbound["port"],
        network=stream.get("network", spec["network"]),
        flow=spec["flow"],
        sni=sni,
        pbk=rs.get("publicKey", ""),
        sid=(r.get("shortIds") or [""])[0],
        fp=rs.get("fingerprint") or "chrome",
        path=(stream.get("xhttpSettings") or {}).get("path", ""),
    )


def vless_link(node: Node, user_uuid: str, host: str, label: str, fp: str | None = None) -> str:
    q = {"encryption": "none", "security": "reality", "sni": node.sni, "fp": fp or node.fp, "pbk": node.pbk, "sid": node.sid}
    if node.flow:
        q["flow"] = node.flow
    q["type"] = node.network
    if node.network == "xhttp":
        q["path"] = node.path or "/"
        q["mode"] = "auto"
    else:
        q["headerType"] = "none"
    q["spx"] = "/"
    return f"vless://{user_uuid}@{host}:{node.port}?{urlencode(q, quote_via=quote)}#{quote(label)}"


class VPN:
    def __init__(self, cfg: Config, db: DB, panel: Panel):
        self.cfg, self.db, self.panel = cfg, db, panel
        self.nodes: list[Node] = []

    async def ensure_nodes(self):
        """Находит или создаёт подключения в панели."""
        inbounds = await self.panel.list_inbounds()
        default_ports = {"xhttp": self.cfg.xhttp_port, "tcp": self.cfg.tcp_port}
        nodes = []
        for spec in NODE_SPECS:
            found = next((i for i in inbounds if i.get("remark") == f"vpn-{spec['key']}"), None)
            if not found:
                port = spec.get("port") or default_ports[spec["key"]]
                log.info("Создаю подключение vpn-%s на порту %s", spec["key"], port)
                await self.panel.add_inbound(inbound_payload(spec, port, spec.get("sni") or self.cfg.reality_sni))
                inbounds = await self.panel.list_inbounds()
                found = next(i for i in inbounds if i.get("remark") == f"vpn-{spec['key']}")
            nodes.append(node_from_inbound(spec, found))
        self.nodes = nodes

    # --- клиенты ---
    def _client(self, tg_id: int, node: Node, user_uuid: str, email: str, token: str, enable: bool) -> dict:
        return {
            "id": user_uuid,
            "security": "",
            "flow": node.flow,
            "email": email,
            "limitIp": 0,
            "totalGB": 0,
            "expiryTime": 0,
            "enable": enable,
            "tgId": tg_id,
            # Панель 3.11 требует уникальный subId у каждого клиента; подписку бот раздаёт сам по token.
            "subId": f"{token}-{node.key}",
            "comment": "",
            "reset": 0,
        }

    async def _add(self, tg_id: int, node: Node, token: str, enable: bool = True):
        email = f"u{tg_id}-{node.key}"
        user_uuid = str(uuid.uuid4())
        client = self._client(tg_id, node, user_uuid, email, token, enable)
        try:
            await self.panel.add_client(node.inbound_id, client)
        except PanelError as first:
            # Клиент с таким email мог остаться в панели (например, после переустановки бота) — пересоздаём.
            try:
                await self.panel.delete_client(email)
                await self.panel.add_client(node.inbound_id, client)
            except PanelError as second:
                raise PanelError(f"создание клиента {email}: {first}") from second
        self.db.set_client(tg_id, node.key, user_uuid, email)

    async def issue(self, tg_id: int) -> str:
        """Создаёт ключи на всех подключениях и делает пользователя активным. Возвращает токен подписки."""
        token = secrets.token_urlsafe(18)
        for node in self.nodes:
            await self._add(tg_id, node, token)
        self.db.activate(tg_id, token)
        return token

    async def revoke(self, tg_id: int):
        for c in self.db.clients(tg_id):
            try:
                await self.panel.delete_client(c["email"])
            except PanelError as e:
                log.warning("Не удалось удалить %s: %s", c["email"], e)
        self.db.drop_clients(tg_id)

    async def reissue(self, tg_id: int) -> str:
        await self.revoke(tg_id)
        return await self.issue(tg_id)

    async def set_enabled(self, tg_id: int, enable: bool):
        user = self.db.get_user(tg_id)
        by_key = {n.key: n for n in self.nodes}
        for c in self.db.clients(tg_id):
            node = by_key.get(c["node"])
            if node:
                await self.panel.update_client(
                    c["email"], self._client(tg_id, node, c["uuid"], c["email"], user["sub_token"], enable)
                )
        self.db.set_status(tg_id, "active" if enable else "blocked")

    async def sync(self):
        """Догоняет новые подключения: если добавили сервер/протокол, старым пользователям ключи появятся сами."""
        for user in self.db.list_users():
            have = {c["node"] for c in self.db.clients(user["tg_id"])}
            for node in self.nodes:
                if node.key not in have:
                    await self._add(user["tg_id"], node, user["sub_token"], enable=user["status"] == "active")

    async def traffic(self, tg_id: int) -> tuple[int, int]:
        up = down = 0
        for c in self.db.clients(tg_id):
            try:
                t = await self.panel.client_traffic(c["email"])
            except PanelError:
                continue
            up += int(t.get("up") or 0)
            down += int(t.get("down") or 0)
        return up, down

    # --- подписка ---
    def links(self, tg_id: int) -> list[tuple[str, str]]:
        """[(название, vless-ссылка)] в порядке подключений."""
        uuids = {c["node"]: c["uuid"] for c in self.db.clients(tg_id)}
        out = []
        if self.cfg.bridge_host:
            # Мост пересылает подключения на нидерландский сервер как есть, поэтому ключи те же.
            # Из России проходят только высокие порты через мост, поэтому в подписке только они.
            by_key = {n.key: n for n in self.nodes}
            for key, title, fp in BRIDGE_VARIANTS:
                node = by_key.get(key)
                if node and key in uuids:
                    label = f"{self.cfg.brand} · {title}"
                    out.append((title, vless_link(node, uuids[key], self.cfg.bridge_host, label, fp)))
            return out
        for node in self.nodes:
            if node.key in uuids:
                label = f"{self.cfg.server_name} · {node.title}"
                out.append((node.title, vless_link(node, uuids[node.key], self.cfg.server_host, label)))
        if self.cfg.server_host6:
            host6 = f"[{self.cfg.server_host6}]"
            for node in self.nodes:
                if node.key in uuids and node.key in IPV6_KEYS:
                    title = f"IPv6 · {node.title}"
                    label = f"{self.cfg.server_name} · {title}"
                    out.append((title, vless_link(node, uuids[node.key], host6, label)))
        return out
