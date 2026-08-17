#!/usr/bin/env bash
# Деплой ботов на VPS. Запускать НА СЕРВЕРЕ из ~/automation/mavsim-delivery-bot:
#   ./deploy.sh
# Выкатывает весь согласованный набор .py разом (git pull), пересобирает
# контейнер и показывает логи — чтобы не повторялась история с частичной
# заливкой одного файла → ImportError → краш-цикл → APIError 429.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_DIR="$(dirname "$REPO_DIR")"
SERVICE="mavsim-bots"

cd "$REPO_DIR"

# Незакоммиченные правки прямо на сервере — стоп: pull их затрёт молча.
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "!! На сервере есть незакоммиченные изменения отслеживаемых файлов:"
  git status --short --untracked-files=no
  echo "!! Разберись с ними (git diff / git checkout --) и запусти снова."
  exit 1
fi

echo ">> git pull"
git pull --ff-only

echo ">> docker compose build $SERVICE"
cd "$COMPOSE_DIR"
docker compose build "$SERVICE"

echo ">> docker compose up -d $SERVICE"
docker compose up -d "$SERVICE"

echo ">> логи (первая строка трейсбека важнее 429 внизу)"
sleep 5
docker compose logs --tail 60 "$SERVICE"
