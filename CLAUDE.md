<!-- rtk-instructions v2 -->
# Правила работы с кодом

Before editing any file, read it first. Before modifying a function, grep for all callers. Research before you edit.

## Правила начала сессии

Если в корне проекта есть session_summary.md — прочитай его первым делом и восстанови контекст перед любой другой работой.

## Правила завершения сессии

Когда пользователь пишет "сохрани сессию" или "выжимка" — сохрани файл session_summary.md в корень проекта по шаблону:

1. Проект — название, технологии, стек
2. Структура проекта — файлы, папки, архитектура
3. Что сделано — реализованные фичи, изменения (с именами файлов/функций)
4. Текущее состояние — что работает, что сломано
5. Что осталось — задачи, планы, идеи которые обсуждали
6. Важные детали — переменные окружения, зависимости, нюансы

После сохранения напомнить закрыть терминал и открыть новый.

# Карта файлов (используй для точечного чтения)

Строки указаны примерно — сверяй grep-ом, файлы часто меняются.

## Локализация (клиентский и курьерский боты, 2026-07)
Оба бота полностью переведены с бардака "тадж \n рус в одном сообщении" на
нормальные словари `L` (driver_bot.py) / `CL` (client_bot.py) вида
`{"ru": {...}, "tj": {...}}`, плюс словари для лейблов кнопок (`JOBS_BTN`,
`AUTH_BTN`, `BACK_BTN` и т.д. — тоже per-lang). Никакого общего `pick_lang`
с текстовым разделителем в driver_bot.py/client_bot.py больше нет (это
работало только пока в тексте был "───" разделитель — убрали разделители,
и pick_lang сломался бы для этих текстов, поэтому оба бота отказались от
него в пользу словарей). `pick_lang`/`LANG_SEPARATOR` в config.py остались
только как legacy — сейчас нигде не используются, можно смело выпилить,
если ничего не всплывёт.

Выбор языка — на самом первом `/start` для нового пользователя (инлайн-кнопки
RU/TJ), сохраняется в таблицу сразу при регистрации. Для уже
зарегистрированных без сохранённого языка — дефолт "ru" (не "both").
Курьер меняет язык позже через кабинет (веб), клиент — так же.

## Схлопывание сообщений (declutter, 2026-07)
Реализовано в обоих ботах через паттерн "трекаем message_id последнего
сообщения по шагу, при следующем шаге — удаляем старое, шлём новое":
- `driver_bot.py`: `_status_msgs` + `_replace_status_message()` /
  `_clear_status_message()` — вся цепочка регистрации (выбор языка → оферта
  → ФИО → телефон → "заявка отправлена" → одобрение/отклонение менеджером)
  схлопывается в одно сообщение. `_try_delete()` дополнительно стирает
  СОБСТВЕННЫЕ сообщения пользователя на каждом шаге (боты могут удалять
  входящие сообщения в приватных чатах — Telegram Bot API это разрешает).
- То же самое: `_last_jobs_msgs` / `_job_message_refs` /
  `_clear_previous_jobs_view()` / `_clear_stale_job_cards()` — биржа заказов:
  повторное нажатие "Свободные заказы" не плодит новые сообщения, а как
  заказ забирают — у ВСЕХ остальных курьеров, кому разослали карточку,
  она превращается в "❌ уже забрал другой" вместо мёртвой кнопки.
- `client_bot.py`: то же самое (`_status_msgs`/`_replace_status_message`/
  `_try_delete`) для цепочки регистрации клиента.
Если добавляешь новый шаг в любую из этих цепочек — не забудь продолжить
паттерн, иначе чат снова начнёт "захламляться".

## Статусы заказа (3 штуки, с 2026-07)
Раньше было 5 (TAKEN → LOADING → IN_TRANSIT → ARRIVED → DELIVERED), теперь
**3**: `TAKEN` ("Принял") → `IN_TRANSIT` ("В пути") → `DELIVERED`
("Доставил"). LOADING и ARRIVED убраны полностью — ни в коде, ни в
admin_panel.html/driver_cabinet.html их быть не должно. Если где-то
всплывают — это забытые следы старой модели, надо чистить.
Плюс `NEW`, `READY_FOR_DRIVERS`, `CANCELLED` — статусы до/вне активной
доставки, не менялись.

## Дубли статуса в разных местах — не забывай синхронизировать все три
Статус заказа хранится в ТРЁХ местах, и их надо обновлять вместе:
1. **"Лист1"** (`sheet` в config.py) — главный источник истины, столбец A.
2. **"Заказы"** (`orders_info_sheet`) — чистый лог для отчётов, столбец C.
   Раньше писался только один раз при создании (**статус NEW навсегда**,
   это было багом) — теперь `config.sync_update_order_info_status(order_id,
   status)` вызывается из всех мест в driver_bot.py/manager_bot.py, которые
   меняют статус в "Лист1". Если добавляешь новую точку смены статуса —
   не забудь вызвать и эту функцию тоже.
3. **"История" клиента в client.html** (`localStorage['user_orders']`) —
   локальный кэш в браузере клиента, создаётся сразу при оформлении заказа
   (для мгновенной обратной связи). Раньше застревал на статусе NEW навсегда
   (не было живой синхронизации). Теперь: `client_bot.get_main_menu()`
   при каждом открытии меню подтягивает актуальные статусы через
   `_sync_get_client_order_statuses()` и зашивает их в URL WebApp-кнопки
   (`&d=<base64 json>`); `client.html` при загрузке накатывает эти статусы
   поверх localStorage по совпадению ID. **Важно**: WebApp-кнопка со
   старым URL не обновляется сама по себе — статус подтянется только когда
   клавиатура будет переслана заново (после /start, смены профиля и т.п.),
   не мгновенно.

## ID заказа — генерируется клиентом, не сервером
Формат `Z-ддмм-XXXX` (4 симв. A-Z0-9). И `client.html` (JS,
`sendDataToBot()`), и `client_bot.py` (`generate_order_id()`) умеют его
генерировать — **но авторитетный источник теперь клиент**: JS создаёт ID и
кладёt его в `data.order_id`, `client_bot.py` использует его (после
валидации по `ORDER_ID_RE`), генерируя свой только как fallback если
клиентский невалиден/отсутствует. Так ID в локальной "Истории" клиента
всегда совпадает с реальным ID в таблице. Раньше клиент генерировал ID в
СТАРОМ формате (`ORD-ддмм-XXXXXX`, 6 симв. base36) независимо от сервера —
они никогда не совпадали, это был баг.

## База-зеркало SQLite + дашборд статистики (db.py, 2026-07)
Отдельный слой аналитики поверх Google Таблиц — **не трогает запись ботов**,
источник истины остаётся "Лист1".
- **`db.py`** — SQLite-зеркало (`SQLITE_DB_PATH`, по умолч. `mavsim.db`;
  на сервере — volume `/data/mavsim.db`, чтобы переживал пересборку).
  `snapshot_from_sheets()` читает "Лист1"+"Водители" целиком и ПОЛНОСТЬЮ
  перезаписывает таблицы `orders`/`drivers` (mirror, без drift). Статусы
  хранятся англ. кодами; даты парсятся из `%d.%m.%Y %H:%M` (UTC+5, тот же
  `DUSHANBE_TZ`, что в driver_bot). Цена — строкой в таблице, парсится в число.
- **`main.py`** — `db.run_sync_loop(interval_sec=600)` добавлен в `asyncio.gather`:
  снапшот при старте и каждые 10 мин. Падение снапшота обёрнуто в try/except —
  ботов не роняет.
- **Дашборд** — кнопка "📊 Статистика" у менеджера (ОТДЕЛЬНАЯ от "🎛 Открыть
  панель"), страница `web/dashboard.html`. Данные по ВСЕМ периодам (неделя/
  месяц/полгода/год) собирает `db.dashboard_payload()`, бот кодирует в `?d=`
  base64 (как admin_panel), страница переключает периоды на клиенте без
  обращения к серверу. Снимок статистики — на момент отправки клавиатуры
  (обновляется по "🔄 Обновить"/`/start`).
- **Экспорт для бухгалтерии** — кнопка в дашборде → `sendData` →
  `manager_bot.handle_webapp` ветка `export_accounting` →
  `db.build_accounting_excel(period)` присылает Excel-реестр доставленных
  заказов за период (дата, ID, маршрут, тип, курьер, сумма + ИТОГО).
- CLI для проверки: `python db.py --sync` (снапшот+статистика),
  `python db.py --stats` (только статистика). На сервере:
  `docker exec automation-mavsim-bots python db.py --stats`.
- **Урок деплоя**: если залить бот, который импортит новый символ из config.py,
  а config.py на сервере старый — `ImportError` роняет ВЕСЬ процесс, `restart:
  always` крутит краш-цикл, а каждый рестарт читает Таблицу на старте →
  APIError 429. Диагноз — по ПЕРВОЙ строке трейсбека, не по 429 внизу.
  Лечить: залить ВЕСЬ согласованный набор .py, не по одному файлу.

## Callback-данные заказа — ТОЛЬКО по ID, не по номеру строки (2026-08)
`take:`, `transit:`, `done:`, `reject:` несут **ID заказа** (`Z-ддмм-XXXX`), а не
номер строки. Раньше был номер строки — и это был баг: строки в "Лист1"
сдвигаются (менеджер удаляет/вставляет), а кнопки в чатах живут неделями, так
что курьер мог забрать СОВСЕМ ДРУГОЙ заказ. `_sync_take_order`,
`_sync_release_order`, `_sync_update_status`, `_sync_reassign_order` принимают
`order_id` и сами ищут строку через `_sync_find_order_by_id` **внутри**
`_order_take_lock`. Если добавляешь новое действие над заказом — тот же паттерн,
номер строки наружу не отдавать. `order_row` из admin_panel.html игнорируется.

## Карточка заказа — только через `_render_job_card()` (2026-08)
Карточки уходят с `parse_mode="Markdown"`, поэтому ВСЕ пользовательские поля
(адрес, комментарий, ФИО) обязаны идти через `md_escape`. Один `_` в адресе
раньше валил отправку, и заказ молча не доезжал ни до одного курьера. Сборка
карточки теперь в одном месте — `driver_bot._render_job_card()`, используется и
биржей, и авторассылкой. Руками `L[lang]["job_card"].format(...)` не звать.

## Единая тема WebApp — `web/theme.css` (2026-08)
Все 5 страниц подключают `<link rel="stylesheet" href="theme.css?v=1">` ПЕРЕД
своим `<style>`; локальных `:root` в страницах больше нет. Токены: новые
(`--ink`/`--surface`/`--accent`/`--page-bg`) + легаси-алиасы (`--text`/`--bg`/
`--primary`/`--sec-bg`), чтобы старые правила работали без переписывания.
Тема — `prefers-color-scheme` + атрибут `data-theme` на `<html>` (его ставит
скрипт страницы по `Telegram.WebApp.colorScheme`; `data-theme` важнее системной).
Фирменный акцент `#EA580C`. Excel-отчёты (`generate_excel_report`,
`generate_summary_excel`, `build_accounting_excel`) — тот же оранжевый `FFEA580C`.
**Новую страницу заводишь — подключи theme.css и не заводи свой `:root`.**
При правке theme.css подними `?v=` во всех страницах (кэш GitHub Pages).

## Деплой
- **`.py`-файлы** (`client_bot.py`, `driver_bot.py`, `manager_bot.py`,
  `config.py`, `main.py`, `db.py`) — с 2026-08-17 деплой **через git**:
  `~/automation/mavsim-delivery-bot/` на VPS — это git-клон `origin/main`
  (публичный репозиторий, HTTPS, ключи не нужны). Порядок: закоммитить и
  запушить в `main`, затем на сервере `cd ~/automation/mavsim-delivery-bot &&
  ./deploy.sh` (скрипт делает `git pull --ff-only` → `docker compose build` →
  `up -d` → показывает логи, и отказывается работать при незакоммиченных
  правках на сервере). Ручной Cyberduck (SFTP) больше не нужен и вреден —
  именно частичная заливка одного файла давала `ImportError` → краш-цикл →
  429. Через git набор файлов приезжает согласованным.
  **Не в git и только руками через Cyberduck: `.env` и `creds.json`** —
  репозиторий публичный. Также untracked на сервере: `data/` (volume) и
  legacy `web_api.py`. `.dockerignore` держит `.git/` и `data/` вне контекста
  сборки. **Новый файл — не забудь добавить в `COPY` в Dockerfile**
  (db.py там уже есть). `docker-compose.yml` (на рабочем столе / в `~/automation/`):
  сервис `mavsim-bots` монтирует volume `./mavsim-delivery-bot/data:/data` и
  задаёт `SQLITE_DB_PATH=/data/mavsim.db`. Env-переменные (`DASHBOARD_URL`,
  `ADMIN_PANEL_URL` и т.д.) — в `.env`; после правки `.env` нужен
  `docker compose up -d mavsim-bots` (перечитать).
- **`web/*.html`** (`client.html`, `driver_cabinet.html`, `admin_panel.html`,
  `dashboard.html`)
  — хостятся на **GitHub Pages**, деплой = `git push` в `main`. VPS тут ни
  при чём. После пуша GitHub Pages пересобирается не мгновенно (обычно
  до пары минут) — если после пуша "ничего не поменялось", сначала
  проверь `git ls-remote origin main` (что коммит реально долетел), потом
  подожди — скорее всего кэш GitHub Pages/Telegram WebView, а не баг.
  Кэш-бастинг: `WEB_APP_URL` в `.env` имеет `?v=19` — если правки в
  html не подхватываются вообще, можно попробовать поднять версию.
- Диагностика прод-бага без доступа к серверу: попроси
  `docker logs automation-mavsim-bots --tail 200` (без `grep` — кириллица
  в `-i паттерн` иногда ломает вызов через SSH-копипаст) и/или скрин
  нужной строки прямо из Google Sheets — обычно быстрее находит причину,
  чем гадать по коду.

## client_bot.py — клиентский бот
| Строка | Что |
|--------|-----|
| 21 | FSM: Registration (waiting_for_lang, waiting_for_fio), Support |
| 34+ | Словари кнопок: AUTH_BTN, BACK_BTN, ACCEPT_OFFER_BTN, ORDER_BTN, SUPPORT_BTN |
| 40 | CL — словарь локализации {"ru": {...}, "tj": {...}} |
| 133 | ORDER_ID_RE — валидация клиентского order_id |
| 135 | generate_order_id (fallback, если клиент не прислал валидный) |
| 141 | sanitize_for_sheet |
| 156 | validate_order_data |
| 175 | _sync_check_user_by_phone |
| 186 | _sync_check_user_by_chat_id |
| 197 | _sync_get_client_order_statuses (для живого статуса в WebApp) |
| 214 | _sync_update_profile |
| 235 | _lang_from_row (дефолт "ru", не "both") |
| 245 | _sync_register_client (пишет и язык) |
| 256 | _sync_get_support_topic |
| 270 | _sync_save_support_topic |
| 281 | _sync_get_client_by_topic |
| 302 | _try_delete (удаляет входящее сообщение пользователя) |
| 309 | _replace_status_message (схлопывание шагов) |
| 322 | cmd_start (лайт-регистрация: если уже есть по chat_id → сразу меню, иначе — выбор языка) |
| 347 | set_client_lang (callback выбора языка) |
| 369 | go_main_menu (глобальная кнопка "Главное меню") |
| 380 | process_contact (авторизация по телефону) |
| 413 | start_fio_step |
| 426 | save_fio (регистрация завершена сразу, без одобрения менеджера) |
| 447 | get_main_menu (async! тянет статусы заказов и зашивает в URL WebApp) |
| 473 | handle_webapp_data (заказы + профиль + push менеджерам о новом заказе) |
| 647 | support_start |
| 664 | support_send (создаёт/ищет топик) |
| 711 | support_continue (чат в режиме поддержки) |
| 733 | support_group_message (ответы менеджера → клиенту) |

## driver_bot.py — курьерский бот
| Строка | Что |
|--------|-----|
| 47+ | _job_message_refs, _last_jobs_msgs, _clear_previous_jobs_view, _status_msgs, _replace_status_message, _clear_status_message, _try_delete, _clear_stale_job_cards — вся механика declutter |
| 119+ | Словари кнопок: JOBS_BTN, CABINET_BTN, SUPPORT_BTN, BACK_BTN, ACCEPT_OFFER_BTN, SHARE_PHONE_BTN, SKIP_BTN, TAKE_JOB_BTN, TRANSIT_BTN, REJECT_BTN, DELIVERED_BTN |
| 131 | L — словарь локализации |
| 297 | FSM: DriverRegistration (waiting_for_lang, waiting_for_fio, waiting_for_phone), DriverRejectReason, DriverSupport |
| 311 | _get_active_driver |
| 408 | _sync_register_driver (принимает lang, пишет сразу в PENDING) |
| 494 | _lang_from_driver_row (дефолт "ru", не "both") |
| 501 | _sync_get_all_active_drivers (теперь включает lang каждого — нужно для рассылки на своём языке) |
| 554 | _sync_get_free_orders |
| 584 | _sync_take_order |
| 603 | _sync_release_order |
| 625 | _sync_update_status |
| 649 | _sync_reassign_order (разрешено только для TAKEN/IN_TRANSIT, не LOADING/ARRIVED) |
| 673 | _sync_get_orders_for_dashboard → (active, free, new); active = TAKEN/IN_TRANSIT |
| 723 | _sync_get_drivers_for_dashboard |
| 740 | _async_get_admin_dashboard_data → {orders, free, new, couriers} |
| 752 | generate_excel_report (show_earnings=False скрывает ставку/заработок — для отчёта, который скачивает сам курьер; True — для отчёта менеджера по конкретному курьеру) |
| 871 | build_driver_main_menu (async) |
| 919 | driver_go_main_menu (глобальная кнопка "Главное меню") |
| 929 | cmd_start_driver (новый → выбор языка; PENDING/ACTIVE/blocked — по сохранённому lang) |
| 961 | set_registration_lang (callback выбора языка) |
| 979 | accept_offer |
| 994 | save_driver_fio |
| 1015 | save_driver_phone (→ уведомляет manager_bot о новом курьере) |
| 1069 | handle_webapp (update_profile + generate_report) |
| 1181 | show_jobs (биржа заказов — схлопывается при повторном нажатии) |
| 1217 | accept_order (take: — TAKEN, чистит мёртвые карточки у других курьеров) |
| 1264 | reject_order (reject: — отказ, FSM причина + фото) |
| 1302 | _do_reject (финализация отказа → READY_FOR_DRIVERS) |
| 1398 | transit_order (transit: — IN_TRANSIT, кнопка сразу "Доставлен", без промежуточного ARRIVED) |
| 1431 | finish_order (done: — DELIVERED) |
| 1463 | driver_support_start |
| 1483 | driver_support_send (создаёт/ищет топик) |
| 1521 | driver_support_continue |
| 1542 | driver_support_group_message (ответы менеджера → курьеру) |
| 1572 | _broadcast_new_free_orders (авто-рассылка новых заказов, на языке каждого курьера) |

## manager_bot.py — менеджерский бот
| Строка | Что |
|--------|-----|
| 61 | FSM: ManagerCancelOrder (своя причина отмены) |
| 69 | _is_manager (async, лист "Менеджеры" + MANAGER_CHAT_ID, кэш 60с) |
| 81 | _sync_approve_driver (PENDING → ACTIVE) |
| 96 | _sync_reject_driver (PENDING → REJECTED) |
| 113 | _sync_set_order_ready (NEW → READY_FOR_DRIVERS, синхронизирует "Заказы") |
| 133 | _sync_change_order_status (генерик смена статуса из ручного переключателя в панели, синхронизирует "Заказы") |
| 150 | _sync_cancel_order (NEW → CANCELLED, синхронизирует "Заказы") |
| 172 | _sync_get_all_couriers_deliveries |
| 203 | _sync_get_all_drivers_rates |
| 228 | generate_summary_excel (сводный Excel на всех курьеров, со ставками — не трогать/не убирать цифры, это для менеджера) |
| 321 | _build_panel_message |
| 341 | cmd_start_manager (/start → сразу открывает панель) |
| 343+ | _build_dashboard_url (async) — собирает db.dashboard_payload() и кодирует в `?d=` для кнопки "📊 Статистика"; None если DASHBOARD_URL пуст |
| 348 | _send_panel (reply keyboard: "🎛 Открыть панель" + отдельная "📊 Статистика" + "🔄 Обновить") |
| 369 | panel_refresh_text (кнопка "🔄 Обновить" — панель НЕ обновляется сама, только по этой кнопке или /start) |
| 380 | order_accept (oa:) |
| 405 | order_cancel_menu (oc:) |
| 419 | order_cancel_reason (ocr:) |
| 444 | order_cancel_custom_start (ocx:) |
| 456 | order_cancel_custom_reason (FSM) |
| 479 | handle_webapp (export_accounting → db.build_accounting_excel / set_ready / reassign / отчёты) |
| 731 | approve_driver (approve_driver: callback ✅ — стирает статус-сообщение у курьера, шлёт одно новое на его языке) |
| 758 | reject_driver_cb (reject_driver: callback ❌ — то же самое) |
| 787 | approve_name_change |
| 812 | reject_name_change |

## config.py
| Строка | Что |
|--------|-----|
| 24 | client_bot, driver_bot, manager_bot |
| 28 | client_dp, driver_dp, manager_dp |
| 37 | gspread init, sheet / drivers_sheet / clients_sheet / managers_sheet / orders_info_sheet |
| 131 | pick_lang / LANG_SEPARATOR — legacy, больше нигде не используется (driver_bot.py и client_bot.py перешли на словари L/CL) |
| 146 | get_manager_chat_ids() — читает лист "Менеджеры" (Telegram ID, ФИО) + MANAGER_CHAT_ID из .env |
| 161 | sync_update_order_info_status(order_id, status) — обновляет статус в листе "Заказы" (столбец C) по ID (столбец A) |
| — | sync_set_delivery_time(order_id, when) — пишет "Дата доставки" в "Лист1" и "Заказы" |

## db.py — база-зеркало SQLite (аналитика/бэкап, не трогает запись ботов)
| Что | Описание |
|-----|----------|
| DB_PATH / _connect / init_db | SQLite (env `SQLITE_DB_PATH`), таблицы `orders`/`drivers`, соединение на вызов (потокобезопасно) |
| snapshot_from_sheets() | читает "Лист1"+"Водители" целиком → полная перезапись таблиц (mirror). config импортится лениво |
| _num / _iso_date | парсеры цены (строка→число) и дат ("%d.%m.%Y %H:%M"→ISO) |
| PERIODS | week/month/half/year → гранулярность day/month, длина |
| _period_range / _overview_range / _series_range / _statuses_range / _couriers_range | статистика по произвольному диапазону |
| dashboard_payload() | статистика по ВСЕМ периодам + now{in_progress, active_couriers} — для `?d=` дашборда |
| build_accounting_excel(period) | Excel-реестр доставленных заказов за период (openpyxl, ленивый импорт) → (BytesIO, имя, подпись) |
| run_sync_loop(interval_sec) | async-цикл снапшота для main.py (try/except, ботов не роняет) |
| `__main__` | CLI: `python db.py --sync` / `--stats` |

# RTK (Rust Token Killer) - Token-Optimized Commands

## Golden Rule

**Always prefix commands with `rtk`**. If RTK has a dedicated filter, it uses it. If not, it passes through unchanged. This means RTK is always safe to use.

**Important**: Even in command chains with `&&`, use `rtk`:
```bash
# ❌ Wrong
git add . && git commit -m "msg" && git push

# ✅ Correct
rtk git add . && rtk git commit -m "msg" && rtk git push
```

## RTK Commands by Workflow

### Build & Compile (80-90% savings)
```bash
rtk cargo build         # Cargo build output
rtk cargo check         # Cargo check output
rtk cargo clippy        # Clippy warnings grouped by file (80%)
rtk tsc                 # TypeScript errors grouped by file/code (83%)
rtk lint                # ESLint/Biome violations grouped (84%)
rtk prettier --check    # Files needing format only (70%)
rtk next build          # Next.js build with route metrics (87%)
```

### Test (60-99% savings)
```bash
rtk cargo test          # Cargo test failures only (90%)
rtk go test             # Go test failures only (90%)
rtk jest                # Jest failures only (99.5%)
rtk vitest              # Vitest failures only (99.5%)
rtk playwright test     # Playwright failures only (94%)
rtk pytest              # Python test failures only (90%)
rtk rake test           # Ruby test failures only (90%)
rtk rspec               # RSpec test failures only (60%)
rtk test <cmd>          # Generic test wrapper - failures only
```

### Git (59-80% savings)
```bash
rtk git status          # Compact status
rtk git log             # Compact log (works with all git flags)
rtk git diff            # Compact diff (80%)
rtk git show            # Compact show (80%)
rtk git add             # Ultra-compact confirmations (59%)
rtk git commit          # Ultra-compact confirmations (59%)
rtk git push            # Ultra-compact confirmations
rtk git pull            # Ultra-compact confirmations
rtk git branch          # Compact branch list
rtk git fetch           # Compact fetch
rtk git stash           # Compact stash
rtk git worktree        # Compact worktree
```

Note: Git passthrough works for ALL subcommands, even those not explicitly listed.

### GitHub (26-87% savings)
```bash
rtk gh pr view <num>    # Compact PR view (87%)
rtk gh pr checks        # Compact PR checks (79%)
rtk gh run list         # Compact workflow runs (82%)
rtk gh issue list       # Compact issue list (80%)
rtk gh api              # Compact API responses (26%)
```

### JavaScript/TypeScript Tooling (70-90% savings)
```bash
rtk pnpm list           # Compact dependency tree (70%)
rtk pnpm outdated       # Compact outdated packages (80%)
rtk pnpm install        # Compact install output (90%)
rtk npm run <script>    # Compact npm script output
rtk npx <cmd>           # Compact npx command output
rtk prisma              # Prisma without ASCII art (88%)
```

### Files & Search (60-75% savings)
```bash
rtk ls <path>           # Tree format, compact (65%)
rtk read <file>         # Code reading with filtering (60%)
rtk grep <pattern>      # Search grouped by file (75%). Format flags (-c, -l, -L, -o, -Z) run raw.
rtk find <pattern>      # Find grouped by directory (70%)
```

### Analysis & Debug (70-90% savings)
```bash
rtk err <cmd>           # Filter errors only from any command
rtk log <file>          # Deduplicated logs with counts
rtk json <file>         # JSON structure without values
rtk deps                # Dependency overview
rtk env                 # Environment variables compact
rtk summary <cmd>       # Smart summary of command output
rtk diff                # Ultra-compact diffs
```

### Infrastructure (85% savings)
```bash
rtk docker ps           # Compact container list
rtk docker images       # Compact image list
rtk docker logs <c>     # Deduplicated logs
rtk kubectl get         # Compact resource list
rtk kubectl logs        # Deduplicated pod logs
```

### Network (65-70% savings)
```bash
rtk curl <url>          # Compact HTTP responses (70%)
rtk wget <url>          # Compact download output (65%)
```

### Meta Commands
```bash
rtk gain                # View token savings statistics
rtk gain --history      # View command history with savings
rtk discover            # Analyze Claude Code sessions for missed RTK usage
rtk proxy <cmd>         # Run command without filtering (for debugging)
rtk init                # Add RTK instructions to CLAUDE.md
rtk init --global       # Add RTK to ~/.claude/CLAUDE.md
```

## Token Savings Overview

| Category | Commands | Typical Savings |
|----------|----------|-----------------|
| Tests | vitest, playwright, cargo test | 90-99% |
| Build | next, tsc, lint, prettier | 70-87% |
| Git | status, log, diff, add, commit | 59-80% |
| GitHub | gh pr, gh run, gh issue | 26-87% |
| Package Managers | pnpm, npm, npx | 70-90% |
| Files | ls, read, grep, find | 60-75% |
| Infrastructure | docker, kubectl | 85% |
| Network | curl, wget | 65-70% |

Overall average: **60-90% token reduction** on common development operations.
<!-- /rtk-instructions -->
