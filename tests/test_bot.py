"""Проверка без настоящего сервера: поднимаем имитацию API панели 3x-ui и гоняем бота против неё.

Запуск: python -m unittest discover -s tests
"""
import base64
import json
import os
import sys
import tempfile
import unittest
from urllib.parse import parse_qs, unquote, urlparse

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bot.config import Config  # noqa: E402
from bot.db import DB  # noqa: E402
from bot.panel import Panel, PanelError  # noqa: E402
from bot.subserver import make_app  # noqa: E402
from bot.vpn import VPN, x25519_pair  # noqa: E402

TOKEN = "test-token"


class FakePanel:
    """Минимальная копия API 3x-ui v3: /panel/api/inbounds/*, /clients/*, /server/status."""

    def __init__(self):
        self.inbounds: list[dict] = []
        self.clients: dict[str, dict] = {}  # email -> {client, inboundId}
        self.calls: list[str] = []

    def app(self) -> web.Application:
        @web.middleware
        async def auth(request, handler):
            self.calls.append(f"{request.method} {request.path}")
            if request.headers.get("Authorization") != f"Bearer {TOKEN}":
                return web.Response(status=404)
            return await handler(request)

        def ok(obj=None):
            return web.json_response({"success": True, "msg": "", "obj": obj})

        def fail(msg):
            return web.json_response({"success": False, "msg": msg, "obj": None})

        async def list_inbounds(_):
            return ok(self.inbounds)

        async def add_inbound(request):
            data = await request.json()
            assert isinstance(data["settings"], str) and isinstance(data["streamSettings"], str)
            if any(i["port"] == data["port"] for i in self.inbounds):
                return fail("port already exists")
            data["id"] = len(self.inbounds) + 1
            self.inbounds.append(data)
            return ok(data)

        async def add_client(request):
            data = await request.json()
            c = data["client"]
            if c["email"] in self.clients:
                return fail("Duplicate email: " + c["email"])
            if c.get("subId") and any(x["client"].get("subId") == c["subId"] for x in self.clients.values()):
                return fail("subId already in use: " + c["subId"])
            assert data["inboundIds"] and all(any(i["id"] == x for i in self.inbounds) for x in data["inboundIds"])
            self.clients[c["email"]] = {"client": c, "inboundId": data["inboundIds"][0], "up": 1000, "down": 5000}
            return ok()

        async def update_client(request):
            email = request.match_info["email"]
            if email not in self.clients:
                return fail("not found")
            self.clients[email]["client"] = await request.json()
            return ok()

        async def del_client(request):
            email = request.match_info["email"]
            if email not in self.clients:
                return fail("not found")
            del self.clients[email]
            return ok()

        async def traffic(request):
            c = self.clients.get(request.match_info["email"])
            return ok({"up": c["up"], "down": c["down"]} if c else None) if c else fail("not found")

        async def onlines(_):
            return ok(list(self.clients)[:1])

        async def status(_):
            return ok({"cpu": 3.2, "mem": {"current": 300 << 20, "total": 2 << 30}, "xray": {"state": "running"}})

        app = web.Application(middlewares=[auth])
        base = "/secret/panel/api"
        app.router.add_get(base + "/inbounds/list", list_inbounds)
        app.router.add_post(base + "/inbounds/add", add_inbound)
        app.router.add_post(base + "/clients/add", add_client)
        app.router.add_post(base + "/clients/update/{email}", update_client)
        app.router.add_post(base + "/clients/del/{email}", del_client)
        app.router.add_get(base + "/clients/traffic/{email}", traffic)
        app.router.add_post(base + "/clients/onlines", onlines)
        app.router.add_get(base + "/server/status", status)
        return app


class BotTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fake = FakePanel()
        self.server = TestServer(self.fake.app())
        await self.server.start_server()
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(
            bot_token="x",
            admin_ids=[1],
            panel_url=str(self.server.make_url("/secret")),
            panel_token=TOKEN,
            server_host="203.0.113.7",
            data_dir=self.tmp.name,
            max_users=2,
        )
        self.db = DB(self.cfg.db_path)
        self.panel = Panel(self.cfg.panel_url, TOKEN)
        self.vpn = VPN(self.cfg, self.db, self.panel)

    async def asyncTearDown(self):
        self.db.conn.close()
        await self.panel.close()
        await self.server.close()
        self.tmp.cleanup()

    async def test_nodes_created_once(self):
        await self.vpn.ensure_nodes()
        self.assertEqual([n.key for n in self.vpn.nodes][:2], ["xhttp", "tcp"])
        self.assertEqual([i["port"] for i in self.fake.inbounds], [443, 8443, 2053, 2083, 39443, 47443])
        self.assertEqual(self.vpn.nodes[2].sni, "vk.com")
        stream = json.loads(self.fake.inbounds[0]["streamSettings"])
        self.assertEqual(stream["network"], "xhttp")
        self.assertEqual(stream["realitySettings"]["target"], "www.samsung.com:443")
        # Повторный запуск не создаёт дублей и читает те же ключи.
        pbk = self.vpn.nodes[0].pbk
        await self.vpn.ensure_nodes()
        self.assertEqual(len(self.fake.inbounds), 6)
        self.assertEqual(self.vpn.nodes[0].pbk, pbk)

    async def test_issue_links_and_subscription(self):
        await self.vpn.ensure_nodes()
        self.db.upsert_user(42, "ivan", "Иван", "pending")
        token = await self.vpn.issue(42)
        user = self.db.get_user(42)
        self.assertEqual(user["status"], "active")
        self.assertEqual(len(self.fake.clients), 6)
        self.assertIn("u42-t-high", self.fake.clients)
        self.assertEqual(self.fake.clients["u42-tcp"]["client"]["flow"], "xtls-rprx-vision")
        self.assertEqual(self.fake.clients["u42-xhttp"]["client"]["flow"], "")

        links = self.vpn.links(42)
        self.assertEqual([t for t, _ in links][:2], ["Основной", "Резервный"])
        self.assertEqual(len(links), 6)
        x = urlparse(links[0][1])
        q = {k: v[0] for k, v in parse_qs(x.query).items()}
        self.assertEqual(x.scheme, "vless")
        self.assertEqual(x.hostname, "203.0.113.7")
        self.assertEqual(x.port, 443)
        self.assertEqual(x.username, self.fake.clients["u42-xhttp"]["client"]["id"])
        self.assertEqual(q["type"], "xhttp")
        self.assertEqual(q["security"], "reality")
        self.assertEqual(q["pbk"], self.vpn.nodes[0].pbk)
        self.assertTrue(q["path"].startswith("/"))
        self.assertNotIn("flow", q)
        self.assertEqual(unquote(x.fragment), "🇳🇱 Нидерланды · Основной")
        t = {k: v[0] for k, v in parse_qs(urlparse(links[1][1]).query).items()}
        self.assertEqual((t["type"], t["flow"]), ("tcp", "xtls-rprx-vision"))

        async with TestClient(TestServer(make_app(self.vpn))) as client:
            resp = await client.get(f"/sub/{token}")
            self.assertEqual(resp.status, 200)
            body = base64.b64decode(await resp.text()).decode().splitlines()
            self.assertEqual(body, [l for _, l in links])
            self.assertIn("download=30000", resp.headers["subscription-userinfo"])
            self.assertEqual((await client.get("/sub/wrong")).status, 404)

            await self.vpn.set_enabled(42, False)
            self.assertFalse(self.fake.clients["u42-tcp"]["client"]["enable"])
            self.assertEqual((await client.get(f"/sub/{token}")).status, 403)
            await self.vpn.set_enabled(42, True)
            self.assertTrue(self.fake.clients["u42-tcp"]["client"]["enable"])

            new_token = await self.vpn.reissue(42)
            self.assertNotEqual(new_token, token)
            self.assertEqual((await client.get(f"/sub/{token}")).status, 404)
            self.assertEqual((await client.get(f"/sub/{new_token}")).status, 200)

        await self.vpn.revoke(42)
        self.assertEqual(self.fake.clients, {})

    async def test_leftover_client_is_replaced(self):
        await self.vpn.ensure_nodes()
        self.fake.clients["u5-xhttp"] = {"client": {"id": "old"}, "inboundId": 1, "up": 0, "down": 0}
        self.db.upsert_user(5, None, "Пётр", "pending")
        await self.vpn.issue(5)
        self.assertNotEqual(self.fake.clients["u5-xhttp"]["client"]["id"], "old")

    async def test_sync_adds_missing_node(self):
        await self.vpn.ensure_nodes()
        self.db.upsert_user(7, None, "Оля", "pending")
        await self.vpn.issue(7)
        del self.fake.clients["u7-tcp"]
        self.db.conn.execute("DELETE FROM clients WHERE tg_id=7 AND node='tcp'")
        await self.vpn.sync()
        self.assertIn("u7-tcp", self.fake.clients)

    async def test_wrong_token(self):
        bad = Panel(self.cfg.panel_url, "nope")
        with self.assertRaises(PanelError):
            await bad.list_inbounds()
        await bad.close()

    async def test_traffic_and_status(self):
        await self.vpn.ensure_nodes()
        self.db.upsert_user(9, None, "Аня", "pending")
        await self.vpn.issue(9)
        self.assertEqual(await self.vpn.traffic(9), (6000, 30000))
        st = await self.panel.status()
        self.assertEqual(st["xray"]["state"], "running")

    def test_invites_and_seats(self):
        code = self.db.new_invite()
        self.assertTrue(self.db.take_invite(code, 1))
        self.assertFalse(self.db.take_invite(code, 2))
        self.db.upsert_user(1, None, "A", "pending")
        self.db.activate(1, "t1")
        self.db.upsert_user(2, None, "B", "pending")
        self.assertEqual(self.db.seats_taken(), 1)

    def test_x25519_format(self):
        priv, pub = x25519_pair()
        self.assertEqual(len(base64.urlsafe_b64decode(priv + "=")), 32)
        self.assertEqual(len(base64.urlsafe_b64decode(pub + "=")), 32)
        self.assertNotIn("=", priv + pub)


class HandlersImportTest(unittest.TestCase):
    def test_router_builds(self):
        from bot.handlers import fmt_bytes, router

        self.assertTrue(router.message.handlers)
        self.assertEqual(fmt_bytes(1536), "1.5 КБ")


if __name__ == "__main__":
    unittest.main()
