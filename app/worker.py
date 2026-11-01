import uuid

from celery import Celery

from app.core.config import settings

celery_app = Celery("worker", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_acks_late=True,            # a crashed worker's task is redelivered...
    worker_prefetch_multiplier=1,   # ...and long jobs don't hoard the queue
    task_reject_on_worker_lost=True,
    task_soft_time_limit=900, task_time_limit=1000,
    result_expires=3600,
    redis_socket_connect_timeout=3, redis_socket_timeout=3,  # fail fast instead of hanging a request
    broker_connection_timeout=3,
)


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_backoff_max=60,
                 max_retries=2, name="ingest_document")
def ingest_document_task(self, doc_id: str, force: bool = False) -> None:
    from app.services.ingestion import process_document
    process_document(uuid.UUID(doc_id), force=force)
