"""Мини-сервер подписок: V2Box раз в 12 часов забирает по ссылке актуальный список ключей."""
import base64
import json
import logging
import os
import re

from aiohttp import web

from .vpn import VPN

log = logging.getLogger(__name__)


def make_app(vpn: VPN) -> web.Application:
    cfg, db = vpn.cfg, vpn.db

    async def sub(request: web.Request) -> web.Response:
        user = db.get_by_token(request.match_info["token"])
        if not user:
            raise web.HTTPNotFound()
        if user["status"] != "active":
            raise web.HTTPForbidden()
        body = base64.b64encode("\n".join(link for _, link in vpn.links(user["tg_id"])).encode()).decode()
        up, down = await vpn.traffic(user["tg_id"])
        headers = {
            "profile-title": "base64:" + base64.b64encode(cfg.brand.encode()).decode(),
            "profile-update-interval": "12",
            "subscription-userinfo": f"upload={up}; download={down}; total=0; expire=0",
            "content-disposition": "inline",
            "cache-control": "no-store",
        }
        return web.Response(text=body, headers=headers, content_type="text/plain")

    app = web.Application()
    app.router.add_get(f"/{cfg.sub_path}/{{token}}", sub)
    return app


AWG_EXCHANGE_PORT = 8899
_PUBKEY = re.compile(r"^[A-Za-z0-9+/]{43}=$")


def make_awg_app(vpn: VPN) -> web.Application:
    """Обмен ключами туннеля AmneziaWG с мостом. Отвечает только IP моста (BRIDGE_HOST);
    файрвол (awg-server.sh) тоже пускает на этот порт только его."""
    cfg = vpn.cfg

    async def exchange(request: web.Request) -> web.Response:
        if not cfg.bridge_host or request.remote != cfg.bridge_host:
            raise web.HTTPForbidden()
        pub = (await request.text()).strip()
        if not _PUBKEY.match(pub):
            raise web.HTTPBadRequest(text="bad key")
        server = os.path.join(cfg.data_dir, "awg-server.json")
        if not os.path.exists(server):
            raise web.HTTPServiceUnavailable(text="tunnel not ready")
        with open(os.path.join(cfg.data_dir, "awg-peer.pub"), "w") as f:
            f.write(pub + "\n")
        with open(server) as f:
            return web.json_response(json.load(f))

    app = web.Application()
    app.router.add_post("/awg-exchange", exchange)
    return app


async def start(vpn: VPN) -> web.AppRunner:
    runner = web.AppRunner(make_app(vpn), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", vpn.cfg.sub_port).start()
    log.info("Подписки раздаются на порту %s", vpn.cfg.sub_port)
    if vpn.cfg.bridge_host:
        awg = web.AppRunner(make_awg_app(vpn), access_log=None)
        await awg.setup()
        await web.TCPSite(awg, "0.0.0.0", AWG_EXCHANGE_PORT).start()
    return runner
