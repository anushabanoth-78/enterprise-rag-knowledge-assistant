import structlog
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.metrics import _redis as metrics_redis
from app.core.metrics import read_http_metrics
from app.core.security import rate_limit, require_admin
from app.db import repository
from app.db.models import User
from app.services import evaluation
from app.worker import celery_app

log = structlog.get_logger()
router = APIRouter(tags=["admin"])


def worker_status() -> dict:
    """Best-effort view of the Celery side. Never raises: the dashboard must load even if Redis is down."""
    out = {"workers_online": 0, "active_tasks": 0, "queued_tasks": None, "reachable": False}
    try:
        out["queued_tasks"] = int(metrics_redis.llen("celery"))
        active = celery_app.control.inspect(timeout=1.0).active() or {}
        out["workers_online"] = len(active)
        out["active_tasks"] = sum(len(v) for v in active.values())
        out["reachable"] = True
    except Exception as exc:  # noqa: BLE001
        log.warning("worker_status_unavailable", error=exc.__class__.__name__)
    return out


@router.get("/admin/metrics")
def metrics(_: User = Depends(require_admin), db: Session = Depends(repository.get_db)):
    return {**repository.admin_metrics(db), "http": read_http_metrics(), "workers": worker_status()}


@router.get("/evaluation/results")
def eval_results(_: User = Depends(require_admin)):
    # The benchmark CSV is global (one file for the whole deployment), so it is admin-only.
    return evaluation.read_results()


@router.get("/evaluation/usage")
def eval_usage(user: User = Depends(rate_limit), db: Session = Depends(repository.get_db)):
    return repository.usage_stats(db, user.id)
