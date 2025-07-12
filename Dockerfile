# A simple, single-stage Dockerfile that correctly handles the local path dependency.
# This approach is common for monorepos or closely-related projects where services are developed together.
#
# NOTE: This Dockerfile assumes the build context is the root of the monorepo
# (e.g., the parent directory of 'tracerail-bootstrap', 'tracerail-core', etc.).
# This is configured in the docker-compose.yml file with 'context: ..'.

# Use a slim, official Python base image.
FROM python:3.11-slim

# Set environment variables for best practices.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VERSION=1.8.2 \
    # Tell Poetry to create the virtual environment inside the project directory.
    # This makes the environment's path predictable and easier to work with.
    POETRY_VIRTUALENVS_IN_PROJECT=true

# Install Poetry system-wide.
RUN pip install "poetry==$POETRY_VERSION"

# Set a general working directory for the application build.
WORKDIR /app

# Copy both projects into the build context.
# The paths are relative to the build context set in docker-compose.yml.
COPY tracerail-core/ ./tracerail-core/
COPY tracerail-task-bridge/ ./tracerail-task-bridge/

# Set the working directory to the main application's folder.
# This is the crucial step that makes the local path dependency work.
WORKDIR /app/tracerail-task-bridge

# Install dependencies for the bridge project.
# From this working directory, Poetry can correctly resolve the relative path
# `../tracerail-core` in pyproject.toml to `/app/tracerail-core`.
# We use `--only main` to exclude development dependencies like pytest.
RUN poetry install --only main

# Expose the port that the Uvicorn server will run on inside the container.
EXPOSE 8000

# Set the command to run when the container starts.
# 'poetry run' executes the command within the Poetry-managed virtual environment.
# --host 0.0.0.0 is crucial to make the server accessible from outside the container.
CMD ["poetry", "run", "uvicorn", "app.bridge:app", "--host", "0.0.0.0", "--port", "8000"]
