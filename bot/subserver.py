"""Мини-сервер подписок: V2Box раз в 12 часов забирает по ссылке актуальный список ключей."""
import base64
import logging

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


async def start(vpn: VPN) -> web.AppRunner:
    runner = web.AppRunner(make_app(vpn), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", vpn.cfg.sub_port).start()
    log.info("Подписки раздаются на порту %s", vpn.cfg.sub_port)
    return runner
