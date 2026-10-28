"""Сценарий целиком через обработчики Telegram: заявка → одобрение → ключ, приглашение, пауза, лимит мест.

Telegram подменён: запросы бота складываются в список, ответы собираются на лету.
"""
import datetime
import os
import sys
import tempfile
import unittest

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, GetMe, SendMessage, SendPhoto
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from aiohttp.test_utils import TestServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bot.config import Config  # noqa: E402
from bot.db import DB  # noqa: E402
from bot.handlers import router  # noqa: E402
from bot.panel import Panel  # noqa: E402
from bot.vpn import VPN  # noqa: E402
from test_bot import TOKEN, FakePanel  # noqa: E402

ADMIN, ALICE, BOB, CAROL = 1, 100, 200, 300
NOW = datetime.datetime(2026, 10, 28)


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.sent = []

    async def make_request(self, bot, method, timeout=None):
        self.sent.append(method)
        if isinstance(method, GetMe):
            return User(id=999, is_bot=True, first_name="VPN", username="our_vpn_bot")
        if isinstance(method, (SendMessage, SendPhoto)):
            return Message(
                message_id=len(self.sent),
                date=NOW,
                chat=Chat(id=method.chat_id, type="private"),
                text=getattr(method, "text", None) or getattr(method, "caption", None),
            )
        return True

    async def close(self):
        pass

    async def stream_content(self, *a, **kw):  # не используется
        yield b""


def msg(uid: int, text: str, name="User") -> Update:
    user = User(id=uid, is_bot=False, first_name=name)
    return Update(
        update_id=1,
        message=Message(message_id=1, date=NOW, chat=Chat(id=uid, type="private"), from_user=user, text=text),
    )


def cb(uid: int, data: str, text="…") -> Update:
    user = User(id=uid, is_bot=False, first_name="X")
    m = Message(message_id=5, date=NOW, chat=Chat(id=uid, type="private"), text=text)
    return Update(update_id=2, callback_query=CallbackQuery(id="c", from_user=user, chat_instance="i", data=data, message=m))


class FlowTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fake = FakePanel()
        self.server = TestServer(self.fake.app())
        await self.server.start_server()
        self.tmp = tempfile.TemporaryDirectory()
        cfg = Config(
            bot_token="1:x", admin_ids=[ADMIN], panel_url=str(self.server.make_url("/secret")),
            panel_token=TOKEN, server_host="203.0.113.7", data_dir=self.tmp.name, max_users=3,
        )
        self.db = DB(cfg.db_path)
        self.panel = Panel(cfg.panel_url, TOKEN)
        self.vpn = VPN(cfg, self.db, self.panel)
        await self.vpn.ensure_nodes()
        self.session = FakeSession()
        self.bot = Bot("1:x", session=self.session, default=DefaultBotProperties(parse_mode="HTML"))
        self.dp = Dispatcher(vpn=self.vpn)
        self.dp.include_router(router)

    async def asyncTearDown(self):
        self.dp.sub_routers.remove(router)
        router._parent_router = None
        self.db.conn.close()
        await self.panel.close()
        await self.server.close()
        self.tmp.cleanup()

    async def feed(self, update):
        self.session.sent.clear()
        await self.dp.feed_update(self.bot, update)
        return self.session.sent

    def texts(self, sent, chat_id=None):
        return [
            (getattr(m, "text", None) or getattr(m, "caption", None) or "")
            for m in sent
            if isinstance(m, (SendMessage, SendPhoto)) and (chat_id is None or m.chat_id == chat_id)
        ]

    async def test_full_flow(self):
        # Админ получает ключ сразу.
        sent = await self.feed(msg(ADMIN, "/start", "Админ"))
        self.assertTrue(any("ключ-подписка" in t for t in self.texts(sent, ADMIN)))
        self.assertEqual(self.db.get_user(ADMIN)["status"], "active")

        # Алиса просит доступ, админу приходит заявка.
        sent = await self.feed(msg(ALICE, "/start", "Алиса"))
        self.assertTrue(any("Запросить доступ" in str(m.reply_markup) for m in sent if isinstance(m, SendMessage)))
        sent = await self.feed(cb(ALICE, "req"))
        self.assertEqual(self.db.get_user(ALICE)["status"], "pending")
        self.assertTrue(any("Заявка на доступ" in t for t in self.texts(sent, ADMIN)))

        # Чужой не может одобрить.
        await self.feed(cb(BOB, f"ok:{ALICE}"))
        self.assertEqual(self.db.get_user(ALICE)["status"], "pending")

        # Админ одобряет — Алиса получает QR и ссылку.
        sent = await self.feed(cb(ADMIN, f"ok:{ALICE}", "📨 Заявка"))
        photos = [m for m in sent if isinstance(m, SendPhoto) and m.chat_id == ALICE]
        self.assertEqual(len(photos), 1)
        self.assertIn("http://203.0.113.7:2096/sub/", photos[0].caption)
        self.assertIn("u100-xhttp", self.fake.clients)

        # Кнопки пользователя.
        sent = await self.feed(msg(ALICE, "🔑 Мой ключ"))
        self.assertTrue(any(isinstance(m, SendPhoto) for m in sent))
        sent = await self.feed(cb(ALICE, "raw"))
        self.assertTrue(any("vless://" in t for t in self.texts(sent)))
        sent = await self.feed(msg(ALICE, "📊 Мой трафик"))
        self.assertTrue(any("получено" in t for t in self.texts(sent)))
        sent = await self.feed(msg(ALICE, "🆘 Не работает"))
        sent = await self.feed(cb(ALICE, "sos"))
        self.assertTrue(any("Не работает VPN" in t for t in self.texts(sent, ADMIN)))

        # Пауза и снятие с паузы.
        await self.feed(cb(ADMIN, f"bl:{ALICE}"))
        self.assertEqual(self.db.get_user(ALICE)["status"], "blocked")
        self.assertFalse(self.fake.clients["u100-tcp"]["client"]["enable"])
        sent = await self.feed(msg(ALICE, "🔑 Мой ключ"))
        self.assertFalse(any(isinstance(m, SendPhoto) for m in sent))
        await self.feed(cb(ADMIN, f"ub:{ALICE}"))
        self.assertEqual(self.db.get_user(ALICE)["status"], "active")

        # Приглашение: Боб получает ключ без заявки.
        sent = await self.feed(cb(ADMIN, "inv"))
        link = next(t for t in self.texts(sent) if "start=inv" in t).split("start=")[1].strip()
        sent = await self.feed(msg(BOB, f"/start {link}", "Боб"))
        self.assertEqual(self.db.get_user(BOB)["status"], "active")
        # Лимит 3 места занят (админ, Алиса, Боб) — новых заявок нет.
        sent = await self.feed(msg(CAROL, f"/start {link}", "Кэрол"))
        self.assertTrue(any("свободных мест нет" in t for t in self.texts(sent, CAROL)))
        sent = await self.feed(cb(CAROL, "req"))
        self.assertIsNone(self.db.get_user(CAROL))

        # Удаление освобождает место.
        await self.feed(cb(ADMIN, f"rm:{BOB}"))
        await self.feed(cb(ADMIN, f"rmy:{BOB}"))
        self.assertIsNone(self.db.get_user(BOB))
        self.assertNotIn("u200-xhttp", self.fake.clients)

        # Повторно приглашение не работает, но обычная заявка доступна.
        sent = await self.feed(msg(CAROL, f"/start {link}", "Кэрол"))
        self.assertTrue(any("уже использована" in t for t in self.texts(sent, CAROL)))
        self.assertTrue(any("Запросить доступ" in str(m.reply_markup) for m in sent if isinstance(m, SendMessage)))

        # Админка и сервер отвечают.
        sent = await self.feed(msg(ADMIN, "⚙️ Админка"))
        self.assertTrue(any("Активных" in t for t in self.texts(sent)))
        sent = await self.feed(msg(ALICE, "⚙️ Админка"))
        self.assertEqual(self.texts(sent), [])
        await self.feed(cb(ADMIN, "srv"))
        await self.feed(cb(ADMIN, "ul:0"))
        await self.feed(cb(ADMIN, f"u:{ALICE}"))

    async def test_reject_and_broadcast(self):
        await self.feed(msg(ALICE, "/start", "Алиса"))
        await self.feed(cb(ALICE, "req"))
        sent = await self.feed(cb(ADMIN, f"no:{ALICE}", "📨 Заявка"))
        self.assertEqual(self.db.get_user(ALICE)["status"], "rejected")
        sent = await self.feed(msg(ALICE, "/start"))
        self.assertTrue(any("отказано" in t for t in self.texts(sent, ALICE)))

        await self.feed(msg(ADMIN, "/start"))
        await self.feed(cb(ADMIN, "bc"))
        sent = await self.feed(msg(ADMIN, "Плановые работы в 23:00"))
        self.assertTrue(any("доставлено 1" in t for t in self.texts(sent, ADMIN)))
        self.assertFalse(any(isinstance(m, AnswerCallbackQuery) and m.show_alert for m in sent))


if __name__ == "__main__":
    unittest.main()
