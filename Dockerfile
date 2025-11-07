# 1. Берем официальный образ Python 3.10 (можете выбрать другую версию)
FROM python:3.11

# 2. Устанавливаем рабочую директорию внутри контейнера
WORKDIR /app

# 3. Устанавливаем переменные окружения, чтобы Python работал корректнее
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 4. Обновляем pip и устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip -r requirements.txt

# 5. Копируем ВЕСЬ код вашего проекта в контейнер
COPY . .

# Мы не пишем команду запуска (CMD) здесь,
# потому что у нас ДВЕ разных команды (для бота и для админки).
# Мы укажем их в docker-compose.yml.
