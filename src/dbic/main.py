import logging
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from dbic.adapters.agent.null import NullAgentRuntime
from dbic.adapters.history_factory import historical_adapters
from dbic.adapters.persistence.postgres import create_database, database_ready
from dbic.adapters.persistence.tasks import PostgresTaskStore
from dbic.adapters.registry import YamlRegistry
from dbic.api.routes.diagnoses import router as diagnoses_router
from dbic.api.service import DiagnosticService
from dbic.core.runner import TaskRunner
from dbic.plugins.mysql.diagnostics import MySQLDiagnostics
from dbic.settings import Settings

logger = logging.getLogger("dbic")
bearer = HTTPBearer(auto_error=False)


class Health(BaseModel):
    status: Literal["ok", "ready"]


class Capability(BaseModel):
    database_type: str
    status: Literal["available", "not_implemented"] = "not_implemented"
    tools: list[str] = []


class Capabilities(BaseModel):
    milestone: Literal["M2"] = "M2"
    historical_backends: dict[str, str]
    databases: list[Capability]


class ErrorBody(BaseModel):
    code: str
    message: str
    retryable: bool
    details: dict[str, str]
    request_id: str


def create_app(
    settings: Settings | None = None,
    *,
    readiness: Callable[[], bool] | None = None,
    diagnostics: DiagnosticService | None = None,
) -> FastAPI:
    config = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_database(config)

        def check_database() -> bool:
            assert engine is not None
            return database_ready(engine)

        app.state.readiness = readiness or check_database
        if diagnostics is None:
            registry = YamlRegistry(config.configs_dir / "instances.yaml")
            store = PostgresTaskStore(engine)
            tools = MySQLDiagnostics(config.mysql_dsn())
            metrics, search = historical_adapters(config)
            app.state.diagnostics = DiagnosticService(
                registry,
                store,
                TaskRunner(
                    registry, tools, store, NullAgentRuntime(), metrics=metrics, search=search
                ),
            )
        else:
            app.state.diagnostics = diagnostics
        try:
            yield
        finally:
            if engine is not None:
                engine.dispose()

    app = FastAPI(
        title="Database AI Center",
        version="0.3.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    async def authorize(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> None:
        if credentials is None or not secrets.compare_digest(
            credentials.credentials.encode(), config.api_token.get_secret_value().encode()
        ):
            raise HTTPException(401, "Unauthorized", headers={"WWW-Authenticate": "Bearer"})

    app.include_router(diagnoses_router, dependencies=[Depends(authorize)])

    @app.middleware("http")
    async def correlation(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.request_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    def error(request: Request, status: int, code: str, message: str) -> JSONResponse:
        return JSONResponse(
            status_code=status,
            content={
                "code": code,
                "message": message,
                "retryable": status == 503,
                "details": {},
                "request_id": request.state.request_id,
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        response = error(request, exc.status_code, f"http_{exc.status_code}", str(exc.detail))
        response.headers.update(exc.headers or {})
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error(request, 422, "validation_error", "Invalid request")

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception) -> JSONResponse:
        logger.error("request_id=%s failed type=%s", request.state.request_id, type(exc).__name__)
        response = error(request, 500, "internal_error", "Internal server error")
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(SQLAlchemyError)
    async def storage_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.error(
            "request_id=%s persistence failed type=%s", request.state.request_id, type(exc).__name__
        )
        return error(request, 503, "storage_unavailable", "Task storage is unavailable")

    @app.get("/healthz", response_model=Health)
    def health() -> Health:
        return Health(status="ok")

    @app.get("/readyz", response_model=Health, responses={503: {"model": ErrorBody}})
    async def ready(request: Request) -> Health | JSONResponse:
        if not await run_in_threadpool(request.app.state.readiness):
            return error(request, 503, "dependency_unavailable", "PostgreSQL schema is not ready")
        return Health(status="ready")

    @app.get(
        "/api/v1/capabilities",
        response_model=Capabilities,
        dependencies=[Depends(authorize)],
        responses={401: {"model": ErrorBody}},
    )
    def capabilities() -> Capabilities:
        return Capabilities(
            historical_backends={
                "metrics": config.metrics_backend,
                "search": config.search_backend,
            },
            databases=[
                Capability(
                    database_type="mysql",
                    status="available",
                    tools=["mysql.health", "mysql.lock_chain", "mysql.slow_query_summary"],
                )
            ]
            + [
                Capability(database_type=kind)
                for kind in ("redis", "mongodb", "postgresql", "doris")
            ],
        )

    @app.get("/openapi.json", dependencies=[Depends(authorize)], include_in_schema=False)
    def openapi() -> JSONResponse:
        return JSONResponse(app.openapi())

    return app
