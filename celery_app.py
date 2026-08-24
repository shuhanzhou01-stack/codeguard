import os

from celery import Celery
from dotenv import load_dotenv

load_dotenv()


REDIS_URL = os.getenv(
    "REDIS_URL",
    "redis://localhost:6379/0"
)


celery_app = Celery(
    "codeguard",
    broker=REDIS_URL
)


celery_app.conf.update(
    imports=("tasks",),
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_always_eager=os.getenv("CELERY_TASK_ALWAYS_EAGER", "false").lower()
    in {"1", "true", "yes"},
)
