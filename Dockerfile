FROM python:3.12-slim

WORKDIR /app

COPY atlys_italy_notifier.py /app/atlys_italy_notifier.py

RUN mkdir -p /app/.state /app/logs

CMD ["python", "/app/atlys_italy_notifier.py", "serve", "--watch-country", "italy", "--interval-seconds", "300"]
