"""Запуск: python -m bot"""
import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from . import config, subserver
from .db import DB
from .handlers import router
from .panel import Panel, PanelError
from .vpn import VPN

log = logging.getLogger("vpn-bot")


async def wait_panel(vpn: VPN):
    """Панель стартует дольше бота — ждём её до 3 минут."""
    for attempt in range(36):
        try:
            await vpn.ensure_nodes()
            return
        except (PanelError, OSError) as e:
            log.info("Панель пока недоступна (%s), жду…", e)
            await asyncio.sleep(5)
    raise SystemExit("Панель 3x-ui не отвечает. Проверь: docker compose logs 3xui")


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = config.load()
    os.makedirs(cfg.data_dir, exist_ok=True)
    db = DB(cfg.db_path)
    panel = Panel(cfg.panel_url, cfg.panel_token)
    vpn = VPN(cfg, db, panel)

    await wait_panel(vpn)
    await vpn.sync()
    log.info("Подключения: %s", ", ".join(f"{n.key}:{n.port}" for n in vpn.nodes))
    runner = await subserver.start(vpn)

    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(vpn=vpn)
    dp.include_router(router)
    try:
        await bot.set_my_commands([BotCommand(command="start", description="Главное меню")])
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()
        await panel.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
