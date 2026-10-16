"""Bounded-cardinality HTTP metrics and metadata-only OpenTelemetry spans."""

import time

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest

from app.core.config import Settings


class Telemetry:
    def __init__(self, settings: Settings):
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "leaseguard_http_requests_total",
            "HTTP responses",
            ["method", "route", "status"],
            registry=self.registry,
        )
        self.latency = Histogram(
            "leaseguard_http_duration_seconds",
            "HTTP response latency",
            ["method", "route"],
            registry=self.registry,
        )
        self.provider = TracerProvider(resource=Resource.create({"service.name": "leaseguard-api"}))
        if settings.otel_enabled:
            self.provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_endpoint, timeout=5))
            )
        self.tracer = self.provider.get_tracer("leaseguard.http")

    def render(self) -> bytes:
        return generate_latest(self.registry)

    def observe(self, method: str, route: str, status: int, seconds: float) -> None:
        method = (
            method
            if method in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}
            else "OTHER"
        )
        self.requests.labels(method, route, str(status)).inc()
        self.latency.labels(method, route).observe(seconds)
        end = time.time_ns()
        span = self.tracer.start_span("http.request", start_time=end - int(seconds * 1_000_000_000))
        span.set_attribute("http.request.method", method)
        span.set_attribute("http.route", route)
        span.set_attribute("http.response.status_code", status)
        span.end(end_time=end)

    def close(self) -> None:
        self.provider.shutdown()
