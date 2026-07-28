"""FastAPI application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from analystbench import __version__
from analystbench.agent_execution import AgentExecutionService
from analystbench.api.routes.benchmarks import router as benchmark_router
from analystbench.api.routes.case_library import router as case_library_router
from analystbench.api.routes.cases_local import router as cases_local_router
from analystbench.api.routes.catalog import router as catalog_router
from analystbench.api.routes.comparisons import router as comparison_router
from analystbench.api.routes.direct_results import router as direct_results_router
from analystbench.api.routes.eval_specs import router as eval_specs_router
from analystbench.api.routes.evaluation_sessions import router as evaluation_sessions_router
from analystbench.api.routes.evaluation_submissions import (
    router as evaluation_submissions_router,
)
from analystbench.api.routes.execution import router as execution_router
from analystbench.api.routes.health import router as health_router
from analystbench.api.routes.settings import router as settings_router
from analystbench.benchmark import BenchmarkService
from analystbench.case_library import (
    CaseLibraryService,
    EvaluationBatchService,
    ReportDraftService,
)
from analystbench.comparison import ComparisonService
from analystbench.config import Settings, get_settings
from analystbench.content_store import ContentStore
from analystbench.db.session import create_database_engine, create_session_factory
from analystbench.errors import AnalystBenchError
from analystbench.eval_spec import EvalSpecService
from analystbench.evaluation_session import EvaluationSessionService
from analystbench.evaluation_submission import (
    EvaluationMethodService,
    EvaluationSubmissionService,
)
from analystbench.logging import configure_logging
from analystbench.services import CatalogService

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or get_settings()
    configure_logging(active_settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = active_settings
        app.state.database_engine = create_database_engine(active_settings)
        app.state.session_factory = create_session_factory(app.state.database_engine)
        app.state.content_store = ContentStore(active_settings.content_store_path)
        app.state.catalog_service = CatalogService(
            app.state.session_factory, app.state.content_store
        )
        app.state.agent_execution_service = AgentExecutionService(
            app.state.session_factory, app.state.content_store, active_settings
        )
        app.state.eval_spec_service = EvalSpecService(
            app.state.session_factory, app.state.content_store
        )
        app.state.evaluation_session_service = EvaluationSessionService(
            app.state.session_factory, app.state.content_store
        )
        app.state.evaluation_method_service = EvaluationMethodService(
            app.state.session_factory, active_settings
        )
        app.state.evaluation_submission_service = EvaluationSubmissionService(
            app.state.session_factory, active_settings
        )
        app.state.case_library_service = CaseLibraryService(
            app.state.session_factory, app.state.content_store, active_settings
        )
        app.state.report_draft_service = ReportDraftService(app.state.session_factory)
        app.state.evaluation_batch_service = EvaluationBatchService(
            app.state.session_factory, app.state.content_store, active_settings
        )
        app.state.benchmark_service = BenchmarkService(
            app.state.session_factory, app.state.content_store, active_settings
        )
        app.state.comparison_service = ComparisonService(app.state.benchmark_service)
        logger.info("application_started", extra={"environment": active_settings.environment})
        try:
            yield
        finally:
            app.state.database_engine.dispose()
            logger.info("application_stopped")

    app = FastAPI(
        title="AnalystBench API",
        version=__version__,
        openapi_url="/api/v1/openapi.json",
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next: object) -> object:
        request_id = request.headers.get("X-Request-ID", str(uuid4()))
        response = await call_next(request)  # type: ignore[misc]
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(AnalystBenchError)
    async def application_error_handler(_: Request, exc: AnalystBenchError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                    "retryable": exc.retryable,
                }
            },
        )

    app.include_router(health_router, prefix="/api/v1")
    app.include_router(catalog_router, prefix="/api/v1")
    app.include_router(benchmark_router, prefix="/api/v1")
    app.include_router(comparison_router, prefix="/api/v1")
    app.include_router(execution_router, prefix="/api/v1")
    app.include_router(eval_specs_router, prefix="/api/v1")
    app.include_router(evaluation_sessions_router, prefix="/api/v1")
    app.include_router(evaluation_submissions_router, prefix="/api/v1")
    app.include_router(case_library_router, prefix="/api/v1")
    app.include_router(direct_results_router, prefix="/api/v1")
    app.include_router(cases_local_router, prefix="/api/v1")
    app.include_router(settings_router, prefix="/api/v1")
    return app


app = create_app()
