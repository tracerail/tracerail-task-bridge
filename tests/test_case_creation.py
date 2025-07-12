import anyio
import asyncio
import os
import pytest
import httpx
import uvicorn
from multiprocessing import Process

from temporalio.client import Client
from temporalio.worker import Worker
from temporalio.service import RPCError

# Import the application and workflow code we need to test
from app.bridge import app
from tracerail.workflows.flexible_case_workflow import FlexibleCaseWorkflow
from tracerail.domain.cases import Case

# --- Test Constants ---
HOST = "127.0.0.1"
API_PORT = 8001  # Use a different port to avoid conflicts with other tests
TEMPORAL_HOST = os.getenv("TEMPORAL_HOST", "localhost")
TEMPORAL_PORT = int(os.getenv("TEMPORAL_PORT", 7233))
TEMPORAL_NAMESPACE = os.getenv("TEMPORAL_NAMESPACE", "default")
TASK_QUEUE = os.getenv("TEMPORAL_CASES_TASK_QUEUE", "cases-task-queue")


# --- Helper functions to run servers in separate processes ---

def run_worker_process():
    """Target function for the worker process. Creates its own client and loop."""
    async def _run():
        client = await Client.connect(
            f"{TEMPORAL_HOST}:{TEMPORAL_PORT}", namespace=TEMPORAL_NAMESPACE
        )
        await Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=[FlexibleCaseWorkflow],
        ).run()
    asyncio.run(_run())


def run_api_server_process():
    """Target function for the FastAPI server process."""
    uvicorn.run(app, host=HOST, port=API_PORT, log_level="warning")


# --- Pytest Fixture to Force Asyncio Backend ---

@pytest.fixture(scope='session')
def anyio_backend():
    """
    Forces pytest-anyio to use the 'asyncio' backend for all tests in this session,
    preventing it from trying to run tests with 'trio'.
    """
    return 'asyncio'


# --- The Integration Test ---

@pytest.mark.anyio
async def test_create_case_endpoint():
    """
    This is an integration test for the case creation API endpoint.
    It verifies that a POST request successfully starts a Temporal workflow
    with the correct initial state.
    """
    # 1. SETUP: Start background services
    worker_process = Process(target=run_worker_process, daemon=True)
    api_server_process = Process(target=run_api_server_process, daemon=True)

    worker_process.start()
    api_server_process.start()

    # Give the servers a moment to initialize
    await anyio.sleep(2)

    temporal_client = None
    workflow_handle = None
    try:
        # 2. SETUP: Connect to Temporal for verification
        try:
            temporal_client = await Client.connect(
                f"{TEMPORAL_HOST}:{TEMPORAL_PORT}", namespace=TEMPORAL_NAMESPACE
            )
        except RuntimeError as e:
            pytest.skip(f"Could not connect to Temporal: {e}")

        # 3. EXECUTION: Make the API call to create a new case
        api_base_url = f"http://{HOST}:{API_PORT}"
        payload = {
            "submitter_name": "Alice",
            "submitter_email": "alice@example.com",
            "amount": 123.45,
            "currency": "USD",
            "category": "Office Supplies",
            "title": "New Keyboard and Mouse",
        }

        async with httpx.AsyncClient(base_url=api_base_url) as http_client:
            response = await http_client.post("/api/v1/cases", json=payload)

        # 4. ASSERTION: Verify the API response
        assert response.status_code == 201, f"Expected 201 Created, got {response.status_code}"
        response_data = response.json()
        assert "caseId" in response_data
        case_id = response_data["caseId"]
        assert case_id is not None
        print(f"API successfully created case with ID: {case_id}")

        # 5. VERIFICATION: Check the workflow state in Temporal
        workflow_handle = temporal_client.get_workflow_handle(case_id)

        # Give the workflow a moment to start and initialize its state
        await anyio.sleep(1)

        queried_state_dict = await workflow_handle.query("get_current_state")
        queried_state = Case.model_validate(queried_state_dict)

        assert queried_state is not None
        assert queried_state.caseDetails.caseId == case_id
        assert queried_state.caseDetails.caseTitle == payload["title"]
        assert queried_state.caseDetails.submitter.name == payload["submitter_name"]
        assert queried_state.caseDetails.caseData.amount == payload["amount"]
        print(f"Successfully verified workflow state for case: {case_id}")

    except httpx.ConnectError as e:
        pytest.fail(f"Could not connect to the API server at {api_base_url}. Is it running? Error: {e}")

    except RPCError as e:
        # This test should not create a workflow that already exists.
        if e.status and e.status.name == 'ALREADY_EXISTS':
            pytest.fail("WorkflowAlreadyExistsError should not happen in this test.")
        # For other RPC errors, fail the test with the error message.
        pytest.fail(f"An unexpected Temporal RPCError occurred: {e.message}")

    finally:
        # 6. TEARDOWN: Clean up all resources
        if workflow_handle:
            try:
                await workflow_handle.terminate(reason="Test completed")
                print(f"Terminated workflow: {workflow_handle.id}")
            except Exception as e:
                print(f"Warning: could not terminate workflow {workflow_handle.id}. It may have already completed. Error: {e}")

        if worker_process.is_alive():
            worker_process.terminate()
        if api_server_process.is_alive():
            api_server_process.terminate()
        print("Cleaned up background processes.")
