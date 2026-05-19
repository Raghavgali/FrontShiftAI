"""Prometheus instrumentation + request correlation for the backend.

Two submodules:
    metrics — all counters, histograms, gauges (single source of truth).
    tracing — X-Request-ID generation + propagation via ContextVar.
"""
from . import metrics, tracing  # noqa: F401  (re-export for import ergonomics)
