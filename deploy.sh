#!/bin/bash
set -euo pipefail

SERVICE_NAME="bo2sale"
BOT_PATH="/root/bo2sale"
PYTHON_BIN="/usr/bin/python3"
SERVICE_FILE="$BOT_PATH/$SERVICE_NAME.service"

echo "Запуск деплоя сервиса $SERVICE_NAME..."

if [[ ! -f "$BOT_PATH/requirements.txt" ]]; then
  echo "Не найден requirements.txt в $BOT_PATH"
  exit 1
fi

if [[ ! -f "$SERVICE_FILE" ]]; then
  echo "Не найден unit-файл: $SERVICE_FILE"
  exit 1
fi

$PYTHON_BIN -m pip install -r "$BOT_PATH/requirements.txt"
cp "$SERVICE_FILE" "/etc/systemd/system/$SERVICE_NAME.service"

systemctl daemon-reload
systemctl enable "$SERVICE_NAME.service"
systemctl restart "$SERVICE_NAME.service"

echo "Деплой завершён. Статус сервиса:"
systemctl status "$SERVICE_NAME.service" --no-pager
