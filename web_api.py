"""
HTTP API для трёх WebApp (client.html, driver_cabinet.html, admin_panel.html).

Запускается в том же процессе/контейнере, что и боты (см. main.py), поэтому
напрямую переиспользует их sync-функции работы с Google Sheets и живые
объекты aiogram Bot для отправки уведомлений — без второго gspread-клиента
и без HTTP-прослойки между API и ботами.

Аутентификация — Telegram WebApp initData (HMAC-SHA256), проверяется той же
логикой, что и раньше для sendData-хендлеров (см. config.verify_init_data).
"""
import asyncio
import logging
import os

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
from pydantic import BaseModel

from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import (
    CLIENT_TOKEN,
    DRIVER_TOKEN,
    MANAGER_TOKEN,
    driver_bot as driver_bot_instance,
    sheet,
    verify_init_data,
    extract_user_id,
)
from client_bot import _sync_check_user_by_chat_id, _sync_update_profile
from driver_bot import (
    _pad_row,
    _sync_get_driver,
    _sync_get_driver_deliveries,
    _sync_reassign_order,
    _async_get_admin_dashboard_data,
    _month_range,
    _current_week_range,
    _week_label,
    DEFAULT_DRIVER_RATE,
    DUSHANBE_TZ,
    send_client_push,
)
from manager_bot import _sync_set_order_ready, _sync_change_order_status

MANAGER_CHAT_ID = os.getenv("MANAGER_CHAT_ID", "")
CORS_ORIGIN = os.getenv("WEBAPP_CORS_ORIGIN", "https://gostwarrir186.github.io")

app = FastAPI(title="Mavsimi Rason WebApp API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[CORS_ORIGIN],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Telegram-Init-Data"],
)


def _auth_dependency(bot_token: str | None, require_manager: bool = False):
    async def dependency(x_telegram_init_data: str = Header(..., alias="X-Telegram-Init-Data")) -> int:
        verified = verify_init_data(bot_token, x_telegram_init_data)
        if not verified:
            raise HTTPException(status_code=401, detail="invalid initData")
        user_id = extract_user_id(verified)
        if not user_id:
            raise HTTPException(status_code=401, detail="no user id in initData")
        if require_manager and (not MANAGER_CHAT_ID or str(user_id) != str(MANAGER_CHAT_ID)):
            raise HTTPException(status_code=403, detail="not the manager")
        return user_id
    return dependency


client_auth = _auth_dependency(CLIENT_TOKEN)
driver_auth = _auth_dependency(DRIVER_TOKEN)
manager_auth = _auth_dependency(MANAGER_TOKEN, require_manager=True)


# ─── Клиент ──────────────────────────────────────────────────────────────────

@app.get("/api/v1/client/profile")
async def client_get_profile(user_id: int = Depends(client_auth)):
    user_data = await asyncio.to_thread(_sync_check_user_by_chat_id, str(user_id))
    if not user_data:
        raise HTTPException(status_code=404, detail="client not found")
    return {
        "fio": user_data[2] if len(user_data) > 2 else "",
        "phone": user_data[3] if len(user_data) > 3 else "",
        "address": user_data[4] if len(user_data) > 4 else "",
    }


class ProfileBody(BaseModel):
    fio: str
    address: str = ""


@app.post("/api/v1/client/profile")
async def client_update_profile(body: ProfileBody, user_id: int = Depends(client_auth)):
    fio = body.fio.strip()
    address = body.address.strip()
    if not fio:
        raise HTTPException(status_code=400, detail="ФИО не может быть пустым")
    success = await asyncio.to_thread(_sync_update_profile, str(user_id), fio, address)
    if not success:
        raise HTTPException(status_code=404, detail="client not found")
    user_data = await asyncio.to_thread(_sync_check_user_by_chat_id, str(user_id))
    return {
        "fio": fio,
        "phone": user_data[3] if user_data and len(user_data) > 3 else "",
        "address": address,
    }


# ─── Курьер ──────────────────────────────────────────────────────────────────

@app.get("/api/v1/driver/cabinet")
async def driver_cabinet(user_id: int = Depends(driver_auth)):
    driver_data = await asyncio.to_thread(_sync_get_driver, str(user_id))
    if not driver_data or driver_data[0].upper() != "ACTIVE":
        raise HTTPException(status_code=403, detail="driver not active")
    fio = driver_data[2] if len(driver_data) > 2 else "Курьер"
    rate = float(driver_data[4]) if len(driver_data) > 4 and driver_data[4] else DEFAULT_DRIVER_RATE
    now = datetime.now(DUSHANBE_TZ)
    date_from, date_to = _month_range(now)
    week_start_dt, week_end_dt = _current_week_range(now)
    deliveries = await asyncio.to_thread(_sync_get_driver_deliveries, str(user_id), date_from, date_to)
    return {
        "name": fio,
        "rate": rate,
        "month": now.strftime("%m.%Y"),
        "month_label": _week_label(week_start_dt, week_end_dt),
        "deliveries": deliveries,
    }


# ─── Менеджер ────────────────────────────────────────────────────────────────

@app.get("/api/v1/manager/dashboard")
async def manager_dashboard(_: int = Depends(manager_auth)):
    return await _async_get_admin_dashboard_data()


@app.post("/api/v1/manager/orders/{order_id}/set-ready")
async def manager_set_ready(order_id: str, _: int = Depends(manager_auth)):
    success, client_chat_id, err = await asyncio.to_thread(_sync_set_order_ready, order_id)
    if not success:
        raise HTTPException(status_code=400, detail=err or "failed")
    if client_chat_id:
        await send_client_push(
            client_chat_id,
            f"📦 Ваш заказ *{order_id}* принят и передан в доставку!\nОжидайте назначения курьера."
        )
    return await _async_get_admin_dashboard_data()


class StatusBody(BaseModel):
    new_status: str
    new_status_label: str | None = None


@app.post("/api/v1/manager/orders/{order_id}/status")
async def manager_change_status(order_id: str, body: StatusBody, _: int = Depends(manager_auth)):
    label = body.new_status_label or body.new_status
    success, client_chat_id, courier_id, err = await asyncio.to_thread(
        _sync_change_order_status, order_id, body.new_status
    )
    if not success:
        raise HTTPException(status_code=400, detail=err or "failed")
    if courier_id:
        try:
            await driver_bot_instance.send_message(
                chat_id=int(courier_id),
                text=f"📋 <b>Статус заказа {order_id} изменён менеджером:</b> {label}",
                parse_mode="HTML"
            )
        except Exception as e:
            logging.error(f"Не удалось уведомить курьера {courier_id}: {e}")
    if client_chat_id:
        await send_client_push(
            client_chat_id,
            f"📦 Статус вашего заказа *{order_id}* обновлён: *{label}*"
        )
    return await _async_get_admin_dashboard_data()


class CancelBody(BaseModel):
    reason: str = "Отменён менеджером"


@app.post("/api/v1/manager/orders/{order_id}/cancel")
async def manager_cancel_active(order_id: str, body: CancelBody, _: int = Depends(manager_auth)):
    success, client_chat_id, courier_id, err = await asyncio.to_thread(
        _sync_change_order_status, order_id, "CANCELLED"
    )
    if not success:
        raise HTTPException(status_code=400, detail=err or "failed")
    if courier_id:
        try:
            await driver_bot_instance.send_message(
                chat_id=int(courier_id),
                text=f"⚠️ <b>Заказ {order_id} отменён менеджером.</b>\nПричина: {body.reason}",
                parse_mode="HTML"
            )
        except Exception as e:
            logging.error(f"Не удалось уведомить курьера {courier_id}: {e}")
    if client_chat_id:
        await send_client_push(
            client_chat_id,
            f"❌ Ваш заказ *{order_id}* был отменён.\n📝 Причина: {body.reason}"
        )
    return await _async_get_admin_dashboard_data()


class ReassignBody(BaseModel):
    order_row: int
    courier_tid: str
    courier_name: str


@app.post("/api/v1/manager/orders/{order_id}/reassign")
async def manager_reassign(order_id: str, body: ReassignBody, _: int = Depends(manager_auth)):
    success, old_courier_id, confirmed_order_id = await asyncio.to_thread(
        _sync_reassign_order, body.order_row, body.courier_name, body.courier_tid
    )
    if not success:
        raise HTTPException(status_code=400, detail="Проверьте статус заказа")

    order_row_vals = _pad_row(await asyncio.to_thread(sheet.row_values, body.order_row))
    client_chat_id = order_row_vals[18]
    city_from = order_row_vals[4]
    city_to = order_row_vals[6]

    if old_courier_id and old_courier_id != body.courier_tid:
        try:
            await driver_bot_instance.send_message(
                chat_id=int(old_courier_id),
                text=f"⚠️ <b>Ваш заказ {confirmed_order_id} переназначен другому курьеру.</b>",
                parse_mode="HTML"
            )
        except Exception as e:
            logging.error(f"Не удалось уведомить старого курьера: {e}")

    kb = InlineKeyboardBuilder()
    kb.button(text="📦 Приступить к погрузке", callback_data=f"load:{body.order_row}")
    try:
        await driver_bot_instance.send_message(
            chat_id=int(body.courier_tid),
            text=(
                f"📦 <b>Вам назначен заказ {confirmed_order_id}!</b>\n"
                f"📍 {city_from} → {city_to}\n\n"
                f"Нажмите кнопку, когда начнёте погрузку:"
            ),
            reply_markup=kb.as_markup(),
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Не удалось уведомить нового курьера {body.courier_tid}: {e}")

    if client_chat_id:
        await send_client_push(
            client_chat_id,
            f"🔄 Ваш заказ *{confirmed_order_id}* передан новому курьеру: *{body.courier_name}*."
        )
    return await _async_get_admin_dashboard_data()
