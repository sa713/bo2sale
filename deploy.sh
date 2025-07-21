#!/bin/bash

SERVICE_NAME="bo2sale"
BOT_PATH="/root/bo2sale"
PYTHON_BIN="/usr/bin/python3"

echo "Запуск деплоя сервиса $SERVICE_NAME..."

# Копируем файлы (если надо, например, из git или локальной папки)
# git clone / обновить проект - пример, если есть git

# Создаем виртуальное окружение (опционально)
# python3 -m venv $BOT_PATH/venv
# source $BOT_PATH/venv/bin/activate
# pip install -r $BOT_PATH/requirements.txt

# Устанавливаем зависимости
pip install -r $BOT_PATH/requirements.txt

# Копируем systemd unit (если нужно)
cp $BOT_PATH/$SERVICE_NAME.service /etc/systemd/system/

# Перезагружаем systemd
systemctl daemon-reload
systemctl enable $SERVICE_NAME.service
systemctl restart $SERVICE_NAME.service

echo "Деплой завершён. Статус сервиса:"
systemctl status $SERVICE_NAME.service --no-pager
