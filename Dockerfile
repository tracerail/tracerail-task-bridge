# A robust, single-stage Dockerfile for a Poetry-managed FastAPI application.

# Use a slim, official Python base image.
FROM python:3.11-slim

# Set environment variables for best practices.
# - PYTHONDONTWRITEBYTECODE: Prevents Python from writing .pyc files.
# - PYTHONUNBUFFERED: Ensures Python output is sent straight to the terminal without buffering.
# - POETRY_VERSION: Pins the version of Poetry for reproducible builds.
# - POETRY_VIRTUALENVS_IN_PROJECT: Instructs Poetry to create the virtual environment inside the project directory (.venv).
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV POETRY_VERSION=1.8.2
ENV POETRY_VIRTUALENVS_IN_PROJECT=true

# Install Poetry system-wide.
RUN pip install "poetry==$POETRY_VERSION"

# Set the working directory for the application.
WORKDIR /app

# Copy the dependency definition files into the working directory.
# This is done first to leverage Docker layer caching. The build will be
# much faster on subsequent runs if these files haven't changed.
COPY pyproject.toml poetry.lock ./

# Install the project's dependencies.
# --no-dev: Excludes development dependencies like pytest and ruff.
# --no-interaction: Prevents poetry from asking for user input.
# --no-ansi: Produces clean, simple output for logs.
RUN poetry install --no-dev --no-interaction --no-ansi

# --- DEBUG: List the contents of the virtual environment's bin directory ---
RUN ls -l .venv/bin

# Copy the application source code into the container.
# This is done *after* installing dependencies to ensure that code changes
# do not invalidate the dependency cache layer.
COPY ./app ./app

# Expose the port that the Uvicorn server will run on inside the container.
# The default port for Uvicorn is 8000.
EXPOSE 8000

# Set the command to run when the container starts.
# 'poetry run' is the canonical way to execute a command within the context
# of the Poetry-managed virtual environment. It ensures that 'uvicorn'
# is found and executed correctly without needing to manipulate the system PATH.
# --host 0.0.0.0 is crucial to make the server accessible from outside the container.
CMD ["poetry", "run", "uvicorn", "app.bridge:app", "--host", "0.0.0.0", "--port", "8000"]
