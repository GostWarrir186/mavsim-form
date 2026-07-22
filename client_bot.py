import asyncio
import base64
import datetime
import html
import json
import logging
import os
import re
import secrets
import string
import urllib.parse

from aiogram import types, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder

from config import client_bot as bot, client_dp as dp, manager_bot as mgr_bot, sheet, clients_sheet, orders_info_sheet, get_manager_chat_ids

class Registration(StatesGroup):
    waiting_for_lang = State()
    waiting_for_fio  = State()

class Support(StatesGroup):
    waiting_for_message = State()
    chatting = State()

WEB_APP_URL = os.getenv("WEB_APP_URL", "https://gostwarrir186.github.io/mavsim-form/web/?v=19")
LINK_TO_OFFER = os.getenv("LINK_TO_OFFER", "")
SUPPORT_CHAT_ID = os.getenv("SUPPORT_CHAT_ID", "")

# ─── Локализация (клиентский бот — только один язык за раз) ─────────────────
AUTH_BTN     = {"ru": "📱 Авторизация",                       "tj": "📱 Ворид шудан"}
BACK_BTN     = {"ru": "🔙 Главное меню",                      "tj": "🔙 Менюи асосӣ"}
ACCEPT_OFFER_BTN = {"ru": "📝 Подписать оферту",               "tj": "📝 Қабули оферта"}
ORDER_BTN    = {"ru": "📦 Оформить доставку",                  "tj": "📦 Ороиши дархост"}
SUPPORT_BTN  = {"ru": "📞 Поддержка",                          "tj": "📞 Дастгирӣ"}

CL = {
    "ru": {
        "welcome": "👋 **Добро пожаловать в Mavsimi Rason!**\nНажмите кнопку ниже для авторизации в системе:",
        "menu_prompt": "Главное меню:",
        "welcome_back": "👋 **С возвращением, {fio}!**\nИспользуйте кнопку ниже для перехода к заказам.",
        "not_found": (
            "📋 **Внимание! Вашего номера нет в системе.**\n"
            "Для создания заказов вам необходимо ознакомиться с [Публичной офертой]({url})."
        ),
        "ask_fio": "Введите ваши **ФИО**:",
        "session_expired": "❌ Сессия истекла. Нажмите /start.",
        "registered": "🎉 **Регистрация завершена!**\n\nРады вас видеть, **{fio}**!",
        "fio_empty": "❌ ФИО не может быть пустым.",
        "profile_updated": "✅ **Данные обновлены!**\n\n• **ФИО:** {fio}\n• **Адрес:** {addr}",
        "addr_missing": "Не указан",
        "profile_user_not_found": "❌ Ошибка обновления. Пользователь не найден.",
        "support_unavailable": "⚙️ Поддержка временно недоступна.",
        "support_prompt": "📞 <b>Напишите ваш вопрос или проблему:</b>\nМы ответим в ближайшее время.",
        "support_sent": "✅ Отправлено! Менеджер ответит здесь.",
        "support_error": "❌ Ошибка. Попробуйте позже.",
        "support_session_expired": "❌ Сессия истекла. Нажмите кнопку поддержки снова.",
        "support_send_failed": "❌ Не удалось отправить. Попробуйте позже.",
        "support_reply_header": "💬 <b>Ответ от поддержки:</b>\n\n{text}",
    },
    "tj": {
        "welcome": "👋 **Ба Mavsimi Rason хуш омадед!**\nБарои ворид шудан тугмаи зерро пахш кунед:",
        "menu_prompt": "Менюи асосӣ:",
        "welcome_back": "👋 **Мо хурсандем, ки шуморо боз дидем, {fio}!**\nБарои гузариш ба дархостҳо тугмаи зеринро истифода баред.",
        "not_found": (
            "📋 **Диққат! Рақами шумо дар систем нест.**\n"
            "Барои эҷоди дархостҳо шумо бояд бо [Офертаи оммавӣ]({url}) шинос шавед."
        ),
        "ask_fio": "**Ному Насаби** худро ворид кунед:",
        "session_expired": "❌ Сессия хатм шуд. /start-ро пахш кунед.",
        "registered": "🎉 **Бақайдгирӣ анҷом ёфт!**\n\nШодем, ки шуморо мебинем, **{fio}**!",
        "fio_empty": "❌ Ном холӣ буда наметавонад.",
        "profile_updated": "✅ **Маълумот навшуд!**\n\n• **Ном:** {fio}\n• **Суроға:** {addr}",
        "addr_missing": "Нишон дода нашуд",
        "profile_user_not_found": "❌ Хатогӣ. Корбар дар база нест.",
        "support_unavailable": "⚙️ Дастгирӣ муваққатан дастнорас аст.",
        "support_prompt": "📞 <b>Саволи худро нависед:</b>\nМо ҳарчи зудтар ҷавоб хоҳем дод.",
        "support_sent": "✅ Фиристода шуд! Менеҷер ин ҷо ҷавоб хоҳад дод.",
        "support_error": "❌ Хатогӣ. Баъдтар кӯшиш кунед.",
        "support_session_expired": "❌ Мӯҳлати сессия гузашт. Тугмаи дастгириро аз нав пахш кунед.",
        "support_send_failed": "❌ Фиристода нашуд. Баъдтар кӯшиш кунед.",
        "support_reply_header": "💬 <b>Ҷавоб аз дастгирӣ:</b>\n\n{text}",
    },
}

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
        "• **Телефон:** {r_phone}"
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
        "• **Телефон:** {r_phone}"
    )
}

ORDER_ID_ALPHABET = string.ascii_uppercase + string.digits
ORDER_ID_RE = re.compile(r"^Z-\d{4}-[A-Z0-9]{4}$")

def generate_order_id() -> str:
    now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=5)
    date_part = now.strftime("%d%m")
    rand_part = "".join(secrets.choice(ORDER_ID_ALPHABET) for _ in range(4))
    return f"Z-{date_part}-{rand_part}"

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

def _sync_get_client_order_statuses(chat_id: str) -> list[dict]:
    """Актуальные статусы заказов клиента (Лист1, Chat ID клиента = столбец S = 19), для живого обновления «Истории» в WebApp."""
    if not sheet:
        return []
    try:
        result = []
        for idx, row in enumerate(sheet.get_all_values()):
            if idx == 0 or len(row) < 19:
                continue
            if str(row[18]).strip() != str(chat_id):
                continue
            result.append({"id": row[1], "status": row[0].upper().strip()})
        return result
    except Exception as e:
        logging.error(f"Ошибка чтения статусов заказов клиента {chat_id}: {e}")
        return []

def _sync_update_profile(chat_id: str, new_fio: str, new_address: str, lang: str | None = None) -> bool:
    """Обновляет ФИО (C), адрес забора (E) и, если передан, язык (H) клиента по Chat ID (F = 6)."""
    if not clients_sheet:
        return False
    try:
        cell = clients_sheet.find(str(chat_id), in_column=6)
        if not cell:
            return False
        updates = [
            {'range': f'C{cell.row}', 'values': [[new_fio]]},
            {'range': f'E{cell.row}', 'values': [[new_address or '']]},
        ]
        if lang in ("ru", "tj"):
            updates.append({'range': f'H{cell.row}', 'values': [[lang]]})
        clients_sheet.batch_update(updates)
        return True
    except Exception as e:
        logging.error(f"Ошибка обновления профиля для chat_id={chat_id}: {e}")
        return False


def _lang_from_row(row, fallback: str = "ru") -> str:
    """Достаёт сохранённое предпочтение языка из строки листа Клиенты (H = 8)."""
    if row and len(row) > 7 and row[7] in ("ru", "tj"):
        return row[7]
    return fallback

def _sync_append_row(row_data: list):
    if sheet:
        sheet.append_row(row_data, table_range="A1")

def _sync_register_client(chat_id: str, fio: str, phone: str, lang: str = "ru") -> bool:
    if not clients_sheet:
        return False
    try:
        now = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=5)).strftime("%d.%m.%Y %H:%M")
        clients_sheet.append_row(["ACTIVE", now, fio, phone, "", str(chat_id), "", lang], table_range="A1")
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

# Последнее сообщение шага регистрации — чтоб цепочка "welcome → не найден → ФИО → готово" схлопывалась в одно
_status_msgs: dict[int, int] = {}


async def _try_delete(message: types.Message):
    try:
        await message.delete()
    except Exception:
        pass


async def _replace_status_message(chat_id: int, text: str, **kwargs):
    old_id = _status_msgs.pop(chat_id, None)
    if old_id:
        try:
            await bot.delete_message(chat_id, old_id)
        except Exception:
            pass
    sent = await bot.send_message(chat_id, text, **kwargs)
    _status_msgs[chat_id] = sent.message_id
    return sent


@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await _try_delete(message)
    user_data = await asyncio.to_thread(_sync_check_user_by_chat_id, str(message.chat.id))
    if user_data:
        fio = user_data[2] if len(user_data) > 2 else "Пользователь"
        phone = user_data[3] if len(user_data) > 3 else ""
        lang = _lang_from_row(user_data)
        await message.answer(
            CL[lang]["welcome_back"].format(fio=fio),
            reply_markup=await get_main_menu(fio, phone, lang, message.chat.id),
            parse_mode="Markdown"
        )
        return

    b = InlineKeyboardBuilder()
    b.button(text="🇷🇺 Русский", callback_data="clientlang:ru")
    b.button(text="🇹🇯 Тоҷикӣ", callback_data="clientlang:tj")
    b.adjust(2)
    sent = await message.answer("🌐 Выберите язык / Забонро интихоб кунед:", reply_markup=b.as_markup())
    _status_msgs[message.chat.id] = sent.message_id
    await state.set_state(Registration.waiting_for_lang)


@dp.callback_query(F.data.startswith("clientlang:"), Registration.waiting_for_lang)
async def set_client_lang(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    lang = callback.data.split(":")[1]
    if lang not in ("ru", "tj"):
        lang = "ru"
    await state.update_data(client_lang=lang)
    b = ReplyKeyboardBuilder()
    b.add(types.KeyboardButton(text=AUTH_BTN[lang], request_contact=True))
    try:
        await callback.message.delete()
    except Exception:
        pass
    _status_msgs.pop(callback.from_user.id, None)
    await _replace_status_message(
        callback.message.chat.id,
        CL[lang]["welcome"],
        reply_markup=b.as_markup(resize_keyboard=True),
        parse_mode="Markdown"
    )


@dp.message(F.text.in_({BACK_BTN["ru"], BACK_BTN["tj"]}))
async def go_main_menu(message: types.Message, state: FSMContext):
    await state.clear()
    await _try_delete(message)
    user_data = await asyncio.to_thread(_sync_check_user_by_chat_id, str(message.chat.id))
    fio   = user_data[2] if user_data and len(user_data) > 2 else "Пользователь"
    phone = user_data[3] if user_data and len(user_data) > 3 else ""
    lang  = _lang_from_row(user_data)
    await message.answer(CL[lang]["menu_prompt"], reply_markup=await get_main_menu(fio, phone, lang, message.chat.id))


@dp.message(F.contact)
async def process_contact(message: types.Message, state: FSMContext):
    phone = message.contact.phone_number
    if not phone.startswith("+"):
        phone = "+" + phone
    await _try_delete(message)

    data = await state.get_data()
    lang = data.get("client_lang", "ru")
    await state.update_data(phone=phone, client_lang=lang)
    user_data = await asyncio.to_thread(_sync_check_user_by_phone, phone)

    if user_data:
        fio = user_data[2] if len(user_data) > 2 else "Пользователь"
        real_lang = _lang_from_row(user_data, fallback=lang)
        await _replace_status_message(
            message.chat.id,
            CL[real_lang]["welcome_back"].format(fio=fio),
            reply_markup=await get_main_menu(fio, phone, real_lang, message.chat.id),
            parse_mode="Markdown"
        )
        _status_msgs.pop(message.chat.id, None)
    else:
        b = ReplyKeyboardBuilder()
        b.add(types.KeyboardButton(text=ACCEPT_OFFER_BTN[lang]))
        await _replace_status_message(
            message.chat.id,
            CL[lang]["not_found"].format(url=LINK_TO_OFFER),
            reply_markup=b.as_markup(resize_keyboard=True),
            parse_mode="Markdown",
            disable_web_page_preview=True
        )

@dp.message(F.text.in_({ACCEPT_OFFER_BTN["ru"], ACCEPT_OFFER_BTN["tj"]}))
async def start_fio_step(message: types.Message, state: FSMContext):
    await _try_delete(message)
    data = await state.get_data()
    lang = data.get("client_lang", "ru")
    await _replace_status_message(
        message.chat.id,
        CL[lang]["ask_fio"],
        reply_markup=types.ReplyKeyboardRemove(),
        parse_mode="Markdown"
    )
    await state.set_state(Registration.waiting_for_fio)

@dp.message(Registration.waiting_for_fio)
async def save_fio(message: types.Message, state: FSMContext):
    fio = message.text.strip()
    data = await state.get_data()
    phone = data.get('phone')
    lang = data.get('client_lang', 'ru')
    await state.clear()
    await _try_delete(message)
    if not phone:
        await message.answer(CL[lang]["session_expired"])
        return

    await asyncio.to_thread(_sync_register_client, str(message.chat.id), fio, phone, lang)

    await _replace_status_message(
        message.chat.id,
        CL[lang]["registered"].format(fio=fio),
        reply_markup=await get_main_menu(fio, phone, lang, message.chat.id),
        parse_mode="Markdown"
    )
    _status_msgs.pop(message.chat.id, None)

async def get_main_menu(fio: str, phone: str, lang: str = "ru", chat_id=None):
    safe_fio = urllib.parse.quote(fio)
    safe_phone = urllib.parse.quote(str(phone))
    final_url = f"{WEB_APP_URL}&fio={safe_fio}&phone={safe_phone}"

    if chat_id is not None:
        try:
            orders = await asyncio.to_thread(_sync_get_client_order_statuses, str(chat_id))
            if orders:
                b64 = base64.urlsafe_b64encode(
                    json.dumps({"orders": orders}, ensure_ascii=False, separators=(",", ":")).encode()
                ).decode().rstrip("=")
                final_url += f"&d={b64}"
        except Exception as e:
            logging.error(f"Ошибка встраивания статусов заказов в WebApp URL для {chat_id}: {e}")

    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(
        text=ORDER_BTN[lang],
        web_app=types.WebAppInfo(url=final_url)
    ))
    builder.add(types.KeyboardButton(text=SUPPORT_BTN[lang]))
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
            new_lang = data.get("lang") if data.get("lang") in VALID_LANGS else None

            user_data_now = await asyncio.to_thread(_sync_check_user_by_chat_id, str(message.chat.id))
            lang = new_lang or _lang_from_row(user_data_now)
            if not updated_fio:
                await message.answer(CL[lang]["fio_empty"])
                return

            # Ищем пользователя по chat_id (не по phone из запроса — безопасно)
            success = await asyncio.to_thread(
                _sync_update_profile,
                str(message.chat.id),
                updated_fio,
                updated_addr,
                new_lang
            )
            if success:
                user_data = await asyncio.to_thread(_sync_check_user_by_chat_id, str(message.chat.id))
                phone_from_db = user_data[3] if user_data and len(user_data) > 3 else ""
                lang = _lang_from_row(user_data)
                await message.answer(
                    CL[lang]["profile_updated"].format(
                        fio=updated_fio,
                        addr=updated_addr if updated_addr else CL[lang]["addr_missing"]
                    ),
                    reply_markup=await get_main_menu(updated_fio, phone_from_db, lang, message.chat.id),
                    parse_mode="Markdown"
                )
            else:
                await message.answer(CL[lang]["profile_user_not_found"])
            return

        # --- Новый заказ ---
        error_msg = validate_order_data(data)
        if error_msg:
            logging.warning(f"Невалидные данные формы от chat_id={message.chat.id}: {error_msg}")
            await message.answer(f"❌ Ошибка в данных формы: {error_msg}. Попробуйте снова.")
            return

        # Сохранённое предпочтение языка — приоритетнее переключателя внутри формы
        stored_user = await asyncio.to_thread(_sync_check_user_by_chat_id, str(message.chat.id))
        stored_lang = _lang_from_row(stored_user)
        if stored_lang in VALID_LANGS:
            lang = stored_lang
        else:
            lang = data.get('lang', 'ru') if data.get('lang') in VALID_LANGS else 'ru'

        dtype_readable = "Ба ПВЗ 🏢" if data['delivery_type'] == "pvz" else "То дар 🚪"
        if lang == "ru":
            dtype_readable = "До ПВЗ 🏢" if data['delivery_type'] == "pvz" else "До двери 🚪"

        utc_now = datetime.datetime.now(datetime.timezone.utc)
        dushanbe_time = (utc_now + datetime.timedelta(hours=5)).strftime("%d.%m.%Y %H:%M")

        # ID обычно приходит от клиента (тот же формат, что и на сервере) — чтобы карточка
        # в локальной "Истории" клиента совпадала с реальным ID. Валидируем строго по формату,
        # при несовпадении/отсутствии — генерируем сами.
        client_order_id = str(data.get("order_id", ""))
        order_id = client_order_id if ORDER_ID_RE.match(client_order_id) else generate_order_id()

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

        # Запись в чистый лист «Заказы»
        if orders_info_sheet:
            def _sync_append_order_info():
                dtype_plain = "До ПВЗ" if data['delivery_type'] == "pvz" else "До двери"
                orders_info_sheet.append_row([
                    order_id,                        # A — ID заказа
                    dushanbe_time,                   # B — Дата
                    "NEW",                           # C — Статус
                    s(data['price']),                # D — Цена (TJS)
                    dtype_plain,                      # E — Тип доставки
                    s(data['weight']),               # F — Вес (кг)
                    s(data['sizes']),                # G — Габариты
                    s(data['s_name']),               # H — ФИО отправителя
                    s(data['s_phone']),              # I — Тел отправителя
                    s(data['city_pickup']),          # J — Город откуда
                    s(data['address_pickup']),       # K — Адрес откуда
                    s(data['r_name']),               # L — ФИО получателя
                    s(data['r_phone']),              # M — Тел получателя
                    s(data['city_delivery']),        # N — Город куда
                    s(data['address_delivery']),     # O — Адрес куда
                    s(data['driver_comment']),       # P — Ориентир
                ], table_range="A1")
            try:
                await asyncio.to_thread(_sync_append_order_info)
            except Exception as e:
                logging.error(f"Ошибка записи в лист Заказы: {e}")

        # Уведомление менеджерам о новом заказе
        if mgr_bot:
            manager_ids = await asyncio.to_thread(get_manager_chat_ids)
            if manager_ids:
                from aiogram.utils.keyboard import InlineKeyboardBuilder as IKB
                dtype_mgr = "До ПВЗ 🏢" if data['delivery_type'] == "pvz" else "До двери 🚪"
                e = html.escape
                mgr_text = (
                    f"🆕 <b>Новый заказ</b> <code>{order_id}</code>\n\n"
                    f"📍 <b>{e(str(data['city_pickup']))}</b> → <b>{e(str(data['city_delivery']))}</b> · {dtype_mgr}\n"
                    f"👤 Отправитель: {e(str(data['s_name']))} · <code>{e(str(data['s_phone']))}</code>\n"
                    f"👤 Получатель: {e(str(data['r_name']))} · <code>{e(str(data['r_phone']))}</code>\n"
                    f"📦 {e(str(data['weight']))} кг · {e(str(data['sizes']))} см\n"
                    f"💰 {e(str(data['price']))} TJS\n"
                    f"📅 {dushanbe_time}"
                )
                b = IKB()
                b.button(text="✅ Принять", callback_data=f"oa:{order_id}")
                b.button(text="❌ Отменить", callback_data=f"oc:{order_id}")
                b.adjust(2)
                for mgr_id in manager_ids:
                    try:
                        await mgr_bot.send_message(
                            chat_id=int(mgr_id),
                            text=mgr_text,
                            reply_markup=b.as_markup(),
                            parse_mode="HTML"
                        )
                    except Exception as e:
                        logging.error(f"Не удалось уведомить менеджера {mgr_id} о заказе {order_id}: {e}")

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

@dp.message(F.text.in_({SUPPORT_BTN["ru"], SUPPORT_BTN["tj"]}))
async def support_start(message: types.Message, state: FSMContext):
    await _try_delete(message)
    user_data = await asyncio.to_thread(_sync_check_user_by_chat_id, str(message.chat.id))
    lang = _lang_from_row(user_data)
    if not SUPPORT_CHAT_ID:
        await message.answer(CL[lang]["support_unavailable"])
        return
    await message.answer(
        CL[lang]["support_prompt"],
        reply_markup=types.ReplyKeyboardRemove(),
        parse_mode="HTML"
    )
    await state.update_data(lang=lang)
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
            text=f"📨 <b>Клиент:</b> {html.escape(message.text)}",
            parse_mode="HTML"
        )
        lang = _lang_from_row(user_data)
        back_kb = ReplyKeyboardBuilder()
        back_kb.button(text=BACK_BTN[lang])
        await state.update_data(fio=fio, phone=phone, topic_id=topic_id, lang=lang)
        await state.set_state(Support.chatting)
        await message.answer(
            CL[lang]["support_sent"],
            reply_markup=back_kb.as_markup(resize_keyboard=True),
        )
    except Exception as e:
        logging.error(f"Ошибка поддержки (SUPPORT_CHAT_ID={SUPPORT_CHAT_ID}): {type(e).__name__}: {e}")
        lang = _lang_from_row(user_data)
        if topic_id:
            # топик создан, но отправка не удалась — сохраняем state чтобы не создавать дубль
            await state.update_data(fio=fio, phone=phone, topic_id=topic_id, lang=lang)
            await state.set_state(Support.chatting)
        else:
            await state.clear()
        await message.answer(CL[lang]["support_error"], reply_markup=await get_main_menu(fio, phone, lang, message.chat.id))


@dp.message(Support.chatting)
async def support_continue(message: types.Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "ru")
    topic_id = data.get("topic_id")
    if not topic_id:
        await state.clear()
        await message.answer(CL[lang]["support_session_expired"])
        return

    try:
        await bot.send_message(
            chat_id=int(SUPPORT_CHAT_ID),
            message_thread_id=topic_id,
            text=f"📨 <b>Клиент:</b> {html.escape(message.text)}",
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Ошибка отправки в топик: {type(e).__name__}: {e}")
        await message.answer(CL[lang]["support_send_failed"])


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

    client_data = await asyncio.to_thread(_sync_check_user_by_chat_id, str(client_chat_id))
    lang = _lang_from_row(client_data)
    try:
        await bot.send_message(
            chat_id=int(client_chat_id),
            text=CL[lang]["support_reply_header"].format(text=html.escape(message.text)),
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Ошибка отправки ответа клиенту {client_chat_id}: {type(e).__name__}: {e}")


