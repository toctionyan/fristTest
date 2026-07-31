from fastapi import APIRouter, Depends, HTTPException, Request, Response
import os


from app.security import require_api_permission
from agent_core.observability.metrics import simple_metrics
from app.services.readiness_service import readiness_report

router = APIRouter(tags=["system"])


@router.get("/health")
def health():
    """Liveness only: do not make container restarts depend on external services."""
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request):
    report = readiness_report(app=request.app)
    return Response(
        content=__import__("json").dumps(report, ensure_ascii=False),
        media_type="application/json",
        status_code=200 if report.get("status") == "ready" else 503,
    )


@router.get("/metrics", dependencies=[Depends(require_api_permission("debug:read"))])
def metrics(request: Request):
    # `/metrics` is deliberately separate from the Customer API.  In non-local
    # profiles deployments must provide a second internal token (and still add
    # network policy/mTLS at the ingress) before exposing this route.
    expected = os.getenv("METRICS_INTERNAL_TOKEN", "").strip()
    profile = str(readiness_report(app=request.app).get("profile") or "")
    if profile != "local":
        supplied = request.headers.get("X-Metrics-Token", "")
        if not expected or supplied != expected:
            raise HTTPException(status_code=404, detail="not found")
    return simple_metrics(request.app.state.agent_service.trace_logger)
