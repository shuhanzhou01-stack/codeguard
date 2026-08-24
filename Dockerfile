FROM python:3.13-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

RUN useradd --create-home --uid 10001 codeguard && chown codeguard:codeguard /app

COPY --chown=codeguard:codeguard . .

USER codeguard

CMD ["celery", "-A", "celery_app:celery_app", "worker", "--loglevel=info"]
