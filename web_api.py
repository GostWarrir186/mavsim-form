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
from datetime import datetime, timedelta
from pydantic import BaseModel

from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import (
    CLIENT_TOKEN,
    DRIVER_TOKEN,
    MANAGER_TOKEN,
    client_bot as client_bot_instance,
    driver_bot as driver_bot_instance,
    manager_bot as manager_bot_instance,
    sheet,
    verify_init_data,
    extract_user_id,
)
from client_bot import (
    _sync_check_user_by_chat_id,
    _sync_update_profile,
    _sync_append_row,
    validate_order_data,
    generate_order_id,
    sanitize_for_sheet,
    RECEIPTS,
    VALID_LANGS,
    orders_info_sheet,
)
from driver_bot import (
    _pad_row,
    _sync_get_driver,
    _sync_get_driver_deliveries,
    _sync_reassign_order,
    _sync_get_all_active_drivers,
    _async_get_admin_dashboard_data,
    _month_range,
    _current_week_range,
    _week_label,
    generate_excel_report,
    DEFAULT_DRIVER_RATE,
    DUSHANBE_TZ,
    send_client_push,
)
from manager_bot import (
    _sync_set_order_ready,
    _sync_change_order_status,
    _sync_get_all_couriers_deliveries,
    _sync_get_all_drivers_rates,
    generate_summary_excel,
)

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
        logging.warning(f"[DEBUG initData] RAW len={len(x_telegram_init_data)} value={x_telegram_init_data!r}")
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

@app.get("/v1/client/profile")
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


@app.post("/v1/client/profile")
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


@app.post("/v1/client/orders")
async def client_create_order(body: dict, user_id: int = Depends(client_auth)):
    error_msg = validate_order_data(body)
    if error_msg:
        raise HTTPException(status_code=400, detail=error_msg)

    lang = body.get('lang', 'ru') if body.get('lang') in VALID_LANGS else 'ru'
    dtype_readable = "Ба ПВЗ 🏢" if body['delivery_type'] == "pvz" else "То дар 🚪"
    if lang == "ru":
        dtype_readable = "До ПВЗ 🏢" if body['delivery_type'] == "pvz" else "До двери 🚪"

    dushanbe_time = datetime.now(DUSHANBE_TZ).strftime("%d.%m.%Y %H:%M")
    order_id = generate_order_id()
    s = sanitize_for_sheet

    row = [
        "NEW", order_id, dushanbe_time, s(body['price']),
        s(body['city_pickup']), s(body['address_pickup']),
        s(body['city_delivery']), s(body['address_delivery']),
        s(body['driver_comment']), body['delivery_type'].upper(),
        s(body['weight']), s(body['sizes']),
        s(body['s_name']), s(body['s_phone']),
        s(body['r_name']), s(body['r_phone']),
        "bot_webapp", "", str(user_id), "",
    ]
    await asyncio.to_thread(_sync_append_row, row)

    if orders_info_sheet:
        def _append_order_info():
            dtype_plain = "До ПВЗ" if body['delivery_type'] == "pvz" else "До двери"
            orders_info_sheet.append_row([
                order_id, dushanbe_time, "NEW", s(body['price']), dtype_plain,
                s(body['weight']), s(body['sizes']), s(body['s_name']), s(body['s_phone']),
                s(body['city_pickup']), s(body['address_pickup']),
                s(body['r_name']), s(body['r_phone']),
                s(body['city_delivery']), s(body['address_delivery']), s(body['driver_comment']),
            ], table_range="A1")
        try:
            await asyncio.to_thread(_append_order_info)
        except Exception as e:
            logging.error(f"Ошибка записи в лист Заказы: {e}")

    if MANAGER_CHAT_ID:
        try:
            dtype_mgr = "До ПВЗ 🏢" if body['delivery_type'] == "pvz" else "До двери 🚪"
            mgr_text = (
                f"🆕 <b>Новый заказ</b> <code>{order_id}</code>\n\n"
                f"📍 <b>{body['city_pickup']}</b> → <b>{body['city_delivery']}</b> · {dtype_mgr}\n"
                f"👤 Отправитель: {body['s_name']} · <code>{body['s_phone']}</code>\n"
                f"👤 Получатель: {body['r_name']} · <code>{body['r_phone']}</code>\n"
                f"📦 {body['weight']} кг · {body['sizes']} см\n"
                f"💰 {body['price']} TJS\n"
                f"📅 {dushanbe_time}"
            )
            kb = InlineKeyboardBuilder()
            kb.button(text="✅ Принять", callback_data=f"oa:{order_id}")
            kb.button(text="❌ Отменить", callback_data=f"oc:{order_id}")
            kb.adjust(2)
            await manager_bot_instance.send_message(
                chat_id=int(MANAGER_CHAT_ID), text=mgr_text,
                reply_markup=kb.as_markup(), parse_mode="HTML"
            )
        except Exception as e:
            logging.error(f"Не удалось уведомить менеджера о заказе {order_id}: {e}")

    receipt = RECEIPTS[lang].format(
        date=dushanbe_time, order_id=order_id,
        s_name=body['s_name'], s_phone=body['s_phone'],
        city_pickup=body['city_pickup'], address_pickup=body['address_pickup'],
        city_delivery=body['city_delivery'], address_delivery=body['address_delivery'],
        delivery_type=dtype_readable,
        r_name=body['r_name'], r_phone=body['r_phone'],
        price=body['price']
    )
    try:
        await client_bot_instance.send_message(chat_id=user_id, text=receipt, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Не удалось отправить чек клиенту {user_id}: {e}")

    return {"order_id": order_id, "receipt": receipt}


# ─── Курьер ──────────────────────────────────────────────────────────────────

@app.get("/v1/driver/cabinet")
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


class ReportBody(BaseModel):
    week_start: str


@app.post("/v1/driver/report")
async def driver_report(body: ReportBody, user_id: int = Depends(driver_auth)):
    try:
        week_start = datetime.strptime(body.week_start, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="некорректный формат даты")
    week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
    period_label = _week_label(week_start, week_end)

    driver_data = await asyncio.to_thread(_sync_get_driver, str(user_id))
    if not driver_data or driver_data[0].upper() != "ACTIVE":
        raise HTTPException(status_code=403, detail="driver not active")
    fio = driver_data[2] if len(driver_data) > 2 else "Курьер"
    try:
        rate = float(driver_data[4]) if len(driver_data) > 4 and driver_data[4] else DEFAULT_DRIVER_RATE
    except (ValueError, TypeError):
        rate = DEFAULT_DRIVER_RATE

    deliveries = await asyncio.to_thread(_sync_get_driver_deliveries, str(user_id), week_start, week_end)
    if not deliveries:
        raise HTTPException(status_code=404, detail=f"За {period_label} доставок не найдено")

    excel_buf = await asyncio.to_thread(generate_excel_report, fio, rate, deliveries, period_label)
    delivered_count = sum(1 for d in deliveries if d["s"] == "DELIVERED")
    await driver_bot_instance.send_document(
        chat_id=user_id,
        document=types.BufferedInputFile(excel_buf.read(), filename=f"report_{body.week_start}_{user_id}.xlsx"),
        caption=(
            f"📄 Отчёт: {period_label}\n"
            f"👤 {fio}\n"
            f"✅ Доставлено: {delivered_count}\n"
            f"💰 К выплате: {delivered_count * rate:.2f} TJS"
        ),
    )
    return {"ok": True}


# ─── Менеджер ────────────────────────────────────────────────────────────────

@app.get("/v1/manager/dashboard")
async def manager_dashboard(_: int = Depends(manager_auth)):
    return await _async_get_admin_dashboard_data()


@app.post("/v1/manager/orders/{order_id}/set-ready")
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


@app.post("/v1/manager/orders/{order_id}/status")
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


@app.post("/v1/manager/orders/{order_id}/cancel")
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


@app.post("/v1/manager/orders/{order_id}/reassign")
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


class CourierReportBody(BaseModel):
    courier_tid: str
    courier_name: str = "Курьер"
    week_start: str


@app.post("/v1/manager/report")
async def manager_report(body: CourierReportBody, _: int = Depends(manager_auth)):
    try:
        week_start = datetime.strptime(body.week_start, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="некорректный формат даты")
    week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
    period_label = _week_label(week_start, week_end)

    driver_data = await asyncio.to_thread(_sync_get_driver, body.courier_tid)
    try:
        rate = float(driver_data[4]) if driver_data and len(driver_data) > 4 and driver_data[4] else DEFAULT_DRIVER_RATE
    except (ValueError, TypeError):
        rate = DEFAULT_DRIVER_RATE

    deliveries = await asyncio.to_thread(_sync_get_driver_deliveries, body.courier_tid, week_start, week_end)
    if not deliveries:
        raise HTTPException(status_code=404, detail=f"За {period_label} у курьера {body.courier_name} доставок не найдено")

    excel_buf = await asyncio.to_thread(generate_excel_report, body.courier_name, rate, deliveries, period_label)
    delivered_count = sum(1 for d in deliveries if d["s"] == "DELIVERED")
    await manager_bot_instance.send_document(
        chat_id=MANAGER_CHAT_ID,
        document=types.BufferedInputFile(excel_buf.read(), filename=f"report_{body.courier_tid}_{body.week_start}.xlsx"),
        caption=(
            f"📄 <b>Отчёт: {period_label}</b>\n"
            f"👤 {body.courier_name}\n"
            f"✅ Доставлено: {delivered_count}\n"
            f"💰 К выплате: {delivered_count * rate:.2f} TJS"
        ),
        parse_mode="HTML",
    )
    return {"ok": True}


class ReportAllBody(BaseModel):
    week_start: str


@app.post("/v1/manager/report-all")
async def manager_report_all(body: ReportAllBody, _: int = Depends(manager_auth)):
    try:
        week_start = datetime.strptime(body.week_start, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="некорректный формат даты")
    week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
    period_label = _week_label(week_start, week_end)

    active_couriers = await asyncio.to_thread(_sync_get_all_active_drivers)
    if not active_couriers:
        raise HTTPException(status_code=404, detail="Нет активных курьеров")

    deliveries_map, rates_map = await asyncio.gather(
        asyncio.to_thread(_sync_get_all_couriers_deliveries, week_start, week_end),
        asyncio.to_thread(_sync_get_all_drivers_rates),
    )

    couriers_stats = []
    for c in active_couriers:
        rate = rates_map.get(c["telegram_id"], DEFAULT_DRIVER_RATE)
        stats = deliveries_map.get(c["telegram_id"], {"total": 0, "delivered": 0})
        couriers_stats.append({
            "fio": c["fio"], "rate": rate,
            "total": stats["total"], "delivered": stats["delivered"],
        })

    excel_buf = await asyncio.to_thread(generate_summary_excel, couriers_stats, period_label)
    total_earn = sum(c["delivered"] * c["rate"] for c in couriers_stats)
    total_deliv = sum(c["delivered"] for c in couriers_stats)
    await manager_bot_instance.send_document(
        chat_id=MANAGER_CHAT_ID,
        document=types.BufferedInputFile(excel_buf.read(), filename=f"summary_{body.week_start}.xlsx"),
        caption=(
            f"📊 <b>Сводный отчёт: {period_label}</b>\n"
            f"👥 Курьеров: {len(couriers_stats)}\n"
            f"✅ Доставлено: {total_deliv}\n"
            f"💰 Итого к выплате: {total_earn:.2f} TJS"
        ),
        parse_mode="HTML",
    )
    return {"ok": True}
