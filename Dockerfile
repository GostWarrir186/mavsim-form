FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY client_bot.py driver_bot.py manager_bot.py config.py main.py db.py ./

CMD ["python", "main.py"]
