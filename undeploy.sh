#!/bin/bash
set -euo pipefail

SERVICE_NAME="bo2sale"
UNIT_PATH="/etc/systemd/system/$SERVICE_NAME.service"

echo "Остановка и удаление сервиса $SERVICE_NAME..."

systemctl stop "$SERVICE_NAME.service" || true
systemctl disable "$SERVICE_NAME.service" || true

if [[ -f "$UNIT_PATH" ]]; then
  rm "$UNIT_PATH"
fi

systemctl daemon-reload

echo "Удаление завершено."
