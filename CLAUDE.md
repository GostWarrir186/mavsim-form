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

## client_bot.py — клиентский бот
| Строка | Что |
|--------|-----|
| 19 | FSM: Registration, Support, Feedback |
| 29 | WEB_APP_URL, SUPPORT_CHAT_ID, MANAGER_CHAT_ID |
| 80 | verify_telegram_init_data |
| 103 | generate_order_id |
| 109 | sanitize_for_sheet |
| 124 | validate_order_data |
| 143 | _sync_check_user_by_phone |
| 154 | _sync_check_user_by_chat_id |
| 165 | _sync_update_profile |
| 182 | _sync_append_row |
| 186 | _sync_register_client |
| 197 | _sync_get_support_topic |
| 211 | _sync_save_support_topic |
| 222 | _sync_get_client_by_topic |
| 240 | cmd_start |
| 258 | go_main_menu (глобальная кнопка «🔙 Главное меню») |
| 267 | process_contact (авторизация по телефону) |
| 307 | start_fio_step (регистрация) |
| 313 | save_fio |
| 331 | get_main_menu |
| 347 | handle_webapp_data (заказы + профиль + push менеджеру о новом заказе) |
| 476 | support_start |
| 489 | support_send (создаёт/ищет топик) |
| 534 | support_continue (чат в режиме поддержки) |
| 555 | support_group_message (ответы менеджера → клиенту) |
| 585 | feedback_start |
| 600 | feedback_send |

## driver_bot.py — курьерский бот
| Строка | Что |
|--------|-----|
| 46 | FSM: DriverRegistration, DriverRejectReason, DriverSupport, DriverFeedback |
| 61 | _get_active_driver |
| 66 | _pad_row |
| 70 | _now_dushanbe |
| 74 | _month_label |
| 82 | _week_label |
| 91 | _current_week_range |
| 98 | _month_range |
| 108 | _sync_get_driver |
| 119 | _sync_get_driver_support_topic |
| 133 | _sync_save_driver_support_topic |
| 144 | _sync_get_driver_by_topic |
| 158 | _sync_register_driver |
| 173 | _sync_get_all_active_drivers |
| 191 | _sync_get_driver_deliveries |
| 226 | _sync_get_free_orders |
| 256 | _sync_take_order |
| 275 | _sync_release_order |
| 297 | _sync_update_status |
| 308 | _sync_find_order_by_id |
| 321 | _sync_reassign_order |
| 344 | _sync_get_orders_for_dashboard → (active, free, new) |
| 394 | _sync_get_drivers_for_dashboard |
| 411 | _async_get_admin_dashboard_data → {orders, free, new, couriers} |
| 425 | generate_excel_report |
| 529 | build_driver_main_menu (async, генерирует URL кабинета) |
| 567 | send_client_push |
| 577 | driver_go_main_menu (глобальная кнопка «🔙 Главное меню») |
| 585 | cmd_start_driver |
| 631 | save_driver_fio (→ уведомляет manager_bot о новом курьере) |
| 670 | handle_webapp (generate_report — Excel-отчёт) |
| 733 | show_jobs (биржа заказов) |
| 763 | accept_order (take:) |
| 803 | reject_order (reject: — отказ, FSM причина + фото) |
| 840 | _do_reject (финализация отказа) |
| 894 | reject_skip |
| 901 | reject_reason_photo |
| 908 | reject_reason_text |
| 913 | load_order |
| 942 | transit_order |
| 970 | arrived_order |
| 998 | finish_order |
| 1026 | driver_support_start |
| 1042 | driver_support_send (создаёт/ищет топик) |
| 1079 | driver_support_continue |
| 1099 | driver_support_group_message (ответы менеджера → курьеру) |
| 1125 | driver_feedback_start |
| 1140 | driver_feedback_send |

## manager_bot.py — менеджерский бот
| Строка | Что |
|--------|-----|
| 54 | FSM: ManagerCancelOrder (своя причина отмены) |
| 58 | _is_manager (проверка MANAGER_CHAT_ID) |
| 64 | _sync_approve_driver (PENDING → ACTIVE) |
| 79 | _sync_reject_driver (PENDING → REJECTED) |
| 96 | _sync_set_order_ready (NEW → READY_FOR_DRIVERS) |
| 115 | _sync_cancel_order (NEW → CANCELLED) |
| 137 | _sync_get_all_couriers_deliveries |
| 168 | _sync_get_all_drivers_rates |
| 193 | generate_summary_excel (сводный Excel на всех курьеров) |
| 286 | _build_panel_message |
| 309 | cmd_start_manager (/start → сразу открывает панель) |
| 316 | _send_panel (отправляет reply keyboard с WebApp) |
| 337 | panel_refresh_text (кнопка «🔄 Обновить») |
| 344 | admin_refresh (inline callback обновления) |
| 354 | do_reassign (rt: callback — выбор нового курьера) |
| 425 | order_accept (oa: — принять NEW-заказ → READY) |
| 450 | order_cancel_menu (oc: — показать причины отмены) |
| 464 | order_cancel_reason (ocr: — отмена с готовой причиной) |
| 489 | order_cancel_custom_start (ocx: — запрос своей причины) |
| 501 | order_cancel_custom_reason (FSM — сохраняет свою причину) |
| 524 | handle_webapp (set_ready / reassign_confirm / reassign_request / отчёты) |
| 726 | approve_driver (approve_driver: callback ✅) |
| 754 | reject_driver_cb (reject_driver: callback ❌) |

## config.py
| Строка | Что |
|--------|-----|
| 24 | client_bot, driver_bot, manager_bot |
| 28 | client_dp, driver_dp, manager_dp |
| 37 | gspread init, sheet / drivers_sheet / clients_sheet |
| 76 | SUPPORT_CHAT_ID, feedback_topic_id |
| 82 | get_or_create_feedback_topic |

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
