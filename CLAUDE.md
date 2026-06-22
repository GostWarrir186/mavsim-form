<!-- rtk-instructions v2 -->
# Правила работы с кодом

Before editing any file, read it first. Before modifying a function, grep for all callers. Research before you edit.

# Карта файлов (используй для точечного чтения)

## bot.py — клиентский бот
| Строка | Что |
|--------|-----|
| 19 | FSM: Registration, Support, Feedback |
| 79 | verify_telegram_init_data |
| 102 | generate_order_id |
| 108 | sanitize_for_sheet |
| 123 | validate_order_data |
| 142 | _sync_check_user_by_phone |
| 153 | _sync_check_user_by_chat_id |
| 164 | _sync_update_profile |
| 185 | _sync_register_client |
| 196 | _sync_get_support_topic |
| 210 | _sync_save_support_topic |
| 221 | _sync_get_client_by_topic |
| 239 | cmd_start |
| 257 | go_main_menu (глобальная кнопка «🔙 Главное меню») |
| 266 | process_contact (авторизация по телефону) |
| 306 | start_fio_step (регистрация) |
| 330 | get_main_menu |
| 346 | handle_webapp_data (заказы + профиль) |
| 448 | support_start |
| 461 | support_send (создаёт/ищет топик) |
| 506 | support_continue (чат в режиме поддержки) |
| 527 | support_group_message (ответы менеджера → клиенту) |
| 557 | feedback_start |
| 572 | feedback_send |

## driver_bot.py — курьерский бот
| Строка | Что |
|--------|-----|
| 38 | FSM: DriverRegistration, DriverSupport, DriverFeedback |
| 50 | _get_active_driver |
| 64 | _month_label |
| 76 | _sync_get_driver |
| 88 | _sync_get_driver_support_topic |
| 102 | _sync_save_driver_support_topic |
| 113 | _sync_get_driver_by_topic |
| 127 | _sync_register_driver |
| 142 | _sync_get_driver_deliveries |
| 184 | _sync_get_free_orders |
| 214 | _sync_take_order |
| 232 | _sync_update_status |
| 244 | generate_excel_report |
| 348 | get_driver_main_menu |
| 369 | driver_go_main_menu (глобальная кнопка «🔙 Главное меню») |
| 376 | cmd_start_driver |
| 444 | open_cabinet (кабинет курьера) |
| 488 | send_monthly_report |
| 501 | handle_report_webapp (Excel-отчёт) |
| 560 | show_jobs (биржа заказов) |
| 590 | accept_order (take) |
| 631 | load_order |
| 660 | transit_order |
| 688 | arrived_order |
| 716 | finish_order |
| 745 | driver_support_start |
| 761 | driver_support_send |
| 799 | driver_support_continue |
| 820 | driver_support_group_message (ответы менеджера → курьеру) |
| 850 | driver_feedback_start |
| 865 | driver_feedback_send |

## config.py
| Строка | Что |
|--------|-----|
| 19 | client_bot, driver_bot, dispatchers |
| 32 | gspread init, sheet / drivers_sheet / clients_sheet |
| 65 | SUPPORT_CHAT_ID, feedback_topic_id |
| 71 | get_or_create_feedback_topic |

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
