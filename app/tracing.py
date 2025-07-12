# app/tracing.py
import os
from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

def setup_tracing(service_name: str):
    """
    Configures and enables OpenTelemetry tracing for the application.
    This function sets up a tracer that exports spans to a Jaeger collector
    via the OTLP gRPC protocol.
    """
    # Create a resource to identify our service in Jaeger/OpenTelemetry.
    resource = Resource(attributes={
        SERVICE_NAME: service_name
    })

    # Create a TracerProvider, which is the cornerstone of the OpenTelemetry SDK.
    provider = TracerProvider(resource=resource)

    # Configure the OTLP exporter. This is the component that sends the trace
    # data to the collector (in our case, Jaeger).
    # The endpoint must match the gRPC receiver of the Jaeger container.
    # We use an environment variable to make this configurable.
    jaeger_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "jaeger:4317")
    otlp_exporter = OTLPSpanExporter(
        endpoint=jaeger_endpoint,
        insecure=True  # Use insecure connection for local development.
    )

    # Use a BatchSpanProcessor to group spans together and send them in batches
    # for better performance.
    span_processor = BatchSpanProcessor(otlp_exporter)
    provider.add_span_processor(span_processor)

    # Set our configured provider as the global tracer provider.
    # From now on, any call to trace.get_tracer(__name__) will use this provider.
    trace.set_tracer_provider(provider)

    print(f"✅ OpenTelemetry tracing configured for '{service_name}', exporting to {jaeger_endpoint}")
