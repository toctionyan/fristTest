import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from fastapi.middleware.cors import CORSMiddleware
from app.api.document_api import router as document_router
from app.api.health_api import router as health_router
from app.api.product_api import router as product_router
from app.services.agent_service import AgentService
from app.services.document_service import DocumentService
from agent_core.config import check_runtime_config, is_local_dev, validate_production_security
from agent_core.composition import get_runtime_registry
from agent_core.runtime.profile import require_runtime_profile
from agent_core.observability.correlation import reset_correlation_id, set_correlation_id
from agent_core.presentation.actions import validate_catalog_integrity
from agent_core.transaction.authority import registered_action_policy_ids
from agent_core.transaction.commit_runtime import COMMITTABLE_TRANSACTION_ACTION_IDS
from agent_core.kernel import validate_runtime_architecture



def create_app() -> FastAPI:
    # Formal application startup must declare a runtime profile. Tests and local
    # bootstrap set APP_PROFILE=local explicitly; no missing variable silently
    # becomes an insecure development deployment.
    require_runtime_profile()
    check_runtime_config(strict=False)
    validate_production_security()
    validate_runtime_architecture(get_runtime_registry())
    runtime_registry = get_runtime_registry()
    validate_catalog_integrity(
        action_ids=runtime_registry.preparable_action_ids(),
        gateway_policy_ids=registered_action_policy_ids(),
        commit_dispatcher_ids=COMMITTABLE_TRANSACTION_ACTION_IDS,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        try:
            close_agent = getattr(app.state.agent_service, "close", None)
            if callable(close_agent):
                close_agent()
        finally:
            close_documents = getattr(app.state.document_service, "close", None)
            if callable(close_documents):
                close_documents()

    app = FastAPI(
        title="Ecommerce Agent Service",
        version="20.6.1",
        lifespan=lifespan,
    )

    allowed_origins = [
        item.strip()
        for item in os.getenv(
            "AGENT_ALLOWED_ORIGINS", "http://127.0.0.1:9000,http://localhost:9000"
        ).split(",")
        if item.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def correlation_context(request, call_next):
        correlation_id = set_correlation_id(request.headers.get("X-Correlation-ID"))
        try:
            response = await call_next(request)
        except Exception:
            # The exception handler can still retrieve the ContextVar while
            # producing its response and structured error trace.
            raise
        finally:
            # ContextVar is reset after the request lifecycle so an async
            # worker/request never inherits another customer's correlation id.
            reset_correlation_id()
        response.headers["X-Correlation-ID"] = correlation_id
        return response

    app.state.agent_service = AgentService()
    app.state.document_service = DocumentService()

    app.include_router(health_router)
    app.include_router(product_router)
    # Documents are a first-class /api product surface.  The router itself
    # enforces actor scope; it is exposed through the formal API boundary.
    app.include_router(document_router, prefix="/api")

    # The product UI is intentionally hosted by the Agent service. The browser
    # never receives internal Business Service credentials; business reads and
    # commands go through authenticated Agent APIs.
    project_root = Path(__file__).resolve().parents[1]
    react_dist = project_root / "frontend" / "dist"
    if react_dist.is_dir():
        # Customer Portal is the only default browser surface.
        app.mount("/web", StaticFiles(directory=react_dist, html=True), name="customer-portal")

        @app.get("/", include_in_schema=False)
        def customer_portal_index():
            return FileResponse(react_dist / "index.html")
    else:
        # Never silently expose the alternate console when a Customer Portal build
        # is absent.  Deployment health should show the packaging fault.
        @app.get("/", include_in_schema=False)
        def missing_customer_portal():
            return Response(status_code=503, content="Customer Portal bundle is not deployed")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return Response(status_code=204)
    return app


app = create_app()
