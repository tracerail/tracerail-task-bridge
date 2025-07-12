"""
TraceRail Task Bridge - FastAPI Application (v3)

This service acts as a bridge between human users/external systems and
running Temporal workflows. It uses FastAPI's lifespan management to maintain
a single, persistent connection to the Temporal service and uses dependency
injection to provide services to its endpoints.
"""

import os
import uuid
import structlog
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional, Dict, Any, List

from fastapi import FastAPI, Depends, HTTPException, Query, Request, Path, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from prometheus_fastapi_instrumentator import Instrumentator, metrics
from pydantic import BaseModel, Field
from temporalio.client import Client
from temporalio.service import RPCError

# Import the core domain service and response model
from tracerail.domain.cases import Case as CaseResponse
from tracerail.domain.cases import CaseService
from tracerail.workflows.flexible_case_workflow import FlexibleCaseWorkflow


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
    """
    Manages the Temporal client's lifecycle with the FastAPI application.
    The client is created on startup and is available for all requests.
    In testing mode, the client connection is skipped.
    """
    if os.getenv("TESTING_MODE") == "true":
        log.info("Running in TESTING_MODE, skipping Temporal connection.")
        app.state.temporal_client = None
        yield
        return

    host = os.getenv("TEMPORAL_HOST", "localhost")
    port = int(os.getenv("TEMPORAL_PORT", 7233))
    namespace = os.getenv("TEMPORAL_NAMESPACE", "default")
    target = f"{host}:{port}"

    log.info("Connecting to Temporal service", target=target)
    try:
        client = await Client.connect(target, namespace=namespace)
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
    description="A bridge service for interacting with TraceRail workflows.",
    version="3.0.0",
    lifespan=lifespan,
)

# --- CORS Middleware Setup ---
# This is crucial for allowing the frontend (which runs on a different port)
# to make API calls to this backend.
# See: https://fastapi.tiangolo.com/tutorial/cors/

# Define the list of origins that are allowed to make cross-origin requests.
# It's good practice to control this via an environment variable for production.
allowed_origins = [
    os.getenv("FRONTEND_URL", "http://localhost:3000"),
    "http://localhost:3002",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],  # Allows all standard HTTP methods
    allow_headers=["*"],  # Allows all headers
)

# --- Metrics Instrumentation ---
# Exposes a /metrics endpoint for Prometheus scraping.
instrumentator = Instrumentator().instrument(app)
# Add the single, default metric provider to generate all necessary metrics.
instrumentator.add(metrics.default())
instrumentator.expose(app)

# --- Dependency Injection ---

def get_case_service(request: Request) -> CaseService:
    """
    A dependency provider that creates and yields a CaseService instance.
    It retrieves the Temporal client from the application state.
    """
    client: Optional[Client] = request.app.state.temporal_client
    if not client:
        # This will be true in TESTING_MODE, which is handled by tests.
        # In a real environment, this would indicate a startup failure.
        raise HTTPException(
            status_code=503,
            detail="Temporal client is not available.",
        )
    return CaseService(client=client)


# --- Pydantic Models for API Payloads ---

class AgentDecisionPayload(BaseModel):
    """Represents the payload from the frontend for an agent's decision."""
    decision: str = Field(..., description="The decision made by the agent (e.g., 'approved', 'rejected').")


class DecisionResponse(BaseModel):
    """Confirmation response after a decision signal has been sent."""
    caseId: str
    status: str
    message: str


class CreateCasePayload(BaseModel):
    """Defines the required data to create a new case."""
    submitter_name: str = Field(..., description="The name of the person submitting the case.")
    submitter_email: str = Field(..., description="The email of the person submitting the case.")
    amount: float = Field(..., description="The primary amount related to the case (e.g., expense amount).")
    currency: str = Field(..., description="The currency code for the amount (e.g., 'USD').")
    category: str = Field(..., description="The category of the case (e.g., 'Travel', 'Software').")
    title: Optional[str] = Field(None, description="An optional title for the case.")


class CreateCaseResponse(BaseModel):
    """The response returned after successfully creating a case."""
    caseId: str
    status: str = "Workflow Started"


class ProviderState(BaseModel):
    """Represents the provider state payload from the Pact verifier."""
    consumer: str
    state: str


# --- API Endpoints ---


@app.post("/api/v1/cases", response_model=CreateCaseResponse, status_code=201, tags=["Cases"])
async def create_case(
    request: Request,
    payload: CreateCasePayload = Body(...)
):
    """
    Creates a new case by starting a new Temporal workflow execution.
    """
    client: Optional[Client] = request.app.state.temporal_client
    if not client:
        raise HTTPException(
            status_code=503,
            detail="Temporal client is not available.",
        )

    task_queue = os.getenv("TEMPORAL_CASES_TASK_QUEUE", "cases-task-queue")
    case_id = f"ER-{uuid.uuid4()}"
    process_name = "expense_approval"
    process_version = "1.0.0"

    try:
        await client.start_workflow(
            FlexibleCaseWorkflow.run,
            args=[process_name, process_version, payload.model_dump()],
            id=case_id,
            task_queue=task_queue,
        )
        log.info("Workflow started successfully", case_id=case_id)
        return CreateCaseResponse(caseId=case_id)
    except RPCError as e:
        log.error(
            "Temporal RPCError while starting workflow",
            case_id=case_id,
            error_message=e.message,
            error_status=e.status,
            error_details=e.details,
        )
        raise HTTPException(status_code=500, detail=f"Temporal error: {e.message}")
    except Exception as e:
        log.error("Generic failure to start workflow", case_id=case_id, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to start case workflow.")


@app.get("/api/v1/cases/{caseId}", response_model=CaseResponse, tags=["Cases"])
async def get_case_by_id(
    caseId: str = Path(..., description="The ID of the case to retrieve."),
    case_service: CaseService = Depends(get_case_service),
):
    """
    Retrieves the complete details for a single case by calling the core
    domain service.
    """
    case = await case_service.get_by_id(case_id=caseId)
    if case:
        return case
    raise HTTPException(status_code=404, detail=f"Case with ID '{caseId}' not found.")


@app.get("/", response_class=HTMLResponse, tags=["General"])
async def root():
    """
    Provides a simple HTML landing page with links to the documentation.
    """
    return """
    <html>
        <head><title>TraceRail Task Bridge</title></head>
        <body style="font-family: sans-serif; padding: 2em;">
            <h1>🌉 TraceRail Task Bridge</h1>
            <p>This service is running and ready to send signals to workflows.</p>
            <ul>
                <li><a href="/docs">API Documentation (Swagger UI)</a></li>
                <li><a href="/redoc">API Documentation (ReDoc)</a></li>
                <li><a href="/health">Health Check</a></li>
            </ul>
        </body>
    </html>
    """


@app.get("/health", tags=["General"])
async def health_check(request: Request):
    """
    Performs a health check on the application and its connection to Temporal.
    """
    if os.getenv("TESTING_MODE") == "true":
        return {"status": "healthy", "mode": "testing"}

    client: Optional[Client] = request.app.state.temporal_client
    is_connected = client is not None and not client.is_closed
    return {
        "status": "healthy" if is_connected else "unhealthy",
        "temporal_connected": is_connected,
        "temporal_host": client.target if client else "N/A",
        "temporal_namespace": client.namespace if client else "N/A",
        "timestamp": datetime.now().isoformat(),
    }


# --- Pact Provider State Setup ---
# This endpoint is used by the Pact verifier to set up provider states.
# It should not be used in a production environment.
@app.post("/_pact/provider_states", tags=["Pact"])
async def provider_states_handler(
    request: Request,
    payload: ProviderState = Body(...)
):
    """Sets up a specific provider state for Pact verification."""
    log.info("Received provider state setup request", state=payload.state, consumer=payload.consumer)
    client: Optional[Client] = request.app.state.temporal_client
    if not client:
        raise HTTPException(status_code=503, detail="Temporal client not available.")

    task_queue = os.getenv("TEMPORAL_CASES_TASK_QUEUE", "cases-task-queue")
    case_id = "ER-2024-08-124"

    if payload.state == "a case with ID ER-2024-08-124 exists":
        # Terminate workflow if it's already running to ensure a clean slate for the test.
        try:
            await client.get_workflow_handle(case_id).terminate(reason="Pact test setup")
            log.info("Terminated existing workflow for clean slate", case_id=case_id)
        except RPCError:
            pass  # It's okay if the workflow doesn't exist yet.

        # Start the workflow with the required state.
        workflow_payload = {
            "submitter_name": "John Doe",
            "submitter_email": "john.doe@example.com",
            "amount": 750.00,
            "currency": "USD",
            "category": "Travel",
            "title": "Expense Report from John Doe for $750.00",
        }
        await client.start_workflow(
            FlexibleCaseWorkflow.run,
            args=["expense_approval", "1.0.0", workflow_payload],
            id=case_id,
            task_queue=task_queue,
        )
        log.info("Provider state setup successful", state=payload.state)
        return {"result": "ok", "state": payload.state}

    log.warn("Provider state not recognized", state=payload.state)
    return {"result": "State not found", "state": payload.state}


@app.post("/api/v1/cases/{caseId}/decision", response_model=DecisionResponse, tags=["Cases"])
async def submit_decision(
    request: Request,
    caseId: str = Path(..., description="The ID of the case to submit a decision for."),
    payload: AgentDecisionPayload = Body(...),
):
    """
    Receives a decision from an agent and signals the corresponding workflow.
    """
    client: Optional[Client] = request.app.state.temporal_client
    if not client:
        raise HTTPException(
            status_code=503,
            detail="Temporal client is not available.",
        )

    try:
        # The workflow_id is the same as the caseId
        handle = client.get_workflow_handle(caseId)
        # Signal the workflow with the 'decision' signal name and the payload's decision value
        await handle.signal("decision", payload.decision)
        return DecisionResponse(
            caseId=caseId,
            status="Signal Sent",
            message=f"Decision '{payload.decision}' was successfully sent to the case.",
        )
    except RPCError as e:
        if e.status and e.status.name == 'NOT_FOUND':
            raise HTTPException(
                status_code=404,
                detail=f"Case with ID '{caseId}' not found or has already completed.",
            )
        raise HTTPException(
            status_code=500,
            detail=f"Temporal service error: {e.message}"
        ) from e
