import os
import asyncio
import re
from datetime import datetime

import aiosqlite
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder


# ---------------- CONFIG ----------------

ADMIN_IDS = {1844618007}
DB_PATH = "support.db"

dp = Dispatcher()

print("✅ BOT LOADED: FULL_SUPPORT_V1")


# ---------------- DB ----------------
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            server TEXT NOT NULL,
            category TEXT NOT NULL,
            text TEXT NOT NULL,
            status TEXT NOT NULL,
            claimed_by INTEGER,
            created_at TEXT NOT NULL
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER NOT NULL,
            media_type TEXT NOT NULL,
            file_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """)
        await db.commit()


async def create_ticket(user: types.User, server: str, category: str, text: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO tickets (user_id, username, server, category, text, status, claimed_by, created_at)
               VALUES (?, ?, ?, ?, ?, 'open', NULL, ?)""",
            (user.id, user.username or "", server, category, text, datetime.utcnow().isoformat())
        )
        await db.commit()
        return cur.lastrowid


async def get_ticket(ticket_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, user_id, server, category, text, status, claimed_by FROM tickets WHERE id=?",
            (ticket_id,)
        )
        return await cur.fetchone()


async def claim_ticket(ticket_id: int, admin_id: int) -> tuple[bool, str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT status, claimed_by FROM tickets WHERE id=?", (ticket_id,))
        row = await cur.fetchone()
        if not row:
            return False, "Тикет не найден."
        status, claimed_by = row
        if status != "open":
            return False, "Тикет закрыт."
        if claimed_by is not None and claimed_by != admin_id:
            return False, "Уже принят другим администратором."

        await db.execute("UPDATE tickets SET claimed_by=? WHERE id=?", (admin_id, ticket_id))
        await db.commit()
        return True, "Принято ✅"


async def close_ticket(ticket_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE tickets SET status='closed' WHERE id=?", (ticket_id,))
        await db.commit()


async def add_media(ticket_id: int, media_type: str, file_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO media (ticket_id, media_type, file_id, created_at) VALUES (?, ?, ?, ?)",
            (ticket_id, media_type, file_id, datetime.utcnow().isoformat())
        )
        await db.commit()


# ---------------- UI ----------------
def main_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="🛠 Открыть тикет", callback_data="support:new")
    kb.button(text="📌 FAQ", callback_data="faq:menu")
    kb.adjust(1)
    return kb.as_markup()


def faq_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="🔐 Аккаунт", callback_data="faq:account")
    kb.button(text="💳 Донат/Оплата", callback_data="faq:payment")
    kb.button(text="🎮 Лаг/Ping/FPS", callback_data="faq:lag")
    kb.button(text="🚫 Бан/Наказание", callback_data="faq:ban")
    kb.button(text="⬅️ Назад", callback_data="back:main")
    kb.adjust(1)
    return kb.as_markup()


FAQ_TEXT = {
    "faq:account": "🔐 Аккаунт: если забыли пароль — официальное восстановление. Если украли: ник/ID, последний вход, скрин/чек.",
    "faq:payment": "💳 Донат/оплата: чек/скрин + время + сумма + способ. Если донат не пришёл — подождите 5–15 минут, если не пришло — откройте тикет.",
    "faq:lag": "🎮 Лаг: закройте фоновые загрузки, держитесь ближе к Wi-Fi, понизьте графику. Если не помогло — откройте тикет и отправьте скрин.",
    "faq:ban": "🚫 Бан: для апелляции нужны ник, ID, сервер, время бана, причина (если есть), скрин.",
}


def server_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="🔴 Сервер 1", callback_data="srv:1")
    kb.button(text="⬅️ Главное меню", callback_data="back:main")
    kb.adjust(1)
    return kb.as_markup()


def category_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="🔐 Аккаунт", callback_data="cat:account")
    kb.button(text="💳 Донат/Оплата", callback_data="cat:payment")
    kb.button(text="🎮 Лаг/Тех", callback_data="cat:tech")
    kb.button(text="🚫 Бан/Наказание", callback_data="cat:ban")
    kb.button(text="❓ Другое", callback_data="cat:other")
    kb.button(text="⬅️ Главное меню", callback_data="back:main")
    kb.adjust(1)
    return kb.as_markup()


def admin_ticket_kb(ticket_id: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="🙋‍♂️ Claim (Принять)", callback_data=f"admin:claim:{ticket_id}")
    kb.button(text="✅ Close (Закрыть)", callback_data=f"admin:close:{ticket_id}")
    kb.adjust(1)
    return kb.as_markup()


# ---------------- FSM ----------------
class SupportFlow(StatesGroup):
    choosing_server = State()
    choosing_category = State()
    entering_text = State()
    adding_media = State()


# ---------------- Helpers ----------------
def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def quick_auto_reply(user_text: str) -> str | None:
    t = normalize(user_text)
    if any(k in t for k in ["пароль", "құпиясөз", "password", "login"]):
        return "🔐 По аккаунту: /start → FAQ → Аккаунт"
    if any(k in t for k in ["донат", "төлем", "kaspi", "qiwi", "payment", "оплата"]):
        return "💳 Донат/оплата: /start → FAQ → Донат/Оплата (нужны чек/время/сумма)"
    if any(k in t for k in ["лаг", "ping", "fps", "тормоз"]):
        return "🎮 Лаг: /start → FAQ → Лаг/Ping/FPS"
    if any(k in t for k in ["бан", "mute", "жаза", "наказание"]):
        return "🚫 Бан: /start → FAQ → Бан/Наказание (для апелляции нужен ник/ID/сервер)"
    return None


async def safe_send(admin_id: int, text: str, kb=None):
    try:
        await bot.send_message(admin_id, text, reply_markup=kb)
    except Exception as e:
        print("ADMIN SEND ERROR:", e)


# ---------------- Commands ----------------
@dp.message(CommandStart())
async def on_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 Привет! Это Tech Support бот для игры.\n"
        "Выбери действие:",
        reply_markup=main_menu_kb()
    )


@dp.message(Command("faq"))
async def cmd_faq(message: types.Message):
    await message.answer("📌 Выберите раздел FAQ:", reply_markup=faq_menu_kb())


# ---------------- Callbacks ----------------
@dp.callback_query(F.data == "back:main")
async def back_main(cb: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("Главное меню:", reply_markup=main_menu_kb())
    await cb.answer()


@dp.callback_query(F.data == "faq:menu")
async def faq_menu(cb: types.CallbackQuery):
    await cb.message.edit_text("📌 Выберите раздел FAQ:", reply_markup=faq_menu_kb())
    await cb.answer()


@dp.callback_query(F.data.startswith("faq:"))
async def faq_item(cb: types.CallbackQuery):
    text = FAQ_TEXT.get(cb.data, "Пусто.")
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Меню FAQ", callback_data="faq:menu")
    kb.button(text="🛠 Открыть тикет", callback_data="support:new")
    kb.button(text="⬅️ Главное меню", callback_data="back:main")
    kb.adjust(1)
    await cb.message.edit_text(text, reply_markup=kb.as_markup())
    await cb.answer()


@dp.callback_query(F.data == "support:new")
async def support_new(cb: types.CallbackQuery, state: FSMContext):
    await state.set_state(SupportFlow.choosing_server)
    await cb.message.edit_text("На каком сервере играешь?", reply_markup=server_kb())
    await cb.answer()


@dp.callback_query(SupportFlow.choosing_server, F.data.startswith("srv:"))
async def choose_server(cb: types.CallbackQuery, state: FSMContext):
    server = cb.data.split(":", 1)[1]
    await state.update_data(server=server)
    await state.set_state(SupportFlow.choosing_category)
    await cb.message.edit_text("Выберите категорию:", reply_markup=category_kb())
    await cb.answer()


@dp.callback_query(SupportFlow.choosing_category, F.data.startswith("cat:"))
async def choose_category(cb: types.CallbackQuery, state: FSMContext):
    category = cb.data.split(":", 1)[1]
    await state.update_data(category=category)
    await state.set_state(SupportFlow.entering_text)
    await cb.message.edit_text(
        "Опишите проблему подробно:\n"
        "• Ник/ID\n"
        "• Что произошло?\n"
        "• Во сколько?\n"
        "• Есть скрин/видео?\n\n"
        "Сначала отправьте текст."
    )
    await cb.answer()


@dp.message(SupportFlow.entering_text, F.text)
async def got_ticket_text(message: types.Message, state: FSMContext):
    data = await state.get_data()
    server = data.get("server", "—")
    category = data.get("category", "other")
    text = message.text.strip()

    ticket_id = await create_ticket(message.from_user, server, category, text)
    await state.update_data(ticket_id=ticket_id)
    await state.set_state(SupportFlow.adding_media)

    admin_text = (
        f"🆕 Ticket #{ticket_id}\n"
        f"User: {message.from_user.full_name} (@{message.from_user.username or '—'})\n"
        f"User ID: {message.from_user.id}\n"
        f"Server: {server}\n"
        f"Category: {category}\n\n"
        f"{text}\n\n"
        f"➡️ Чтобы ответить — сделайте Reply на это сообщение."
    )

    for admin_id in ADMIN_IDS:
        await safe_send(admin_id, admin_text, kb=admin_ticket_kb(ticket_id))

    await message.answer(
        f"✅ Тикет создан: #{ticket_id}\n"
        "Теперь можете отправить скрин/фото/видео.\n"
        "Если больше ничего нет — напишите /done"
    )


@dp.message(SupportFlow.adding_media, Command("done"))
async def done_media(message: types.Message, state: FSMContext):
    await message.answer("Хорошо ✅ Когда оператор ответит — сообщение придёт сюда.")
    await state.clear()


@dp.message(SupportFlow.adding_media, F.photo)
async def add_photo(message: types.Message, state: FSMContext):
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    if not ticket_id:
        return

    file_id = message.photo[-1].file_id
    await add_media(ticket_id, "photo", file_id)

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_photo(admin_id, file_id, caption=f"📎 Ticket #{ticket_id}: PHOTO (user {message.from_user.id})")
        except Exception as e:
            print("ADMIN PHOTO ERROR:", e)

    await message.answer("📎 Фото добавлено. Есть ещё? Если нет — /done.")


@dp.message(SupportFlow.adding_media, F.video)
async def add_video(message: types.Message, state: FSMContext):
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    if not ticket_id:
        return

    file_id = message.video.file_id
    await add_media(ticket_id, "video", file_id)

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_video(admin_id, file_id, caption=f"📎 Ticket #{ticket_id}: VIDEO (user {message.from_user.id})")
        except Exception as e:
            print("ADMIN VIDEO ERROR:", e)

    await message.answer("📎 Видео добавлено. Есть ещё? Если нет — /done.")


# Fallback: only non-command text
@dp.message(F.text & ~F.text.startswith("/") & ~F.reply_to_message)
async def fallback(message: types.Message):
    auto = quick_auto_reply(message.text)
    if auto:
        await message.answer(auto, reply_markup=main_menu_kb())
        return
    await message.answer("Чтобы открыть тикет: /start → 🛠 Открыть тикет.", reply_markup=main_menu_kb())


# ---------------- Admin actions ----------------
@dp.callback_query(F.data.startswith("admin:claim:"), F.from_user.func(lambda u: u and u.id in ADMIN_IDS))
async def admin_claim(cb: types.CallbackQuery):
    ticket_id = int(cb.data.split(":")[-1])
    ok, msg = await claim_ticket(ticket_id, cb.from_user.id)
    await cb.answer(msg, show_alert=not ok)
    if ok:
        await cb.message.answer(f"🙋‍♂️ Ticket #{ticket_id} принят: {cb.from_user.full_name}")


@dp.callback_query(F.data.startswith("admin:close:"), F.from_user.func(lambda u: u and u.id in ADMIN_IDS))
async def admin_close(cb: types.CallbackQuery):
    ticket_id = int(cb.data.split(":")[-1])
    await close_ticket(ticket_id)
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await cb.message.answer(f"✅ Ticket #{ticket_id} закрыт.")
    await cb.answer("Closed")


# Admin reply -> user
@dp.message(F.reply_to_message, F.from_user.func(lambda u: u and u.id in ADMIN_IDS))
async def admin_reply(message: types.Message):
    replied = message.reply_to_message.text or ""
    m_ticket = re.search(r"Ticket #(\d+)", replied)
    m_user = re.search(r"User ID:\s*(\d+)", replied)

    if not m_ticket or not m_user:
        await message.answer("Сообщение, на которое вы ответили, не похоже на формат тикета. Ответьте Reply на сообщение тикета.")
        return

    ticket_id = int(m_ticket.group(1))
    user_id = int(m_user.group(1))

    row = await get_ticket(ticket_id)
    if not row:
        await message.answer("Тикет не найден.")
        return

    _, _, server, category, _, status, claimed_by = row
    if status != "open":
        await message.answer("Этот тикет закрыт.")
        return

    # Если тикет уже Claimed — отвечает только тот админ
    if claimed_by is not None and claimed_by != message.from_user.id:
        await message.answer("Этот тикет уже принят другим администратором (Claim).")
        return

    try:
        await bot.send_message(
            user_id,
            f"🛠 Ответ поддержки (Ticket #{ticket_id})\nServer: {server} | Category: {category}\n\n{message.text}"
        )
        await message.answer("✅ Отправлено игроку.")
    except Exception as e:
        await message.answer(f"❌ Не отправлено: {e}")
        print("USER SEND ERROR:", e)


async def main():
    await init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":

    asyncio.run(main())



