# Makefile for tracerail-task-bridge
# Provides convenient shortcuts for common development tasks.

# By default, running 'make' will show the help message.
.DEFAULT_GOAL := help

# Declare targets that are not actual files.
.PHONY: help test test-pact

help:
	@echo "Available commands for tracerail-task-bridge:"
	@echo "----------------------------------------------"
	@echo "  make help          - Show this help message."
	@echo "  make test          - Run all Python tests."
	@echo "  make test-pact     - Run only the Pact contract verification test."

test:
	@echo "🐍 Running all Python tests..."
	@poetry run pytest

test-pact:
	@echo "🤝 Running Pact contract verification test..."
	@TESTING_MODE=true poetry run pytest tests/test_pact_verification.py
