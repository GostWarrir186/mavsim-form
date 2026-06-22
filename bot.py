import asyncio
import datetime
import hmac
import hashlib
import json
import logging
import os
import urllib.parse
import uuid

from aiogram import types, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import ReplyKeyboardBuilder

from config import client_bot as bot, client_dp as dp, sheet, clients_sheet, CLIENT_TOKEN, get_or_create_feedback_topic

class Registration(StatesGroup):
    waiting_for_fio = State()

class Support(StatesGroup):
    waiting_for_message = State()
    chatting = State()

class Feedback(StatesGroup):
    waiting_for_message = State()

WEB_APP_URL = os.getenv("WEB_APP_URL", "https://gostwarrir186.github.io/mavsim-form/web/?v=19")
LINK_TO_OFFER = "https://www.google.com"
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
        "💰 **К ОПЛАТЕ:** {price} TJS\n\n"
        "ℹ️ *Статус: [NEW]. После проверки менеджером заказ улетит курьерам.*"
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
        "💰 **БАРОИ ПАДОХТ:** {price} TJS\n\n"
        "ℹ️ *Статус: [NEW]. Пас аз санҷиши менеҷер дархост ба курьерҳо меравад.*"
    )
}

# --- БЕЗОПАСНОСТЬ: верификация Telegram WebApp initData ---
def verify_telegram_init_data(init_data: str) -> bool:
    """
    Проверяет подлинность данных от Telegram WebApp по HMAC-SHA256.
    Документация: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    if not init_data:
        return False
    try:
        parsed = dict(
            x.split('=', 1) for x in urllib.parse.unquote(init_data).split('&')
            if '=' in x
        )
        received_hash = parsed.pop('hash', '')
        if not received_hash:
            return False
        check_string = '\n'.join(f'{k}={v}' for k, v in sorted(parsed.items()))
        secret_key = hmac.new(b'WebAppData', CLIENT_TOKEN.encode(), hashlib.sha256).digest()
        computed_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(computed_hash, received_hash)
    except Exception as e:
        logging.warning(f"Ошибка верификации initData: {e}")
        return False

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
        sheet.append_row(row_data)

def _sync_register_client(chat_id: str, fio: str, phone: str) -> bool:
    if not clients_sheet:
        return False
    try:
        now = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=5)).strftime("%d.%m.%Y %H:%M")
        clients_sheet.append_row(["ACTIVE", now, fio, phone, "", str(chat_id), ""])
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
    await message.answer("👋 Главное меню:", reply_markup=get_main_menu(fio, phone))


@dp.message(F.contact)
async def process_contact(message: types.Message, state: FSMContext):
    phone = message.contact.phone_number
    if not phone.startswith("+"):
        phone = "+" + phone

    await state.update_data(phone=phone)
    user_data = await asyncio.to_thread(_sync_check_user_by_phone, phone)

    if user_data:
        # ФИО из столбца M (индекс 12)
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
        await message.answer("❌ Сессия истекла. Нажмите /start и авторизуйтесь снова.")
        return

    await asyncio.to_thread(_sync_register_client, str(message.chat.id), fio, phone)

    success_text = f"🎉 **Бақайдгирӣ анҷом ёфт! / Регистрация завершена!**\n\nРады вас видеть, **{fio}**!"
    await message.answer(
        success_text,
        reply_markup=get_main_menu(fio, phone),
        parse_mode="Markdown"
    )

def get_main_menu(fio: str, phone: str):
    safe_fio = urllib.parse.quote(fio)
    safe_phone = urllib.parse.quote(str(phone))
    final_url = f"{WEB_APP_URL}&fio={safe_fio}&phone={safe_phone}"

    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(
        text="📦 Оформить доставку / Ороиши дархост",
        web_app=types.WebAppInfo(url=final_url)
    ))
    builder.add(types.KeyboardButton(text="📞 Поддержка / Дастгирӣ"))
    builder.add(types.KeyboardButton(text="💡 Обратная связь"))
    builder.adjust(1)
    return builder.as_markup(resize_keyboard=True)

@dp.message(F.web_app_data)
async def handle_webapp_data(message: types.Message):
    try:
        data = json.loads(message.web_app_data.data)

        # --- Обновление профиля ---
        if data.get("action") == "update_profile":
            updated_fio = data.get("fio", "").strip()
            updated_addr = data.get("address", "").strip()

            if not updated_fio:
                await message.answer("❌ ФИО не может быть пустым.")
                return

            # Ищем пользователя по chat_id (не по phone из запроса — безопасно)
            success = await asyncio.to_thread(
                _sync_update_profile,
                str(message.chat.id),
                updated_fio,
                updated_addr
            )
            if success:
                # Получаем актуальный телефон из базы для меню
                user_data = await asyncio.to_thread(_sync_check_user_by_chat_id, str(message.chat.id))
                phone_from_db = user_data[3] if user_data and len(user_data) > 3 else ""
                await message.answer(
                    f"✅ **Данные профиля успешно обновлены!**\n\n"
                    f"• **Новое ФИО:** {updated_fio}\n"
                    f"• **Адрес забора:** {updated_addr if updated_addr else 'Не указан'}",
                    reply_markup=get_main_menu(updated_fio, phone_from_db),
                    parse_mode="Markdown"
                )
            else:
                await message.answer("❌ Ошибка при обновлении профиля. Пользователь не найден в базе данных.")
            return

        # --- Новый заказ ---
        error_msg = validate_order_data(data)
        if error_msg:
            logging.warning(f"Невалидные данные формы от chat_id={message.chat.id}: {error_msg}")
            await message.answer(f"❌ Ошибка в данных формы: {error_msg}. Попробуйте снова.")
            return

        lang = data.get('lang', 'ru') if data.get('lang') in VALID_LANGS else 'ru'

        dtype_readable = "Ба ПВЗ 🏢" if data['delivery_type'] == "pvz" else "То дар 🚪"
        if lang == "ru":
            dtype_readable = "До ПВЗ 🏢" if data['delivery_type'] == "pvz" else "До двери 🚪"

        utc_now = datetime.datetime.now(datetime.timezone.utc)
        dushanbe_time = (utc_now + datetime.timedelta(hours=5)).strftime("%d.%m.%Y %H:%M")

        # ID генерируется на СЕРВЕРЕ, не доверяем клиентскому
        order_id = generate_order_id()

        s = sanitize_for_sheet
        row = [
            "NEW",                               # A (1)  - Статус заявки
            order_id,                            # B (2)  - ID заказа
            dushanbe_time,                       # C (3)  - Дата и время оформления
            s(data['price']),                    # D (4)  - Итоговая стоимость
            s(data['city_pickup']),              # E (5)  - Город забора
            s(data['address_pickup']),           # F (6)  - Точный адрес забора
            s(data['city_delivery']),            # G (7)  - Город доставки
            s(data['address_delivery']),         # H (8)  - Точный адрес доставки
            s(data['driver_comment']),           # I (9)  - Ориентир для курьера
            data['delivery_type'].upper(),       # J (10) - Тип доставки (PVZ / DOOR)
            s(data['weight']),                   # K (11) - Вес посылки
            s(data['sizes']),                    # L (12) - Габариты
            s(data['s_name']),                   # M (13) - ФИО отправителя
            s(data['s_phone']),                  # N (14) - Телефон отправителя
            s(data['r_name']),                   # O (15) - ФИО получателя
            s(data['r_phone']),                  # P (16) - Телефон получателя
            "bot_webapp",                        # Q (17) - Источник создания
            "",                                  # R (18) - Имя курьера
            str(message.chat.id),                # S (19) - Telegram Chat ID клиента
            ""                                   # T (20) - Telegram Chat ID курьера
        ]

        await asyncio.to_thread(_sync_append_row, row)

        msg = RECEIPTS[lang].format(
            date=dushanbe_time, order_id=order_id,
            s_name=data['s_name'], s_phone=data['s_phone'],
            city_pickup=data['city_pickup'], address_pickup=data['address_pickup'],
            city_delivery=data['city_delivery'], address_delivery=data['address_delivery'],
            delivery_type=dtype_readable,
            r_name=data['r_name'], r_phone=data['r_phone'],
            price=data['price']
        )
        await message.answer(msg, parse_mode="Markdown")

    except KeyError as e:
        logging.error(f"Отсутствует обязательное поле в данных формы: {e}")
        await message.answer("❌ Ошибка в данных формы. Попробуйте снова.")
    except Exception as e:
        logging.error(f"Критическая ошибка обработки webapp_data: {e}", exc_info=True)
        await message.answer("❌ Системная ошибка обработки формы. Попробуйте позже.")


# ─── Поддержка (Topics) ──────────────────────────────────────────────────────

@dp.message(F.text == "📞 Поддержка / Дастгирӣ")
async def support_start(message: types.Message, state: FSMContext):
    if not SUPPORT_CHAT_ID:
        await message.answer("⚙️ Поддержка временно недоступна.")
        return
    await message.answer(
        "📞 <b>Напишите ваш вопрос или проблему:</b>\n\nМы ответим в ближайшее время.",
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
            "✅ Отправлено! Менеджер ответит здесь.\n\nМожете написать ещё или вернуться в меню.",
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
        "💡 <b>Обратная связь</b>\n\n"
        "Опишите баг, косяк или предложение по улучшению бота.\n"
        "Постарайтесь написать подробно — это поможет нам стать лучше 🙏",
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
            "✅ Спасибо! Ваш отзыв получен — мы обязательно его рассмотрим.",
            reply_markup=get_main_menu(fio, phone),
        )
    except Exception as e:
        logging.error(f"Ошибка отправки обратной связи: {type(e).__name__}: {e}")
        await message.answer("❌ Не удалось отправить. Попробуйте позже.", reply_markup=get_main_menu(fio, phone))