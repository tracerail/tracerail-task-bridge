import sys
import os
import re
import anyio
import asyncio
import pytest
import uvicorn
from multiprocessing import Process
from pathlib import Path

# This is the crucial part to fix the ModuleNotFoundError.
# We add the 'src' directory to the Python path so that pytest can find the 'app' module.
SRC_PATH = str(Path(__file__).parent.parent / "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

from fastapi import APIRouter
from pydantic import BaseModel
from pact import Verifier
from temporalio.client import Client
from temporalio.worker import Worker

# Now that the path is set, these imports should work correctly.
from app.bridge import app
from tracerail.workflows.flexible_case_workflow import FlexibleCaseWorkflow


# --- Test Constants ---
HOST = "127.0.0.1"
API_PORT = 8000
TEMPORAL_HOST = os.getenv("TEMPORAL_HOST", "localhost")
TEMPORAL_PORT = int(os.getenv("TEMPORAL_PORT", 7233))
TEMPORAL_NAMESPACE = os.getenv("TEMPORAL_NAMESPACE", "default")
TASK_QUEUE = os.getenv("TEMPORAL_CASES_TASK_QUEUE", "pact-verification-task-queue")


# --- Provider State Setup ---
# This endpoint is called by the Pact verifier to set up the provider state.

pact_router = APIRouter()

class ProviderState(BaseModel):
    """A Pydantic model for the provider state POST body."""
    consumer: str
    state: str

async def _start_workflow_for_state(state: str):
    """A helper to parse the state string and start the necessary workflow."""
    # This regex is designed to be flexible and extract the case ID
    # from the state string defined in the consumer's Pact test.
    state_pattern = re.compile(
        r"case with ID ([\w-]+) (?:exists|is ready for a decision)"
    )
    match = state_pattern.search(state)

    if not match:
        print(f"Could not parse case ID from provider state: '{state}'")
        return

    case_id = match.group(1)
    print(f"Setting up state for case ID: {case_id}")

    try:
        client = await Client.connect(f"{TEMPORAL_HOST}:{TEMPORAL_PORT}", namespace=TEMPORAL_NAMESPACE)
        await client.start_workflow(
            FlexibleCaseWorkflow.run,
            args=["expense_approval", "1.0.0", {"submitter_name": "Pact Test"}],
            id=case_id,
            task_queue=TASK_QUEUE,
            id_reuse_policy="TerminateIfRunning",
        )
        print(f"Successfully started workflow for case ID: {case_id}")
        await anyio.sleep(0.5)
    except Exception as e:
        print(f"Error setting up provider state: {e}")
        raise

@pact_router.post("/_pact/provider_states")
async def provider_states(state: ProviderState):
    """Pact verifier calls this endpoint to set up a given state."""
    print(f"Received provider state setup request: {state.state}")
    await _start_workflow_for_state(state.state)
    return {"result": "ok"}

# The main app needs to include this router for the verifier to access it.
app.include_router(pact_router)


# --- Test Server and Worker Setup ---

def run_worker_process():
    """Target function to run the Temporal worker in a separate process."""
    async def _run():
        client = await Client.connect(f"{TEMPORAL_HOST}:{TEMPORAL_PORT}", namespace=TEMPORAL_NAMESPACE)
        await Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=[FlexibleCaseWorkflow],
        ).run()
    asyncio.run(_run())

def run_api_server_process():
    """Target function to run the FastAPI server in a separate process."""
    uvicorn.run("app.bridge:app", host=HOST, port=API_PORT, log_level="warning", reload=False)


# --- The Test Case ---

@pytest.mark.asyncio
async def test_api_honors_pact_contract(request):
    """
    Verifies the FastAPI provider against the contract from the frontend consumer.
    This version uses the correct pact-python v2 API.
    """
    worker_process = Process(target=run_worker_process, daemon=True)
    api_server_process = Process(target=run_api_server_process, daemon=True)

    try:
        # Start the background services
        worker_process.start()
        api_server_process.start()
        await anyio.sleep(3)  # Give servers a moment to initialize

        # Correctly instantiate the Verifier for pact-python v2
        verifier = Verifier(
            provider="TracerailAPI",
            provider_base_url=f"http://{HOST}:{API_PORT}"
        )

        # Define the location of the contract file
        root_dir = Path(request.config.rootdir)
        pact_file = root_dir.parent / "tracerail-action-center" / "pacts" / "TracerailActionCenter-TracerailAPI.json"

        if not pact_file.exists():
            pytest.fail(f"Pact file not found at: {pact_file}")

        # Run the verification
        success, logs = verifier.verify_pacts(
            str(pact_file),
            provider_states_setup_url=f"http://{HOST}:{API_PORT}/_pact/provider_states",
        )

        # Assert the result
        if not success:
            print("\n" + "=" * 80)
            print("PACT VERIFICATION FAILED. LOGS:")
            print("=" * 80)
            for log in logs:
                print(log)
            print("=" * 80 + "\n")
        assert success, "Pact verification failed. See logs for details."

    finally:
        # Terminate the background processes
        if worker_process.is_alive():
            worker_process.terminate()
        if api_server_process.is_alive():
            api_server_process.terminate()
