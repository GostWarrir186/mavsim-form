# Выжимка сессии — 2026-06-24 (сессия 2)

## 1. Проект

**Mavsimi Rason** — система доставки (Таджикистан, валюта TJS).  
Репозиторий бота: `/Users/azam/Desktop/mavsim-delivery-bot`  
Репозиторий GitHub Pages (HTML): `git@github.com:GostWarrir186/mavsim-form.git`  
Клон GitHub Pages: `/private/tmp/mavsim-form/`  
GitHub Pages: `https://gostwarrir186.github.io/mavsim-form/`  
Стек: Python 3.x, aiogram 3.x, gspread, Google Sheets как БД, GitHub Pages (HTML/JS без фреймворков).  
Три бота запускаются одним процессом через `asyncio.gather` в `main.py`.

---

## 2. Структура проекта

```
mavsim-delivery-bot/
├── main.py                  # запуск трёх ботов
├── config.py                # токены, gspread, 4 листа
├── client_bot.py            # клиентский бот
├── driver_bot.py            # курьерский бот
├── manager_bot.py           # менеджерский бот
├── creds.json               # Google service account
├── requirements.txt
├── .env
├── CLAUDE.md
└── web/
    ├── client.html          # WebApp клиента
    ├── driver_cabinet.html  # WebApp кабинет курьера (объединён с отчётом)
    ├── report_picker.html   # (устарел)
    └── admin_panel.html     # WebApp панель менеджера
```

**Google Sheets — «Заявки Mavsimi Rason», 4 листа:**
- **Лист1** (заказы): A=статус, B=order_id, C=дата, D=цена, E=город_откуда, F=адрес_откуда, G=город_куда, H=адрес_куда, I=ориентир, J=тип(PVZ/DOOR), K=вес, L=габариты, M=ФИО_отправителя, N=тел_отправителя, O=ФИО_получателя, P=тел_получателя, Q=источник, R=имя_курьера, S=chat_id_клиента, T=telegram_id_курьера
- **Клиенты**: A=статус, B=дата_рег, C=ФИО, D=телефон, E=адрес_забора, F=chat_id, G=support_topic_id
- **Водители**: A=статус(PENDING/ACTIVE/REJECTED), B=дата_рег, C=ФИО, D=telegram_id, E=ставка_TJS, F=дата_оферты, G=support_topic_id, H=телефон
- **Заказы** (чистый): A=ID, B=дата, C=статус, D=цена, E=тип, F=вес, G=габариты, H=ФИО_отпр, I=тел_отпр, J=город_откуда, K=адрес_откуда, L=ФИО_получ, M=тел_получ, N=город_куда, O=адрес_куда, P=ориентир

**Статусы заказа:** NEW → READY_FOR_DRIVERS → TAKEN → LOADING → IN_TRANSIT → ARRIVED → DELIVERED | CANCELLED

---

## 3. Что сделано в этой сессии

### Фикс: авторефреш панели менеджера после WebApp-действий
- Проблема: после `sendData` WebApp закрывается, но кнопка «🎛 Открыть панель» хранила старый URL → курьер показывался занятым даже после отмены заказа
- Решение: `await _send_panel(message.chat.id, bot)` в конце каждого action-хендлера в `handle_webapp`
- Затронутые actions: `change_status`, `cancel_active`, `set_ready`, `reassign_confirm`
- Файл: `manager_bot.py`

### Рефакторинг: удаление мёртвого кода
- `driver_bot.py:422` — удалён мёртвый `return result` после `return {…}`
- `manager_bot.py` — удалён `import traceback`
- `manager_bot.py` — удалён дублирующий `from driver_bot import _pad_row` внутри `_sync_cancel_order`
- `manager_bot.py` — `_build_panel_message` — убраны dead InlineKeyboardMarkup и `_`-распаковка
- `manager_bot.py` — удалены `admin_refresh` callback, `do_reassign` (rt:), `reassign_request` action
- `client_bot.py` — `LINK_TO_OFFER = "https://www.google.com"` → `os.getenv("LINK_TO_OFFER", "")`
- `client_bot.py` — удалён неверный комментарий «ФИО из столбца M (индекс 12)»

### UI: унификация дизайн-системы (все три WebApp)
**admin_panel.html:**
- `--r: 12px` → `--radius: 14px / --radius-sm: 10px`
- `--primary-g` → `--primary-grad`
- Добавлены `--primary-light`, `--success-grad`
- Кнопка «⚙️ Управление» — была серая, стала синяя с обводкой (primary style)
- Модалка — верхний радиус `16px` → `20px`
- `btn-report-all` — зелёный градиент вместо плоского
- `btn-report` — тонкая синяя обводка
- Добавлен `DELIVERED` в STATUS_LABEL и CSS (.sp-DELIVERED)
- Тени статистики: `0 2px 8px` → `0 4px 16px` (как в driver_cabinet)

**client.html:**
- Добавлены `--success-grad`, `--warning` в :root
- Добавлена `.brand-bar` — 3px градиентная полоска наверху sticky header
- `btn-success` теперь через `var(--success-grad)`

**driver_cabinet.html:**
- Выравнивание форматирования :root (идентично другим файлам)

---

## 4. Текущее состояние

**Работает:**
- Все три бота запускаются одним процессом локально
- Клиентский бот: заказы, профиль, поддержка, обратная связь
- Курьерский бот: регистрация с телефоном, полный цикл статусов, кабинет+отчёт
- Менеджерский бот: /start → панель, авторефреш после каждого действия, push о новых заказах

**GitHub Pages (запушено):**
- `admin_panel.html`, `driver_cabinet.html`, `client.html` — актуальны (коммит `cb8646c`)

**Требует проверки (не тестировалось):**
- Авторефреш панели после отмены/смены статуса/переназначения
- Модалка «⚙️ Управление» — кнопка теперь синяя, работает ли визуально

---

## 5. Что осталось

- [ ] Протестировать весь флоу нового заказа (клиент → push менеджеру → принять/отклонить)
- [ ] Протестировать авторефреш: после cancel_active курьер должен стать «Свободен»
- [ ] Протестировать регистрацию нового курьера (шаг телефона)
- [ ] Добавить реальную ссылку на оферту в `.env` (LINK_TO_OFFER=...)
- [ ] Хостинг (Railway не вышло — Cyrillic в env var обрезается xargs'ом)

---

## 6. Важные детали

**.env (актуальный):**
```
TELEGRAM_BOT_TOKEN=8911160775:AAFpuaYqxNFqe-w8f_6HxZ95Gn6i7dd5OYE
DRIVER_BOT_TOKEN=8634346674:AAFW5TBonzvr49tw2zxjMwMOEZqiFbRMCPo
MANAGER_BOT_TOKEN=8853197840:AAFzkGJY3YA9P-oHDKMHS-_fhzAaX8EWIK0
GOOGLE_CREDS_PATH=creds.json
GOOGLE_SHEET_NAME=Заявки Mavsimi Rason
WEB_APP_URL=https://gostwarrir186.github.io/mavsim-form/web/client.html?v=20
DRIVER_WEBAPP_URL=https://gostwarrir186.github.io/mavsim-form/web/driver_cabinet.html
REPORT_PICKER_URL=https://gostwarrir186.github.io/mavsim-form/web/report_picker.html
ADMIN_PANEL_URL=https://gostwarrir186.github.io/mavsim-form/web/admin_panel.html
SUPPORT_CHAT_ID=-1003913005014
MANAGER_CHAT_ID=972542297
DEFAULT_DRIVER_RATE=15.0
LINK_TO_OFFER=          ← нужно заполнить реальной ссылкой на оферту
```

**Критические нюансы:**
- Все Sheets-операции → `asyncio.to_thread(...)` (блокирующие вызовы)
- Callback-хендлеры: `await callback.answer()` первой строкой
- `sendData()` работает ТОЛЬКО из reply keyboard web_app кнопки, НЕ из inline keyboard
- `sendData()` всегда закрывает WebApp — live-обновление без закрытия требует backend API (не реализовано)
- `build_driver_main_menu(driver_id)` — async, везде `await`
- `_order_take_lock` (threading.Lock) в driver_bot — не убирать, защита от race condition
- manager_bot импортирует из driver_bot → порядок в main.py: сначала driver_bot, потом manager_bot
- `_sync_change_order_status` возвращает `(success, client_chat_id, courier_id, err)` — 4 значения
- `_sync_set_order_ready` и `_sync_cancel_order` возвращают `(success, client_chat_id, err)` — 3 значения
- file_id фото переносится между ботами через `mgr_bot.send_photo(photo=file_id)` — работает
- Railway не вышло: Cyrillic в GOOGLE_SHEET_NAME обрезается xargs'ом при `railway up`
- Клон репо GitHub Pages: `/private/tmp/mavsim-form/` (копировать файлы туда → git push)
- `_build_panel_message` возвращает `(text, webapp_url)` — 2 значения (inline keyboard удалён)

**Зависимости:** aiogram, gspread, google-auth, python-dotenv, openpyxl
