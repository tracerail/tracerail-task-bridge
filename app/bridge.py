"""
TraceRail Task Bridge - FastAPI Application (v2)

This service acts as a bridge between human users/external systems and
running Temporal workflows. It uses FastAPI's lifespan management to maintain
a single, persistent connection to the Temporal service for efficiency and
robustness.
"""

import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional, Dict, Any, List

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from temporalio.client import Client
from temporalio.service import RPCError


# --- Application Lifespan Management ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manages the Temporal client's lifecycle with the FastAPI application.
    The client is created on startup and is available for all requests.
    """
    host = os.getenv("TEMPORAL_HOST", "localhost")
    port = int(os.getenv("TEMPORAL_PORT", 7233))
    namespace = os.getenv("TEMPORAL_NAMESPACE", "default")
    target = f"{host}:{port}"

    print(f"Connecting to Temporal service at {target}...")
    try:
        client = await Client.connect(target, namespace=namespace)
        app.state.temporal_client = client
        print("✅ Successfully connected to Temporal.")
        yield
    finally:
        if "temporal_client" in app.state and app.state.temporal_client:
            await app.state.temporal_client.close()
            print("🔌 Temporal client connection closed.")


# --- FastAPI Application Setup ---

app = FastAPI(
    title="TraceRail Task Bridge",
    description="A bridge service for sending signals to human-in-the-loop workflows.",
    version="2.0.0",
    lifespan=lifespan,
)


# --- Pydantic Models for API Payloads ---

class Decision(BaseModel):
    """
    Represents the payload for sending a decision to a workflow.
    """
    workflow_id: str = Field(..., description="The ID of the Temporal workflow to signal.")
    status: str = Field(..., description="The decision status (e.g., 'approved', 'rejected').")
    reviewer: str = Field(..., description="The name or ID of the person who made the decision.")
    comments: Optional[str] = Field(None, description="Optional comments from the reviewer.")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Additional metadata.")


class WorkflowSignalInfo(BaseModel):
    """
    Response model after successfully sending a signal.
    """
    workflow_id: str
    signal_name: str
    signal_sent: bool = True
    timestamp: datetime


# --- API Endpoints ---

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
    client: Optional[Client] = request.app.state.temporal_client
    is_connected = client is not None and not client.is_closed
    return {
        "status": "healthy" if is_connected else "unhealthy",
        "temporal_connected": is_connected,
        "temporal_host": client.target if client else "N/A",
        "temporal_namespace": client.namespace if client else "N/A",
        "timestamp": datetime.now().isoformat(),
    }


@app.post("/decision", response_model=WorkflowSignalInfo, tags=["Workflows"])
async def post_decision(request: Request, payload: Decision):
    """
    Receives a decision and signals the corresponding running workflow.
    """
    client: Client = request.app.state.temporal_client
    signal_name = "decision"
    try:
        handle = client.get_workflow_handle(payload.workflow_id)
        await handle.signal(signal_name, payload.status)
        return WorkflowSignalInfo(
            workflow_id=payload.workflow_id,
            signal_name=signal_name,
            timestamp=datetime.now(),
        )
    except RPCError as e:
        if e.status and e.status.name == 'NOT_FOUND':
            raise HTTPException(
                status_code=404,
                detail=f"Workflow with ID '{payload.workflow_id}' not found or has already completed.",
            )
        raise HTTPException(status_code=500, detail=f"A Temporal service error occurred: {e.message}") from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")


@app.get("/workflows", tags=["Workflows"])
async def list_workflows(
    request: Request,
    query: str = Query("WorkflowType = 'ExampleWorkflow'", description="Temporal list workflow query string."),
    limit: int = Query(50, ge=1, le=1000, description="Maximum number of workflows to return."),
) -> List[Dict[str, Any]]:
    """
    Lists recent workflows from the Temporal service.
    """
    client: Client = request.app.state.temporal_client
    try:
        workflows = []
        async for workflow in client.list_workflows(query=query):
            workflows.append(
                {
                    "workflow_id": workflow.id,
                    "run_id": workflow.run_id,
                    "workflow_type": workflow.workflow_type,
                    "status": workflow.status.name,
                    "start_time": workflow.start_time,
                    "close_time": workflow.close_time,
                }
            )
            if len(workflows) >= limit:
                break
        return workflows
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list workflows: {str(e)}")
