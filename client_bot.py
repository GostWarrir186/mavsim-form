import asyncio
import datetime
import logging
import os
import urllib.parse
import uuid

from aiogram import types, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder

from config import client_bot as bot, client_dp as dp, sheet, clients_sheet, orders_info_sheet, get_or_create_feedback_topic

class Registration(StatesGroup):
    waiting_for_fio = State()

class Support(StatesGroup):
    waiting_for_message = State()
    chatting = State()

class Feedback(StatesGroup):
    waiting_for_message = State()

WEB_APP_URL = os.getenv("WEB_APP_URL", "https://gostwarrir186.github.io/mavsim-form/web/?v=19")
LINK_TO_OFFER = os.getenv("LINK_TO_OFFER", "")
SUPPORT_CHAT_ID = os.getenv("SUPPORT_CHAT_ID", "")

RECEIPTS = {
    "ru": (
        "📅 **Дата оформления:** {date}\n\n"
        "📦 **ИНФОРМАЦИЯ О ЗАКАЗЕ**\n"
        "🆔 **ID:** `{order_id}`\n"
        "───────────────\n"
        "👤 **ОТПРАВИТЕЛЬ**\n"
        "• **Имя:** {s_name}\n"
        "• **Телефон:** {s_phone}\n"
        "───────────────\n"
        "📍 **МАРШРУТ И ДОСТАВКА**\n"
        "• **Откуда:** {city_pickup}, {address_pickup}\n"
        "• **Куда:** {city_delivery}, {address_delivery}\n"
        "• **Тип:** {delivery_type}\n"
        "───────────────\n"
        "👤 **ПОЛУЧАТЕЛЬ**\n"
        "• **Имя:** {r_name}\n"
        "• **Телефон:** {r_phone}\n"
        "───────────────\n"
        "💰 **К ОПЛАТЕ:** {price} TJS"
    ),
    "tj": (
        "📅 **Санаи эҷод:** {date}\n\n"
        "📦 **МАЪЛУМОТИ ДАРХОСТ**\n"
        "🆔 **ID:** `{order_id}`\n"
        "───────────────\n"
        "👤 **ФИРИСТАНДА**\n"
        "• **Ном:** {s_name}\n"
        "• **Телефон:** {s_phone}\n"
        "───────────────\n"
        "📍 **МАРШРУТ ВА РАСОНИДАН**\n"
        "• **Аз куҷо:** {city_pickup}, {address_pickup}\n"
        "• **Ба куҷо:** {city_delivery}, {address_delivery}\n"
        "• **Намуд:** {delivery_type}\n"
        "───────────────\n"
        "👤 **ҚАБУЛКУНАНДА**\n"
        "• **Ном:** {r_name}\n"
        "• **Телефон:** {r_phone}\n"
        "───────────────\n"
        "💰 **БАРОИ ПАДОХТ:** {price} TJS"
    )
}

def generate_order_id() -> str:
    now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=5)
    date_part = now.strftime("%d%m")
    rand_part = uuid.uuid4().hex[:6].upper()
    return f"ORD-{date_part}-{rand_part}"

def sanitize_for_sheet(value) -> str:
    """Предотвращает formula injection: строки, начинающиеся с = + - @ \t \r, экранируются апострофом."""
    s = str(value) if value is not None else ""
    if s and s[0] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + s
    return s[:500]  # Ограничение длины

REQUIRED_ORDER_FIELDS = {
    's_name', 'r_name', 's_phone', 'r_phone',
    'city_pickup', 'city_delivery', 'address_pickup', 'address_delivery',
    'delivery_type', 'price', 'weight', 'sizes', 'driver_comment',
}
VALID_DELIVERY_TYPES = {'pvz', 'door'}
VALID_LANGS = set(RECEIPTS.keys())

def validate_order_data(data: dict) -> str | None:
    """Возвращает сообщение об ошибке или None если данные корректны."""
    for field in REQUIRED_ORDER_FIELDS:
        if field not in data:
            return f"отсутствует поле: {field}"
        if len(str(data[field])) > 500:
            return f"поле {field} слишком длинное"
    if str(data.get('delivery_type', '')).lower() not in VALID_DELIVERY_TYPES:
        return "недопустимый тип доставки"
    try:
        price = float(data['price'])
        if price <= 0 or price > 100_000:
            return "недопустимая цена"
    except (ValueError, TypeError):
        return "цена должна быть числом"
    return None

# --- Синхронные операции с Google Sheets ---

def _sync_check_user_by_phone(phone: str):
    """Ищет клиента по телефону в листе 'Клиенты' (столбец D = 4)."""
    if not clients_sheet:
        return None
    try:
        cell = clients_sheet.find(phone, in_column=4)
        return clients_sheet.row_values(cell.row) if cell else None
    except Exception as e:
        logging.error(f"Ошибка поиска клиента по телефону {phone}: {e}")
        return None

def _sync_check_user_by_chat_id(chat_id: str):
    """Ищет клиента по Chat ID в листе 'Клиенты' (столбец F = 6)."""
    if not clients_sheet:
        return None
    try:
        cell = clients_sheet.find(str(chat_id), in_column=6)
        return clients_sheet.row_values(cell.row) if cell else None
    except Exception as e:
        logging.error(f"Ошибка поиска клиента по chat_id {chat_id}: {e}")
        return None

def _sync_update_profile(chat_id: str, new_fio: str, new_address: str) -> bool:
    """Обновляет ФИО (C) и адрес забора (E) клиента по Chat ID (F = 6)."""
    if not clients_sheet:
        return False
    try:
        cell = clients_sheet.find(str(chat_id), in_column=6)
        if not cell:
            return False
        clients_sheet.batch_update([
            {'range': f'C{cell.row}', 'values': [[new_fio]]},
            {'range': f'E{cell.row}', 'values': [[new_address or '']]},
        ])
        return True
    except Exception as e:
        logging.error(f"Ошибка обновления профиля для chat_id={chat_id}: {e}")
        return False

def _sync_append_row(row_data: list):
    if sheet:
        sheet.append_row(row_data, table_range="A1")

def _sync_register_client(chat_id: str, fio: str, phone: str) -> bool:
    if not clients_sheet:
        return False
    try:
        now = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=5)).strftime("%d.%m.%Y %H:%M")
        clients_sheet.append_row(["ACTIVE", now, fio, phone, "", str(chat_id), ""], table_range="A1")
        return True
    except Exception as e:
        logging.error(f"Ошибка регистрации клиента chat_id={chat_id}: {e}")
        return False

def _sync_get_support_topic(chat_id: str) -> str | None:
    """Возвращает topic_id из колонки G(7) листа Клиенты."""
    if not clients_sheet:
        return None
    try:
        cell = clients_sheet.find(str(chat_id), in_column=6)
        if not cell:
            return None
        row = clients_sheet.row_values(cell.row)
        return row[6] if len(row) > 6 and row[6] else None
    except Exception as e:
        logging.error(f"Ошибка получения support_topic для chat_id={chat_id}: {e}")
        return None

def _sync_save_support_topic(chat_id: str, topic_id: int) -> None:
    """Сохраняет topic_id в колонку G(7) листа Клиенты."""
    if not clients_sheet:
        return
    try:
        cell = clients_sheet.find(str(chat_id), in_column=6)
        if cell:
            clients_sheet.update_cell(cell.row, 7, str(topic_id))
    except Exception as e:
        logging.error(f"Ошибка сохранения support_topic для chat_id={chat_id}: {e}")

def _sync_get_client_by_topic(topic_id: str) -> str | None:
    """Ищет chat_id клиента по topic_id из колонки G(7) листа Клиенты."""
    if not clients_sheet:
        return None
    try:
        cell = clients_sheet.find(str(topic_id), in_column=7)
        if not cell:
            return None
        row = clients_sheet.row_values(cell.row)
        return row[5] if len(row) > 5 else None
    except Exception as e:
        logging.error(f"Ошибка поиска клиента по topic_id={topic_id}: {e}")
        return None


# --- Хэндлеры ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    welcome_text = (
        "👋 **Ба Mavsimi Rason хуш омадед!**\n"
        "Барои ворид шудан тугмаи зерро пахш кунед:\n\n"
        "───────────────────────\n\n"
        "👋 **Добро пожаловать в Mavsimi Rason!**\n"
        "Нажмите кнопку ниже для авторизации в системе:"
    )
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="📱 Ворид шудан / Авторизация", request_contact=True))
    await message.answer(
        welcome_text,
        reply_markup=builder.as_markup(resize_keyboard=True),
        parse_mode="Markdown"
    )

@dp.message(F.text == "🔙 Главное меню")
async def go_main_menu(message: types.Message, state: FSMContext):
    await state.clear()
    user_data = await asyncio.to_thread(_sync_check_user_by_chat_id, str(message.chat.id))
    fio   = user_data[2] if user_data and len(user_data) > 2 else "Пользователь"
    phone = user_data[3] if user_data and len(user_data) > 3 else ""
    await message.answer("Меню асосӣ / Главное меню:", reply_markup=get_main_menu(fio, phone))
    await send_order_button(message, fio, phone)


@dp.message(F.contact)
async def process_contact(message: types.Message, state: FSMContext):
    phone = message.contact.phone_number
    if not phone.startswith("+"):
        phone = "+" + phone

    await state.update_data(phone=phone)
    user_data = await asyncio.to_thread(_sync_check_user_by_phone, phone)

    if user_data:
        fio = user_data[2] if len(user_data) > 2 else "Пользователь"
        menu_text = (
            f"👋 **Мо хурсандем, ки шуморо боз дидем, {fio}!**\n\n"
            f"───────────────────────\n\n"
            f"👋 **С возвращением, {fio}!**\n"
            f"Используйте кнопку ниже для перехода к заказам."
        )
        await message.answer(
            menu_text,
            reply_markup=get_main_menu(fio, phone),
            parse_mode="Markdown"
        )
        await send_order_button(message, fio, phone)
    else:
        not_found_text = (
            f"📋 **Диққат! Рақами шумо дар систем нест.**\n"
            f"Барои эҷоди дархостҳо шумо бояд бо [Офертаи оммавӣ]({LINK_TO_OFFER}) шинос шавед.\n\n"
            f"───────────────────────\n\n"
            f"📋 **Внимание! Вашего номера нет в системе.**\n"
            f"Для создания заказов вам необходимо ознакомиться с [Публичной офертой]({LINK_TO_OFFER})."
        )
        builder = ReplyKeyboardBuilder()
        builder.add(types.KeyboardButton(text="📝 Қабули оферта / Подписать оферту"))
        await message.answer(
            not_found_text,
            reply_markup=builder.as_markup(resize_keyboard=True),
            parse_mode="Markdown",
            disable_web_page_preview=True
        )

@dp.message(F.text == "📝 Қабули оферта / Подписать оферту")
async def start_fio_step(message: types.Message, state: FSMContext):
    ask_text = "**Ному Насаби** худро ворид кунед:\n\n───────────────────────\n\nВведите ваши **ФИО**:"
    await message.answer(ask_text, reply_markup=types.ReplyKeyboardRemove(), parse_mode="Markdown")
    await state.set_state(Registration.waiting_for_fio)

@dp.message(Registration.waiting_for_fio)
async def save_fio(message: types.Message, state: FSMContext):
    fio = message.text.strip()
    data = await state.get_data()
    phone = data.get('phone')
    await state.clear()
    if not phone:
        await message.answer("❌ Сессия хатм шуд. /start-ро пахш кунед.\n\n❌ Сессия истекла. Нажмите /start.")
        return

    await asyncio.to_thread(_sync_register_client, str(message.chat.id), fio, phone)

    success_text = f"🎉 **Бақайдгирӣ анҷом ёфт! / Регистрация завершена!**\n\nРады вас видеть, **{fio}**!"
    await message.answer(
        success_text,
        reply_markup=get_main_menu(fio, phone),
        parse_mode="Markdown"
    )
    await send_order_button(message, fio, phone)

def get_main_menu(fio: str = "", phone: str = ""):
    # fio/phone больше не нужны для клавиатуры (кнопка WebApp — inline, см. send_order_button),
    # сигнатура сохранена ради существующих вызовов.
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="📞 Поддержка / Дастгирӣ"))
    builder.add(types.KeyboardButton(text="💡 Обратная связь"))
    builder.adjust(1)
    return builder.as_markup(resize_keyboard=True)


async def send_order_button(message: types.Message, fio: str, phone: str):
    """
    Inline-кнопка (не reply!) — только через неё Telegram передаёт подписанный
    initData, которым потом проверяется подлинность запросов к web_api.py.
    """
    safe_fio = urllib.parse.quote(fio)
    safe_phone = urllib.parse.quote(str(phone))
    final_url = f"{WEB_APP_URL}&fio={safe_fio}&phone={safe_phone}"
    b = InlineKeyboardBuilder()
    b.button(text="📦 Оформить доставку / Ороиши дархост", web_app=types.WebAppInfo(url=final_url))
    await message.answer(
        "👇 Нажмите, чтобы оформить доставку / Барои ороиши дархост пахш кунед:",
        reply_markup=b.as_markup()
    )

# ─── Поддержка (Topics) ──────────────────────────────────────────────────────

@dp.message(F.text == "📞 Поддержка / Дастгирӣ")
async def support_start(message: types.Message, state: FSMContext):
    if not SUPPORT_CHAT_ID:
        await message.answer("⚙️ Поддержка временно недоступна.")
        return
    await message.answer(
        "📞 <b>Саволи худро нависед:</b>\n"
        "Мо ҳарчи зудтар ҷавоб хоҳем дод.\n\n"
        "📞 <b>Напишите ваш вопрос или проблему:</b>\n"
        "Мы ответим в ближайшее время.",
        reply_markup=types.ReplyKeyboardRemove(),
        parse_mode="HTML"
    )
    await state.set_state(Support.waiting_for_message)


@dp.message(Support.waiting_for_message)
async def support_send(message: types.Message, state: FSMContext):
    user_data = await asyncio.to_thread(_sync_check_user_by_chat_id, str(message.chat.id))
    fio   = user_data[2] if user_data and len(user_data) > 2 else "Неизвестно"
    phone = user_data[3] if user_data and len(user_data) > 3 else "Неизвестно"
    chat_id_str = str(message.chat.id)

    topic_id = None
    try:
        topic_id_str = await asyncio.to_thread(_sync_get_support_topic, chat_id_str)
        if topic_id_str:
            topic_id = int(topic_id_str)
        else:
            topic = await bot.create_forum_topic(
                chat_id=int(SUPPORT_CHAT_ID),
                name=f"{fio} | {phone}"
            )
            topic_id = topic.message_thread_id
            await asyncio.to_thread(_sync_save_support_topic, chat_id_str, topic_id)

        await bot.send_message(
            chat_id=int(SUPPORT_CHAT_ID),
            message_thread_id=topic_id,
            text=f"📨 <b>Клиент:</b> {message.text}",
            parse_mode="HTML"
        )
        back_kb = ReplyKeyboardBuilder()
        back_kb.button(text="🔙 Главное меню")
        await state.update_data(fio=fio, phone=phone, topic_id=topic_id)
        await state.set_state(Support.chatting)
        await message.answer(
            "✅ Фиристода шуд! Менеҷер ин ҷо ҷавоб хоҳад дод.\n\n"
            "✅ Отправлено! Менеджер ответит здесь.",
            reply_markup=back_kb.as_markup(resize_keyboard=True),
        )
    except Exception as e:
        logging.error(f"Ошибка поддержки (SUPPORT_CHAT_ID={SUPPORT_CHAT_ID}): {type(e).__name__}: {e}")
        if topic_id:
            # топик создан, но отправка не удалась — сохраняем state чтобы не создавать дубль
            await state.update_data(fio=fio, phone=phone, topic_id=topic_id)
            await state.set_state(Support.chatting)
        else:
            await state.clear()
        await message.answer("❌ Ошибка. Попробуйте позже.", reply_markup=get_main_menu(fio, phone))


@dp.message(Support.chatting)
async def support_continue(message: types.Message, state: FSMContext):
    data = await state.get_data()
    topic_id = data.get("topic_id")
    if not topic_id:
        await state.clear()
        await message.answer("❌ Сессия истекла. Нажмите кнопку поддержки снова.")
        return

    try:
        await bot.send_message(
            chat_id=int(SUPPORT_CHAT_ID),
            message_thread_id=topic_id,
            text=f"📨 <b>Клиент:</b> {message.text}",
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Ошибка отправки в топик: {type(e).__name__}: {e}")
        await message.answer("❌ Не удалось отправить. Попробуйте позже.")


@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def support_group_message(message: types.Message):
    """Пересылает ответы менеджеров из топиков поддержки клиентам."""
    if not SUPPORT_CHAT_ID or str(message.chat.id) != str(SUPPORT_CHAT_ID):
        return
    if not message.message_thread_id:
        return
    if not message.from_user or message.from_user.is_bot:
        return
    if not message.text:
        return

    client_chat_id = await asyncio.to_thread(
        _sync_get_client_by_topic, str(message.message_thread_id)
    )
    if not client_chat_id:
        return

    try:
        await bot.send_message(
            chat_id=int(client_chat_id),
            text=f"💬 <b>Ответ от поддержки:</b>\n\n{message.text}",
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Ошибка отправки ответа клиенту {client_chat_id}: {type(e).__name__}: {e}")


# ─── Обратная связь ───────────────────────────────────────────────────────────

@dp.message(F.text == "💡 Обратная связь")
async def feedback_start(message: types.Message, state: FSMContext):
    if not SUPPORT_CHAT_ID:
        await message.answer("⚙️ Обратная связь временно недоступна.")
        return
    await message.answer(
        "💡 <b>Бознигарӣ</b>\n\n"
        "Хато ё пешниҳоди худро нависед.\n\n"
        "💡 <b>Обратная связь</b>\n\n"
        "Опишите баг или предложение по улучшению бота.",
        reply_markup=types.ReplyKeyboardRemove(),
        parse_mode="HTML"
    )
    await state.set_state(Feedback.waiting_for_message)


@dp.message(Feedback.waiting_for_message)
async def feedback_send(message: types.Message, state: FSMContext):
    await state.clear()
    user_data = await asyncio.to_thread(_sync_check_user_by_chat_id, str(message.chat.id))
    fio   = user_data[2] if user_data and len(user_data) > 2 else "Неизвестно"
    phone = user_data[3] if user_data and len(user_data) > 3 else "Неизвестно"

    topic_id = await get_or_create_feedback_topic(bot)
    if not topic_id:
        await message.answer("❌ Не удалось отправить. Попробуйте позже.", reply_markup=get_main_menu(fio, phone))
        return

    text = (
        f"💡 <b>Обратная связь [Клиент]</b>\n"
        f"👤 {fio} | {phone}\n"
        f"🆔 <code>{message.chat.id}</code>\n"
        f"───────────────\n"
        f"{message.text}"
    )
    try:
        await bot.send_message(
            chat_id=int(SUPPORT_CHAT_ID),
            message_thread_id=topic_id,
            text=text,
            parse_mode="HTML"
        )
        await message.answer(
            "✅ Ташаккур! Бознигарии шумо қабул шуд.\n\n✅ Спасибо! Ваш отзыв получен.",
            reply_markup=get_main_menu(fio, phone),
        )
    except Exception as e:
        logging.error(f"Ошибка отправки обратной связи: {type(e).__name__}: {e}")
        await message.answer("❌ Не удалось отправить. Попробуйте позже.", reply_markup=get_main_menu(fio, phone))