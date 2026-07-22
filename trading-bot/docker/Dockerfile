FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN pip install --no-cache-dir -e .

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src

VOLUME ["/app/data", "/app/logs", "/app/secrets"]

ENTRYPOINT ["python", "-m", "tradingbot.main"]
CMD ["run"]
