"""Команды и кнопки бота: пользователи и админка."""
import html
import io
import logging

import segno
from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from . import texts as T
from .panel import PanelError
from .vpn import VPN

log = logging.getLogger(__name__)
router = Router()
PAGE = 10


class Broadcast(StatesGroup):
    text = State()


# ---------- помощники ----------

def is_admin(vpn: VPN, user_id: int) -> bool:
    return user_id in vpn.cfg.admin_ids


def fmt_bytes(n: int) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "Б" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.2f} ТБ"


def who(user) -> str:
    name = html.escape(user["full_name"] or str(user["tg_id"]))
    tail = f" (@{html.escape(user['username'])})" if user["username"] else ""
    return f'<a href="tg://user?id={user["tg_id"]}">{name}</a>{tail}'


def ikb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows]
    )


def main_menu(admin: bool) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=T.BTN_KEY), KeyboardButton(text=T.BTN_HOWTO)],
        [KeyboardButton(text=T.BTN_TRAFFIC), KeyboardButton(text=T.BTN_HELP)],
    ]
    if admin:
        rows.append([KeyboardButton(text=T.BTN_ADMIN)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


async def send_key(bot: Bot, vpn: VPN, tg_id: int, intro: str | None = None):
    user = vpn.db.get_user(tg_id)
    url = vpn.cfg.sub_url(user["sub_token"])
    if intro:
        await bot.send_message(tg_id, intro, reply_markup=main_menu(is_admin(vpn, tg_id)))
    qr = segno.make(url, error="m")
    buf = io.BytesIO()
    qr.save(buf, kind="png", scale=8, border=2)
    await bot.send_photo(
        tg_id,
        BufferedInputFile(buf.getvalue(), "key.png"),
        caption=T.KEY.format(server=html.escape(vpn.cfg.server_name), url=url),
        reply_markup=ikb([("📋 Ключи по отдельности", "raw"), ("📖 Как подключить", "howto")]),
    )


async def notify_admins(bot: Bot, vpn: VPN, text: str, markup=None):
    for admin_id in vpn.cfg.admin_ids:
        try:
            await bot.send_message(admin_id, text, reply_markup=markup)
        except Exception as e:  # админ мог не запускать бота
            log.warning("Не отправил админу %s: %s", admin_id, e)


async def activate(bot: Bot, vpn: VPN, tg_id: int) -> str | None:
    """Выдаёт ключ. Возвращает текст ошибки или None."""
    if vpn.db.seats_taken() >= vpn.cfg.max_users:
        return f"Лимит {vpn.cfg.max_users} пользователей исчерпан."
    try:
        await vpn.issue(tg_id)
    except PanelError as e:
        log.exception("Не удалось выдать ключ")
        return f"Панель ответила ошибкой: {e}"
    await send_key(bot, vpn, tg_id, T.APPROVED)
    return None


def active_only(vpn: VPN, tg_id: int):
    user = vpn.db.get_user(tg_id)
    return user if user and user["status"] == "active" else None


# ---------- пользователи ----------

@router.message(CommandStart())
async def start(message: Message, command: CommandObject, bot: Bot, vpn: VPN):
    u = message.from_user
    db = vpn.db
    user = db.get_user(u.id)
    admin = is_admin(vpn, u.id)
    if user:
        db.touch_user(u.id, u.username, u.full_name)

    if user and user["status"] == "active":
        await message.answer(T.HELLO_ACTIVE.format(brand=html.escape(vpn.cfg.brand)), reply_markup=main_menu(admin))
        return
    if user and user["status"] == "blocked":
        await message.answer(T.HELLO_BLOCKED)
        return

    # Админ получает ключ сразу.
    if admin:
        db.upsert_user(u.id, u.username, u.full_name, "pending")
        err = await activate(bot, vpn, u.id)
        if err:
            await message.answer("⚠️ " + err, reply_markup=main_menu(True))
        return

    # Пригласительная ссылка: t.me/<бот>?start=inv...
    code = (command.args or "").strip()
    if code.startswith("inv"):
        if vpn.db.seats_taken() >= vpn.cfg.max_users:
            await message.answer(T.NO_SEATS)
            return
        if db.take_invite(code, u.id):
            db.upsert_user(u.id, u.username, u.full_name, "pending")
            err = await activate(bot, vpn, u.id)
            if err:
                await message.answer("⚠️ Не получилось выдать ключ, администратор уже в курсе.")
                await notify_admins(bot, vpn, f"⚠️ Ошибка выдачи по приглашению для {who(db.get_user(u.id))}: {err}")
            else:
                await notify_admins(bot, vpn, f"🎟 По приглашению подключился {who(db.get_user(u.id))}")
            return
        await message.answer(T.INVITE_BAD)

    if user and user["status"] == "pending":
        await message.answer(T.HELLO_PENDING)
        return
    if user and user["status"] == "rejected":
        await message.answer(T.HELLO_REJECTED)
        return
    await message.answer(
        T.HELLO_NEW.format(brand=html.escape(vpn.cfg.brand)),
        reply_markup=ikb([("🔑 Запросить доступ", "req")]),
    )


@router.callback_query(F.data == "req")
async def request_access(cb: CallbackQuery, bot: Bot, vpn: VPN):
    u = cb.from_user
    user = vpn.db.get_user(u.id)
    if user and user["status"] in ("active", "blocked", "rejected", "pending"):
        await cb.answer("Заявка уже есть", show_alert=False)
        return
    if vpn.db.seats_taken() >= vpn.cfg.max_users:
        await cb.message.edit_text(T.NO_SEATS)
        await cb.answer()
        return
    vpn.db.upsert_user(u.id, u.username, u.full_name, "pending")
    await cb.message.edit_text(T.REQUEST_SENT)
    taken = vpn.db.seats_taken()
    await notify_admins(
        bot,
        vpn,
        f"📨 Заявка на доступ: {who(vpn.db.get_user(u.id))}\nID: <code>{u.id}</code>\n"
        f"Занято мест: {taken}/{vpn.cfg.max_users}",
        ikb([("✅ Одобрить", f"ok:{u.id}"), ("❌ Отклонить", f"no:{u.id}")]),
    )
    await cb.answer()


@router.message(F.text == T.BTN_KEY)
async def my_key(message: Message, bot: Bot, vpn: VPN):
    if not active_only(vpn, message.from_user.id):
        user = vpn.db.get_user(message.from_user.id)
        blocked = user and user["status"] == "blocked"
        await message.answer(T.HELLO_BLOCKED if blocked else "Ключа пока нет. Нажми /start, чтобы запросить доступ.")
        return
    await send_key(bot, vpn, message.from_user.id)


@router.callback_query(F.data == "raw")
async def raw_keys(cb: CallbackQuery, vpn: VPN):
    if not active_only(vpn, cb.from_user.id):
        await cb.answer("Нет доступа", show_alert=True)
        return
    await cb.answer()
    await cb.message.answer(
        "📋 Ключи по отдельности. Коснись ключа, чтобы скопировать, затем в приложении: "
        "V2Box — «+» → Import v2ray uri from clipboard, Happ — «Из буфера»."
    )
    # По одному ключу в сообщении: все вместе не влезают в лимит Telegram (4096 символов).
    for title, link in vpn.links(cb.from_user.id):
        await cb.message.answer(f"<b>{html.escape(title)}</b>\n<code>{html.escape(link)}</code>")


@router.message(F.text == T.BTN_HOWTO)
@router.callback_query(F.data == "howto")
async def howto(event, vpn: VPN):
    msg = event.message if isinstance(event, CallbackQuery) else event
    await msg.answer(T.HOWTO.format(ios=T.V2BOX_IOS, android=T.V2BOX_ANDROID), disable_web_page_preview=True)
    if isinstance(event, CallbackQuery):
        await event.answer()


@router.message(F.text == T.BTN_TRAFFIC)
async def my_traffic(message: Message, vpn: VPN):
    if not active_only(vpn, message.from_user.id):
        await message.answer("Ключа пока нет.")
        return
    up, down = await vpn.traffic(message.from_user.id)
    await message.answer(T.TRAFFIC.format(up=fmt_bytes(up), down=fmt_bytes(down)))


@router.message(F.text == T.BTN_HELP)
async def help_(message: Message):
    await message.answer(T.HELP, reply_markup=ikb([("✉️ Сообщить администратору", "sos")]))


@router.callback_query(F.data == "sos")
async def sos(cb: CallbackQuery, bot: Bot, vpn: VPN):
    user = vpn.db.get_user(cb.from_user.id)
    if user:
        await notify_admins(bot, vpn, f"🆘 Не работает VPN у {who(user)}")
    await cb.message.edit_text(T.HELP_SENT)
    await cb.answer()


# ---------- админка ----------

def admin_menu(vpn: VPN) -> tuple[str, InlineKeyboardMarkup]:
    db = vpn.db
    text = (
        f"⚙️ <b>Админка</b>\n\n"
        f"Активных: <b>{db.count('active')}</b> · на паузе: <b>{db.count('blocked')}</b> · "
        f"заявок: <b>{db.count('pending')}</b>\nМест занято: {db.seats_taken()}/{vpn.cfg.max_users}"
    )
    kb = ikb(
        [("👥 Пользователи", "ul:0"), (f"📨 Заявки ({db.count('pending')})", "pend")],
        [("🎟 Приглашение", "inv"), ("📣 Рассылка", "bc")],
        [("🖥 Сервер", "srv")],
    )
    return text, kb


@router.message(F.text == T.BTN_ADMIN)
@router.message(Command("admin"))
async def admin(message: Message, vpn: VPN):
    if not is_admin(vpn, message.from_user.id):
        return
    text, kb = admin_menu(vpn)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "adm")
async def admin_back(cb: CallbackQuery, vpn: VPN):
    if not is_admin(vpn, cb.from_user.id):
        return
    text, kb = admin_menu(vpn)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.regexp(r"^(ok|no):\d+$"))
async def decide(cb: CallbackQuery, bot: Bot, vpn: VPN):
    if not is_admin(vpn, cb.from_user.id):
        return
    action, raw_id = cb.data.split(":")
    tg_id = int(raw_id)
    user = vpn.db.get_user(tg_id)
    if not user or user["status"] != "pending":
        await cb.answer("Заявка уже обработана", show_alert=True)
        await cb.message.edit_reply_markup(reply_markup=None)
        return
    if action == "no":
        vpn.db.set_status(tg_id, "rejected")
        await cb.message.edit_text(cb.message.html_text + "\n\n❌ Отклонено")
        try:
            await bot.send_message(tg_id, T.HELLO_REJECTED)
        except Exception:
            pass
        await cb.answer()
        return
    await cb.answer("Выдаю ключ…")
    err = await activate(bot, vpn, tg_id)
    if err:
        await cb.message.answer("⚠️ " + err)
        return
    await cb.message.edit_text(cb.message.html_text + "\n\n✅ Одобрено, ключ отправлен")


@router.callback_query(F.data == "pend")
async def pending_list(cb: CallbackQuery, vpn: VPN):
    if not is_admin(vpn, cb.from_user.id):
        return
    users = vpn.db.list_users(("pending",))
    if not users:
        await cb.answer("Заявок нет", show_alert=True)
        return
    for user in users:
        await cb.message.answer(
            f"📨 Заявка: {who(user)}\nID: <code>{user['tg_id']}</code>",
            reply_markup=ikb([("✅ Одобрить", f"ok:{user['tg_id']}"), ("❌ Отклонить", f"no:{user['tg_id']}")]),
        )
    await cb.answer()


@router.callback_query(F.data.startswith("ul:"))
async def user_list(cb: CallbackQuery, vpn: VPN):
    if not is_admin(vpn, cb.from_user.id):
        return
    page = int(cb.data.split(":")[1])
    users = vpn.db.list_users()
    chunk = users[page * PAGE:(page + 1) * PAGE]
    rows = []
    for user in chunk:
        mark = "🟢" if user["status"] == "active" else "⏸"
        label = user["full_name"] or user["username"] or str(user["tg_id"])
        rows.append([(f"{mark} {label}"[:40], f"u:{user['tg_id']}")])
    nav = []
    if page > 0:
        nav.append(("◀️", f"ul:{page - 1}"))
    if (page + 1) * PAGE < len(users):
        nav.append(("▶️", f"ul:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([("⬅️ Назад", "adm")])
    text = f"👥 Пользователи: {len(users)} (стр. {page + 1})" if users else "Пользователей пока нет."
    await cb.message.edit_text(text, reply_markup=ikb(*rows))
    await cb.answer()


async def user_card(vpn: VPN, tg_id: int) -> tuple[str, InlineKeyboardMarkup]:
    user = vpn.db.get_user(tg_id)
    up, down = await vpn.traffic(tg_id)
    status = {"active": "🟢 активен", "blocked": "⏸ на паузе"}.get(user["status"], user["status"])
    text = (
        f"👤 {who(user)}\nID: <code>{tg_id}</code>\nСтатус: {status}\n"
        f"Трафик: ⬆️ {fmt_bytes(up)} · ⬇️ {fmt_bytes(down)}"
    )
    toggle = ("⏸ Пауза", f"bl:{tg_id}") if user["status"] == "active" else ("▶️ Включить", f"ub:{tg_id}")
    kb = ikb([toggle, ("🔄 Новый ключ", f"re:{tg_id}")], [("🗑 Удалить", f"rm:{tg_id}")], [("⬅️ К списку", "ul:0")])
    return text, kb


@router.callback_query(F.data.regexp(r"^u:\d+$"))
async def show_user(cb: CallbackQuery, vpn: VPN):
    if not is_admin(vpn, cb.from_user.id):
        return
    tg_id = int(cb.data.split(":")[1])
    if not vpn.db.get_user(tg_id):
        await cb.answer("Пользователь не найден", show_alert=True)
        return
    text, kb = await user_card(vpn, tg_id)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.regexp(r"^(bl|ub|re|rm|rmy):\d+$"))
async def user_action(cb: CallbackQuery, bot: Bot, vpn: VPN):
    if not is_admin(vpn, cb.from_user.id):
        return
    action, raw_id = cb.data.split(":")
    tg_id = int(raw_id)
    user = vpn.db.get_user(tg_id)
    if not user:
        await cb.answer("Пользователь не найден", show_alert=True)
        return

    async def tell(text: str):
        try:
            await bot.send_message(tg_id, text)
        except Exception:
            pass

    try:
        if action == "rm":
            await cb.message.edit_text(
                f"Удалить {who(user)}? Ключ перестанет работать, место освободится.",
                reply_markup=ikb([("🗑 Да, удалить", f"rmy:{tg_id}"), ("Отмена", f"u:{tg_id}")]),
            )
            await cb.answer()
            return
        if action == "rmy":
            await vpn.revoke(tg_id)
            vpn.db.delete_user(tg_id)
            await tell(T.REMOVED_NOTICE)
            await cb.message.edit_text("🗑 Пользователь удалён.", reply_markup=ikb([("⬅️ К списку", "ul:0")]))
            await cb.answer()
            return
        if action == "bl":
            await vpn.set_enabled(tg_id, False)
            await tell(T.BLOCKED_NOTICE)
        elif action == "ub":
            await vpn.set_enabled(tg_id, True)
            await tell(T.UNBLOCKED_NOTICE)
        elif action == "re":
            await vpn.reissue(tg_id)
            if tg_id != cb.from_user.id:
                await tell(T.REISSUED_NOTICE)
            await send_key(bot, vpn, tg_id)
    except PanelError as e:
        await cb.answer(f"Ошибка панели: {e}"[:190], show_alert=True)
        return
    text, kb = await user_card(vpn, tg_id)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer("Готово")


@router.callback_query(F.data == "inv")
async def invite(cb: CallbackQuery, bot: Bot, vpn: VPN):
    if not is_admin(vpn, cb.from_user.id):
        return
    me = await bot.get_me()
    code = vpn.db.new_invite()
    await cb.message.answer(
        "🎟 Одноразовая пригласительная ссылка. Перешли её человеку: по ней ключ выдаётся сразу, без заявки.\n\n"
        f"https://t.me/{me.username}?start={code}"
    )
    await cb.answer()


@router.callback_query(F.data == "srv")
async def server(cb: CallbackQuery, vpn: VPN):
    if not is_admin(vpn, cb.from_user.id):
        return
    try:
        st = await vpn.panel.status()
        online = await vpn.panel.onlines()
    except PanelError as e:
        await cb.answer(f"Панель недоступна: {e}"[:190], show_alert=True)
        return
    mem = st.get("mem") or {}
    xray = st.get("xray") or {}
    mem_txt = (
        f"{fmt_bytes(mem.get('current', 0))} из {fmt_bytes(mem.get('total', 0))}" if mem.get("total") else "—"
    )
    online_users = {e.rsplit("-", 1)[0] for e in online}
    text = (
        f"🖥 <b>Сервер</b> {html.escape(vpn.cfg.server_name)} ({vpn.cfg.server_host})\n\n"
        f"Xray: <b>{xray.get('state', '?')}</b> {xray.get('version', '')}\n"
        f"Процессор: {float(st.get('cpu') or 0):.0f}%\nПамять: {mem_txt}\n"
        f"Сейчас онлайн: <b>{len(online_users)}</b>"
    )
    await cb.message.edit_text(text, reply_markup=ikb([("🔄 Обновить", "srv"), ("⬅️ Назад", "adm")]))
    await cb.answer()


@router.callback_query(F.data == "bc")
async def broadcast_start(cb: CallbackQuery, state: FSMContext, vpn: VPN):
    if not is_admin(vpn, cb.from_user.id):
        return
    await state.set_state(Broadcast.text)
    await cb.message.answer("📣 Пришли текст рассылки для всех активных пользователей. /cancel — отмена.")
    await cb.answer()


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено.")


@router.message(Broadcast.text)
async def broadcast_send(message: Message, state: FSMContext, bot: Bot, vpn: VPN):
    await state.clear()
    if not is_admin(vpn, message.from_user.id):
        return
    sent = failed = 0
    for user in vpn.db.list_users(("active",)):
        try:
            await bot.copy_message(user["tg_id"], message.chat.id, message.message_id)
            sent += 1
        except Exception:
            failed += 1
    await message.answer(f"Рассылка готова: доставлено {sent}, не доставлено {failed}.")
