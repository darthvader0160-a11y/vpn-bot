"""Настройки бота. Всё берётся из переменных окружения (файл .env на сервере)."""
import os
from dataclasses import dataclass


def _int_list(raw: str) -> list[int]:
    return [int(x) for x in raw.replace(" ", "").split(",") if x]


@dataclass
class Config:
    bot_token: str
    admin_ids: list[int]
    panel_url: str            # http://127.0.0.1:<порт>/<секретный путь>
    panel_token: str          # API-токен панели 3x-ui
    server_host: str          # IP или домен сервера, который пойдёт в ключи
    server_name: str = "🇳🇱 Нидерланды"
    brand: str = "Наш VPN"
    max_users: int = 50
    sub_port: int = 2096
    sub_path: str = "sub"
    reality_sni: str = "www.samsung.com"
    xhttp_port: int = 443
    tcp_port: int = 8443
    data_dir: str = "/data"
    admin_contact: str = ""

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, "bot.db")

    @property
    def nodes_path(self) -> str:
        return os.path.join(self.data_dir, "nodes.json")

    def sub_url(self, token: str) -> str:
        return f"http://{self.server_host}:{self.sub_port}/{self.sub_path}/{token}"


def load() -> Config:
    env = os.environ
    missing = [k for k in ("BOT_TOKEN", "ADMIN_IDS", "PANEL_URL", "PANEL_TOKEN", "SERVER_HOST") if not env.get(k)]
    if missing:
        raise SystemExit("Не заданы переменные: " + ", ".join(missing))
    return Config(
        bot_token=env["BOT_TOKEN"],
        admin_ids=_int_list(env["ADMIN_IDS"]),
        panel_url=env["PANEL_URL"].rstrip("/"),
        panel_token=env["PANEL_TOKEN"],
        server_host=env["SERVER_HOST"],
        server_name=env.get("SERVER_NAME", "🇳🇱 Нидерланды"),
        brand=env.get("BRAND", "Наш VPN"),
        max_users=int(env.get("MAX_USERS", "50")),
        sub_port=int(env.get("SUB_PORT", "2096")),
        sub_path=env.get("SUB_PATH", "sub").strip("/"),
        reality_sni=env.get("REALITY_SNI", "www.samsung.com"),
        xhttp_port=int(env.get("XHTTP_PORT", "443")),
        tcp_port=int(env.get("TCP_PORT", "8443")),
        data_dir=env.get("DATA_DIR", "/data"),
        admin_contact=env.get("ADMIN_CONTACT", ""),
    )
