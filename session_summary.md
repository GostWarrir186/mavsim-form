# Выжимка сессии — 2026-07-27 (аналитика: база-зеркало + дашборд)

## 1. Проект

**Mavsimi Rason** — система доставки (Таджикистан, валюта TJS, сомони).
Репозиторий: `/Users/azam/Documents/mavsim-delivery-bot`
(`git@github.com:GostWarrir186/mavsim-form.git`)
GitHub Pages (WebApp): `https://gostwarrir186.github.io/mavsim-form/web/`
Стек: Python 3.11+, aiogram 3.x, gspread + Google Sheets как источник истины,
**SQLite как база-зеркало для аналитики**, GitHub Pages (HTML/JS без
фреймворков), Docker на VPS. Три бота — один процесс через `asyncio.gather`
в `main.py`.

---

## 2. Структура проекта

```
mavsim-delivery-bot/
├── main.py                  # запуск трёх ботов + db.run_sync_loop (снапшот каждые 10 мин)
├── config.py                # токены, gspread, листы, sanitize_for_sheet, md_escape
├── client_bot.py            # клиентский бот
├── driver_bot.py            # курьерский бот (send_client_push, declutter, автопуш)
├── manager_bot.py           # менеджерский бот (+ кнопка Статистика, экспорт бухгалтерии)
├── db.py                    # ★ НОВЫЙ: SQLite-зеркало, статистика по периодам, Excel бухгалтерии
├── Dockerfile               # COPY включает db.py
├── requirements.txt
├── CLAUDE.md                # правила + карта файлов (обновлена: db.py, дашборд)
├── web/{client,driver_cabinet,report_picker,admin_panel,dashboard}.html
└── graphify-out/            # knowledge graph кодовой базы
```
Плюс `~/Desktop/docker-compose.yml` (деплой-конфиг на уровне `~/automation/`):
у сервиса `mavsim-bots` добавлен volume `./mavsim-delivery-bot/data:/data`
и `SQLITE_DB_PATH=/data/mavsim.db`.

**Google Sheets «Заявки Mavsimi Rason» (4 листа):** Лист1 (заказы, гл. источник),
Клиенты, Водители, Заказы (чистый лог). Схема колонок — см. CLAUDE.md.
**Статусы заказа (3):** `TAKEN`→`IN_TRANSIT`→`DELIVERED` (+ NEW,
READY_FOR_DRIVERS, CANCELLED). **Курьер:** PENDING→ACTIVE|REJECTED.

---

## 3. Что сделано в этой сессии (задеплоено и проверено в проде)

### База-зеркало SQLite (`db.py`, новый)
- Снапшот "Лист1"+"Водители" → SQLite (`orders`/`drivers`), полная перезапись
  (mirror без drift). Не трогает запись ботов — ноль риска.
- `main.py`: `db.run_sync_loop(interval_sec=600)` в `asyncio.gather` — снапшот
  при старте и каждые 10 мин, обёрнут в try/except.
- Volume `/data/mavsim.db` — база переживает пересборку контейнера.
- CLI: `python db.py --sync|--stats` (на сервере — `docker exec ...`).

### Дашборд статистики (`web/dashboard.html`, новый)
- ОТДЕЛЬНАЯ кнопка "📊 Статистика" у менеджера (не связана с "🎛 Панель").
  `manager_bot._build_dashboard_url()` собирает `db.dashboard_payload()` в `?d=`.
- Периоды **неделя/месяц/полгода/год** — все данные зашиты в URL, переключение
  на клиенте без обращения к серверу.
- Премиальный адаптивный дизайн (фирменный оранжевый, светлая/тёмная тема
  Telegram): hero-выручка, KPI (3 кол моб / 5 широк), график с тумблером
  Заказы/Выручка, donut статусов, топ курьеров. Проверено скриншотами
  (моб 390 / десктоп 900).

### Экспорт для бухгалтерии
- Кнопка в дашборде → `Telegram.WebApp.sendData` → `manager_bot.handle_webapp`
  ветка `export_accounting` → `db.build_accounting_excel(period)` присылает
  Excel-реестр доставленных заказов (дата, ID, маршрут, тип, курьер, сумма +
  ИТОГО).

### Попутно
- Починен краш-цикл на сервере: старый `config.py` без `md_escape` +
  новый `manager_bot.py` → ImportError → рестарты → 429. Вылечено заливкой
  всего согласованного набора `.py`. Урок записан в CLAUDE.md и в память.
- `DASHBOARD_URL` добавлен в `.env` на сервере.

---

## 4. Текущее состояние

**Работает в проде (проверено):** три бота, автопуш, двуязычие ru/tj,
3 статуса, база-зеркало наполняется, дашборд со всеми периодами, экспорт
бухгалтерии присылает Excel.

**Замечание:** обрезка справа была только в headless-скриншоте (артефакт
рендера), на реальном устройстве вёрстка корректна — пользователь подтвердил.

---

## 5. Что осталось (обсуждалось, не начато)

1. **Этап 2 — горячие чтения на базу.** Перевести биржу заказов и дашборды
   курьера/менеджера с `get_all_values()` Google на чтение из SQLite-зеркала,
   чтобы снять лимиты 429 при росте объёма. Сейчас не срочно.
2. **Расширение Excel бухгалтерии** — при запросе добавить телефоны, ставку
   курьера, заработок, отдельный лист-разбивку по курьерам.
3. **«Живое» обновление WebApp без кнопки** — нужен backend API (FastAPI) +
   fetch. Ранее пробовали (07-21), откатили к base64+sendData.
4. **Прочие идеи UX** (не начаты): оценка курьера после доставки, гео-локация
   курьера, авто-обновление панели менеджера, SLA-алерты.

---

## 6. Важные детали

**Деплой:**
- `.py` (вкл. `db.py`) — Cyberduck (SFTP) в `~/automation/mavsim-delivery-bot/`,
  затем `cd ~/automation && docker compose build mavsim-bots && docker compose up -d mavsim-bots`.
  Git на `.py` НЕ влияет. Новый .py → добавить в `COPY` в Dockerfile.
- `web/*.html` (вкл. `dashboard.html`) — GitHub Pages, `git push` в main.
- `.env` на сервере: `DASHBOARD_URL`, `ADMIN_PANEL_URL`, `WEB_APP_URL(?v=)`,
  токены. После правки `.env` → `docker compose up -d mavsim-bots`.
- Диагностика: `docker logs automation-mavsim-bots --tail 100` (без grep).
  При краше — смотреть ПЕРВУЮ строку трейсбека, не 429 внизу.

**Критические нюансы:**
- db.py не трогает запись в Sheets — источник истины остаётся "Лист1".
  Снапшот — полная перезапись, статусы англ. кодами, даты `%d.%m.%Y %H:%M` UTC+5.
- Payload дашборда ограничен по размеру независимо от числа заказов (всё
  агрегировано): ~5-8 КБ base64 в `?d=`.
- Все Sheets/SQLite-операции → `asyncio.to_thread(...)`; callback →
  `await callback.answer()` первой строкой.
- `sendData()` только из reply keyboard web_app кнопки; всегда закрывает WebApp.
- WebApp-кнопка со старым URL не обновляется сама — данные подтянутся только
  при пересылке клавиатуры заново (после /start / "🔄 Обновить").
- main.py: сначала driver_bot, потом manager_bot (manager импортит из driver).
- Секреты — в `.env` (не в git).

**Зависимости:** aiogram, gspread, google-auth, python-dotenv, openpyxl,
sqlite3 (stdlib — ставить ничего не надо).
