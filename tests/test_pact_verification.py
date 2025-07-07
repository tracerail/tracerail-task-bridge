import anyio
import asyncio
import os
import pytest
import uvicorn
from multiprocessing import Process
from pathlib import Path

from pact import Verifier
from temporalio.client import Client
from temporalio.worker import Worker

# Import the application and workflow code we need to test
from app.bridge import app
from tracerail.workflows.flexible_case_workflow import FlexibleCaseWorkflow


# --- Test Constants ---
HOST = "127.0.0.1"
API_PORT = 8000
TEMPORAL_HOST = os.getenv("TEMPORAL_HOST", "localhost")
TEMPORAL_PORT = int(os.getenv("TEMPORAL_PORT", 7233))
TEMPORAL_NAMESPACE = os.getenv("TEMPORAL_NAMESPACE", "default")
TASK_QUEUE = os.getenv("TEMPORAL_CASES_TASK_QUEUE", "cases-task-queue")
PACT_CASE_ID = "ER-2024-08-124"


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


# --- The Verification Test ---

@pytest.mark.anyio
async def test_api_honors_pact_contract(request):
    """
    This is a full end-to-end provider verification test. It orchestrates all
    necessary components (worker, server, workflow) to verify the API against
    the frontend's contract.
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
        # 2. SETUP: Connect to Temporal and start the specific workflow for the test
        try:
            temporal_client = await Client.connect(
                f"{TEMPORAL_HOST}:{TEMPORAL_PORT}", namespace=TEMPORAL_NAMESPACE
            )
        except RuntimeError as e:
            if "Connection refused" in str(e):
                pytest.skip("Temporal dev server not available. Skipping test.")
            raise

        workflow_handle = await temporal_client.start_workflow(
            FlexibleCaseWorkflow.run,
            args=["expense_approval", "1.0.0", {}],
            id=PACT_CASE_ID,
            task_queue=TASK_QUEUE,
        )
        await anyio.sleep(0.5)

        # 3. VERIFICATION: Locate pact file and run the verifier
        root_dir = Path(request.config.rootdir)
        pact_file = root_dir / "pacts" / "TracerailActionCenter-TracerailAPI.json"
        if not pact_file.exists():
            pytest.fail(f"Pact file not found at expected path: {pact_file}")

        verifier = Verifier(
            provider="TracerailAPI",
            provider_base_url=f"http://{HOST}:{API_PORT}",
        )
        success, logs = verifier.verify_pacts(str(pact_file))

        # 4. ASSERTION: Check the result
        if not success:
            print("\n" + "=" * 80)
            print("PACT VERIFICATION FAILED. LOGS:")
            print("=" * 80)
            for log in logs:
                print(log)
            print("=" * 80 + "\n")
        assert success, "Pact verification failed. See logs for details."

    finally:
        # 5. TEARDOWN: Clean up all resources
        if workflow_handle:
            await workflow_handle.terminate(reason="Test completed")
        if temporal_client:
            await temporal_client.close()
        if worker_process.is_alive():
            worker_process.terminate()
        if api_server_process.is_alive():
            api_server_process.terminate()
