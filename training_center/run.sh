#!/bin/sh
echo "Тренировочный центр: каркас запущен"
exec python3 -m http.server 8099 --directory /app
