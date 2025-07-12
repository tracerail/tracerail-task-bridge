import pytest
from httpx import AsyncClient
from prometheus_client.parser import text_string_to_metric_families

from app.bridge import app

# --- Test Case ---
@pytest.mark.anyio
async def test_metrics_endpoint_produces_latency_metric():
    """
    Tests that the /metrics endpoint, when configured, produces the
    necessary latency histogram metric.

    This test runs the FastAPI app directly with an async client,
    avoiding the need for a separate running server process.
    """
    # Set the TESTING_MODE environment variable to avoid the Temporal connection,
    # which is not needed for this specific test.
    import os
    os.environ["TESTING_MODE"] = "true"

    async with AsyncClient(app=app, base_url="http://test") as client:
        # Make a request to a valid endpoint to generate some metrics
        response = await client.get("/")
        assert response.status_code == 200

        # Now, scrape the /metrics endpoint
        metrics_response = await client.get("/metrics")
        assert metrics_response.status_code == 200

        # Parse the text response into Prometheus metric families
        metrics_text = metrics_response.text
        latency_metric_found = False

        # The default latency metric is http_request_duration_seconds
        for family in text_string_to_metric_families(metrics_text):
            if family.name == "http_request_duration_seconds":
                latency_metric_found = True
                # Check that it's a histogram (has _bucket samples)
                assert any("_bucket" in sample.name for sample in family.samples)
                break

        assert latency_metric_found, "The http_request_duration_seconds histogram was not found in the /metrics output."

    # Unset the environment variable to avoid side effects in other tests
    del os.environ["TESTING_MODE"]
