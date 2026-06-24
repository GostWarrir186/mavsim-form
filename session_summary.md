# Выжимка сессии — 2026-06-24

## 1. Проект

**Mavsimi Rason** — система доставки (Таджикистан, валюта TJS).  
Репозиторий бота: `/Users/azam/Desktop/mavsim-delivery-bot`  
Репозиторий GitHub Pages (HTML): `git@github.com:GostWarrir186/mavsim-form.git`  
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
- **Заказы** (новый): A=ID, B=дата, C=статус, D=цена, E=тип, F=вес, G=габариты, H=ФИО_отпр, I=тел_отпр, J=город_откуда, K=адрес_откуда, L=ФИО_получ, M=тел_получ, N=город_куда, O=адрес_куда, P=ориентир

**Статусы заказа:** NEW → READY_FOR_DRIVERS → TAKEN → LOADING → IN_TRANSIT → ARRIVED → DELIVERED | CANCELLED

---

## 3. Что сделано в этой сессии

### Фикс: WEB_APP_URL клиентского бота
- Был: `web/?v=20` → 404 (нет index.html)
- Стал: `web/client.html?v=20` (прямая ссылка на файл)
- Файл: `.env`

### Менеджерский бот — полный отказ от slash-команд
- `/start` теперь сразу вызывает `_send_panel()` — открывает панель с кнопками
- Удалены: `/panel`, `/reassign`, `/ready`
- Удалён `Command` импорт (остался только `CommandStart`)
- Файл: `manager_bot.py`

### Панель менеджера — NEW-заказы
- Вкладка «Биржа» переименована в «📦 Заказы»
- Добавлена секция «⏳ Ожидают подтверждения» для NEW-заказов с кнопкой «✅ Принять в доставку»
- `sendData({action: 'set_ready', order_id, order_row})` → handler в `manager_bot.py`
- Счётчик «Новых» (красный) в статистике вместо «Свободных»
- `_sync_get_orders_for_dashboard` теперь возвращает `(active, free, new)` вместо `(active, free)`
- `_async_get_admin_dashboard_data` добавляет `new` в возвращаемый dict
- Файлы: `driver_bot.py`, `manager_bot.py`, `admin_panel.html`

### Push-уведомления менеджеру о новых заказах
- Когда клиент оформляет заказ → менеджеру сразу приходит сообщение с деталями
- Две кнопки: **✅ Принять** (`oa:<order_id>`) и **❌ Отменить** (`oc:<order_id>`)
- При отмене: inline-клавиатура с причинами (4 готовых + «✏️ Своя причина» → FSM)
- `CANCEL_REASONS` = ["Не смогли согласовать детали", "Адрес недоступен", "Нет курьеров в этой зоне", "Дублирующий заказ"]
- FSM: `ManagerCancelOrder.waiting_for_reason`
- Клиент получает уведомление с причиной при отмене
- Новые функции: `_sync_cancel_order`, `order_accept`, `order_cancel_menu`, `order_cancel_reason`, `order_cancel_custom_start`, `order_cancel_custom_reason`
- Файлы: `client_bot.py`, `manager_bot.py`

### Регистрация курьера — телефон (столбец H в Водители)
- Добавлен шаг `DriverRegistration.waiting_for_phone`
- После ФИО бот просит телефон (кнопка «📱 Поделиться номером» или ввод вручную)
- `_sync_register_driver(chat_id, fio, phone)` сохраняет телефон в колонку H
- Менеджер видит телефон в уведомлении о новом курьере
- Файл: `driver_bot.py`

### Новый лист «Заказы» в Google Sheets
- Создаётся автоматически при запуске бота если не существует
- 16 столбцов: чистая информация о заказе (без технических полей)
- Запись происходит в `client_bot.py` → `handle_webapp_data` параллельно с Лист1
- Файлы: `config.py`, `client_bot.py`

### Модалка управления активными заказами (admin_panel.html)
- Кнопка «↔️ Переназначить» заменена на «⚙️ Управление» на каждой карточке
- Тап открывает bottom-sheet с тремя секциями:
  1. **Сменить статус** — pills: Взят / Погрузка / В пути / На месте / Доставлен (прошлые greyed, текущий синий, будущие кликабельны)
  2. **Переназначить курьера** — список с 🟢/🔴 (перенесён из старой reassign-модалки)
  3. **Отменить заказ** — причины + «✏️ Своя причина» (появляется текстовое поле)
- `sendData({action: 'change_status', ...})` → уведомление курьеру + клиенту
- `sendData({action: 'cancel_active', reason, ...})` → CANCELLED + уведомления обоим
- Новая функция: `_sync_change_order_status(order_id, new_status)` → `(success, client_chat_id, courier_id, err)`
- Старый `#reassignModal` удалён, весь его функционал внутри `#manageModal`
- Файлы: `admin_panel.html`, `manager_bot.py`

### Карта файлов в CLAUDE.md обновлена
- Актуальные номера строк для всех трёх ботов

---

## 4. Текущее состояние

**Работает:**
- Все три бота запускаются одним процессом локально
- Клиентский бот: заказы (URL теперь правильный `client.html`), профиль, поддержка, обратная связь
- Курьерский бот: регистрация с телефоном, полный цикл статусов, кабинет+отчёт, отказ с причиной/фото
- Менеджерский бот: /start → сразу панель, push о новых заказах (✅/❌), модалка управления заказами

**Запушено на GitHub Pages:**
- `admin_panel.html` — актуальная версия с модалкой управления (коммит `bb599b3`)
- `driver_cabinet.html` и `client.html` — актуальны

**Требует проверки (не тестировалось):**
- WEB_APP_URL — `client.html?v=20` (должно открываться теперь)
- Push-уведомления менеджеру о новом заказе
- Кнопки ✅/❌ и причины отмены NEW-заказа
- Регистрация курьера с шагом телефона
- Лист «Заказы» создаётся при первом запуске
- Модалка «⚙️ Управление» — смена статуса, переназначение, отмена

---

## 5. Что осталось

- [ ] Протестировать весь флоу нового заказа (клиент → push менеджеру → принять/отклонить)
- [ ] Протестировать модалку управления активными заказами
- [ ] Протестировать регистрацию нового курьера (шаг телефона)
- [ ] Добавить заголовки в Google Sheets: Водители G1="Topic ID", H1="Телефон"; Клиенты G1="Topic ID"
- [ ] Рассмотреть хостинг (Railway не вышло — Cyrillic в env var обрезается xargs'ом)

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
```

**Критические нюансы:**
- Все Sheets-операции → `asyncio.to_thread(...)` (блокирующие вызовы)
- Callback-хендлеры: `await callback.answer()` первой строкой
- `sendData()` работает ТОЛЬКО из reply keyboard web_app кнопки, НЕ из inline keyboard
- `build_driver_main_menu(driver_id)` — async, везде `await`
- `_order_take_lock` (threading.Lock) в driver_bot — не убирать, защита от race condition
- manager_bot импортирует из driver_bot → порядок в main.py: сначала driver_bot, потом manager_bot
- `_sync_change_order_status` возвращает `(success, client_chat_id, courier_id, err)` — 4 значения
- `_sync_set_order_ready` и `_sync_cancel_order` возвращают `(success, client_chat_id, err)` — 3 значения
- file_id фото переносится между ботами через `mgr_bot.send_photo(photo=file_id)` — работает
- Railway не вышло: Cyrillic в GOOGLE_SHEET_NAME обрезается xargs'ом при `railway up`
- Клон репо GitHub Pages лежит в scratchpad: `/private/tmp/.../scratchpad/mavsim-form/`

**Зависимости:** aiogram, gspread, google-auth, python-dotenv, openpyxl
