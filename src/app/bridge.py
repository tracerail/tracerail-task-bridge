"""
TraceRail Task Bridge - FastAPI Application (v3.1 - Tenant-Aware)

This service acts as a bridge between human users/external systems and
running Temporal workflows. It has been refactored to support a multi-tenant
architecture.
"""

import os
import re
import structlog
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import (
    FastAPI,
    Depends,
    HTTPException,
    Request,
    Path,
    Body,
    Security,
    APIRouter,
)
from fastapi.security import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, Field
from temporalio.client import Client
from temporalio.service import RPCError
from temporalio.contrib.opentelemetry import TracingInterceptor

from tracerail.service.case_service import CaseService
from tracerail.domain.cases import Case as CaseResponse
from tracerail.workflows.flexible_case_workflow import FlexibleCaseWorkflow
from .tracing import setup_tracing


# --- Structured Logging Setup ---
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)
log = structlog.get_logger()


# --- Application Lifespan Management ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manages the Temporal client's lifecycle with the FastAPI application."""
    setup_tracing("tracerail-task-bridge")

    if os.getenv("TESTING_MODE") == "true":
        log.info("Running in TESTING_MODE, skipping Temporal connection.")
        app.state.temporal_client = None
        yield
        return

    host = os.getenv("TEMPORAL_HOST", "localhost")
    port = int(os.getenv("TEMPORAL_PORT", 7233))
    # In a multi-tenant setup, we connect to the 'default' namespace.
    # The CaseService is responsible for creating clients for specific tenant namespaces.
    namespace = "default"
    target = f"{host}:{port}"

    log.info("Connecting to Temporal service", target=target)
    try:
        client = await Client.connect(
            target,
            namespace=namespace,
            interceptors=[TracingInterceptor()],
        )
        app.state.temporal_client = client
        log.info("Successfully connected to Temporal.")
        yield
    finally:
        if hasattr(app.state, "temporal_client") and app.state.temporal_client:
            await app.state.temporal_client.close()
            log.info("Temporal client connection closed.")


# --- FastAPI Application Setup ---
app = FastAPI(
    title="TraceRail Task Bridge",
    description="A multi-tenant bridge service for interacting with TraceRail workflows.",
    version="3.1.0",
    lifespan=lifespan,
)

# --- CORS Middleware ---
allowed_origins = [
    os.getenv("FRONTEND_URL", "http://localhost:3000"),
    "http://localhost:3002",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Metrics and Tracing Instrumentation ---
Instrumentator().instrument(app).expose(app)
FastAPIInstrumentor.instrument_app(app)

# --- Authentication & Authorization ---
api_key_header = APIKeyHeader(name="Authorization", auto_error=False)

async def get_tenant_id_from_auth(
    api_key: str = Security(api_key_header),
    tenantId: str = Path(..., description="The ID of the tenant."),
) -> str:
    """
    Dependency that validates an API key and ensures it matches the tenantId in the path.
    This is a basic implementation for demonstration and contract testing.
    """
    if api_key is None:
        raise HTTPException(
            status_code=401, detail="Authorization header is missing"
        )

    expected_prefix = f"Bearer test-token-for-"
    if not api_key.startswith(expected_prefix):
        raise HTTPException(status_code=401, detail="Invalid authorization scheme.")

    # In a real system, the token would be a JWT or an opaque token that is
    # validated and mapped to a tenantId server-side. For this test, we embed
    # the tenantId in the token itself for simplicity.
    token_tenant_id = api_key[len(expected_prefix):]
    if token_tenant_id != tenantId:
        raise HTTPException(
            status_code=403, detail="Token is not valid for the specified tenant."
        )
    return tenantId


# --- Dependency Injection ---
def get_case_service(request: Request) -> CaseService:
    """Dependency provider that creates and yields a CaseService instance."""
    client: Optional[Client] = request.app.state.temporal_client
    if not client:
        raise HTTPException(status_code=503, detail="Temporal client is not available.")
    return CaseService(client=client)


# --- Pydantic Models ---
class AgentDecisionPayload(BaseModel):
    decision: str = Field(..., description="The decision made by the agent.")

class DecisionResponse(BaseModel):
    caseId: str
    status: str
    message: str


# --- Tenant-Aware API Router ---
tenant_router = APIRouter(
    prefix="/api/v1/tenants/{tenantId}",
    tags=["Cases"],
    dependencies=[Depends(get_tenant_id_from_auth)],
)

@tenant_router.get("/cases/{caseId}", response_model=CaseResponse)
async def get_case_by_id(
    tenantId: str,
    caseId: str,
    case_service: CaseService = Depends(get_case_service),
):
    """Retrieves the complete details for a single case for a specific tenant."""
    case = await case_service.get_by_id(case_id=caseId, tenant_id=tenantId)
    if case:
        return case
    raise HTTPException(status_code=404, detail=f"Case with ID '{caseId}' not found.")

@tenant_router.post("/cases/{caseId}/decision", response_model=DecisionResponse)
async def submit_tenant_decision(
    tenantId: str,
    caseId: str,
    payload: AgentDecisionPayload = Body(...),
    case_service: CaseService = Depends(get_case_service),
):
    """Receives a decision from an agent and signals the corresponding workflow."""
    try:
        result = await case_service.submit_decision(
            case_id=caseId,
            decision=payload.decision,
            tenant_id=tenantId,
        )
        return result
    except RPCError as e:
        if e.status and e.status.name == 'NOT_FOUND':
            raise HTTPException(
                status_code=404,
                detail=f"Case with ID '{caseId}' not found or has already completed.",
            )
        raise HTTPException(
            status_code=500, detail=f"Temporal service error: {e.message}"
        ) from e

app.include_router(tenant_router)


# --- General & Pact Endpoints ---
@app.get("/", response_class=HTMLResponse, tags=["General"])
async def root():
    """Provides a simple HTML landing page with links to the documentation."""
    return """
    <html>
        <head><title>TraceRail Task Bridge</title></head>
        <body style="font-family: sans-serif; padding: 2em;">
            <h1>🌉 TraceRail Task Bridge (Multi-Tenant)</h1>
            <p>This service is running. Access tenant-specific data via <code>/api/v1/tenants/{tenantId}/...</code></p>
            <ul>
                <li><a href="/docs">API Documentation (Swagger UI)</a></li>
                <li><a href="/redoc">API Documentation (ReDoc)</a></li>
            </ul>
        </body>
    </html>
    """

class ProviderState(BaseModel):
    consumer: str
    state: str

@app.post("/_pact/provider_states", include_in_schema=False)
async def provider_states_handler(payload: ProviderState, request: Request):
    """Sets up a specific provider state for Pact verification."""
    log.info("Received provider state setup request", state=payload.state)
    client: Optional[Client] = request.app.state.temporal_client
    if not client:
        raise HTTPException(status_code=503, detail="Temporal client is not available.")

    # This regex handles both provider states defined in the consumer contract.
    state_pattern = re.compile(
        r"case with ID ([\w-]+) (?:exists|is ready for a decision) for tenant with ID ([\w-]+)"
    )
    match = state_pattern.search(payload.state)

    if not match:
        log.warn("Provider state not recognized", state=payload.state)
        return {"result": "State not found"}

    case_id, tenant_id = match.groups()
    task_queue = "pact-verification-task-queue"
    log.info("Setting up state", case_id=case_id, tenant_id=tenant_id)

    try:
        # In Phase 2, this would connect to the correct tenant namespace.
        # For now, we run the workflow in the default namespace.
        await client.start_workflow(
            FlexibleCaseWorkflow.run,
            args=["expense_approval", "1.0.0", {"submitter_name": "Pact Test"}],
            id=case_id,
            task_queue=task_queue,
            id_reuse_policy="TerminateIfRunning",
        )
        return {"result": "ok"}
    except RPCError as e:
        log.error("Failed to set up provider state", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to start workflow for Pact state")
