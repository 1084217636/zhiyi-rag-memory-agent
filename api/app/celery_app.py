"""知忆 AI 异步任务：文档解析与长期记忆抽取。"""
from celery import Celery

from app.config import settings

celery_app = Celery(
    "zhiyi",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks", "app.tasks.parse", "app.tasks.memory"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=False,
    task_track_started=True,
    task_default_queue="default",
    task_routes={
        "app.tasks.parse.*": {"queue": "parse"},
        "app.tasks.memory.*": {"queue": "memory"},
    },
)
