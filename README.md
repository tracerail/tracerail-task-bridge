# TraceRail Task Bridge

[![Build Status](https://img.shields.io/github/actions/workflow/status/tracerail/tracerail-task-bridge/main.yml?branch=main)](https://github.com/tracerail/tracerail-task-bridge/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

The **TraceRail Task Bridge** is a lightweight FastAPI service that acts as an HTTP interface to the Temporal workflow engine. Its primary purpose is to provide a simple, stateless API for external systems or user interfaces to interact with running workflows, specifically for human-in-the-loop (HITL) scenarios.

---

## Overview

In a complex AI workflow system like TraceRail, some steps require human intervention. When a workflow reaches one of these steps, it pauses and waits for an external signal to continue. The Task Bridge provides the endpoints to send those signals.

This service is a core component of the [TraceRail Bootstrap](https://github.com/tracerail/tracerail-bootstrap) stack.

### Key Responsibilities

*   **Signal Workflows**: Provides a `/decision` endpoint to send a result (e.g., "approved", "rejected") to a specific waiting workflow.
*   **List Workflows**: Provides a `/workflows` endpoint to query and view the status of recent workflow executions.
*   **Health Checks**: A `/health` endpoint to verify connectivity to the underlying Temporal service.

---

## API Endpoints

The service exposes a simple RESTful API. The full interactive documentation is available via Swagger UI when the service is running.

*   **`GET /docs`**: Interactive Swagger UI for the API.
*   **`GET /health`**: Checks the health of the service and its connection to Temporal.
*   **`GET /workflows`**: Lists recent workflows. Supports filtering via a Temporal query string.
*   **`POST /decision`**: Sends a decision signal to a specified `workflow_id`.

---

## Getting Started

### Running with Docker Compose (Recommended)

The easiest way to run the Task Bridge is as part of the `tracerail-bootstrap` stack. The `docker-compose.yml` file in that repository is configured to build and run this service.

1.  Navigate to the `tracerail-bootstrap` directory.
2.  Run `make up`.
3.  The service will be available at **http://localhost:7070**.

### Running Standalone for Development

You can also run the service locally for development or testing.

1.  **Prerequisites**:
    *   Python 3.11+
    *   Poetry
    *   A running Temporal service.

2.  **Install Dependencies**:
    ```bash
    poetry install
    ```

3.  **Configure Environment**:
    Create a `.env` file in the root of this repository with the location of your Temporal service:
    ```dotenv
    # .env
    TEMPORAL_HOST=localhost
    TEMPORAL_PORT=7233
    ```

4.  **Run the Server**:
    The service will be started with `uvicorn`.
    ```bash
    poetry run uvicorn app.bridge:app --reload
    ```
    The API will be available at **http://localhost:8000**.

---

## Development

*   **Linter**: `poetry run ruff check .`
*   **Formatter**: `poetry run black .`
*   **Tests**: `poetry run pytest` (requires a running Temporal instance)
