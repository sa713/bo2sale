#!/bin/bash

SERVICE_NAME="bo2sale"

echo "Остановка и удаление сервиса $SERVICE_NAME..."

systemctl stop $SERVICE_NAME.service
systemctl disable $SERVICE_NAME.service
rm /etc/systemd/system/$SERVICE_NAME.service
systemctl daemon-reload

echo "Удаление завершено."
