FROM python:3.12-slim

WORKDIR /app

# Устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код
COPY main.py .

# Открываем порт
EXPOSE 8000

# Запускаем сервис
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]