# Grafana Provisioning

Phase 7 deliverables — all importable as-is into Grafana Cloud (or a
self-hosted Grafana). Nothing here assumes paid tier; everything fits
inside the Grafana Cloud free tier (10K metrics / 50 GB logs / 14-day
retention).

## Files

| File | Purpose |
|------|---------|
| `slo.yaml` | Service Level Objectives (voice p95, chat p95, per-tenant availability, retrieval/generation p95, session creation). Single source of truth for target values. |
| `alerts.yaml` | Prometheus-format alert rules derived from the SLOs plus operational alarms (circuit breaker open, DB pool saturation, tenant error spike). |
| `dashboards/service_health.json` | Four Golden Signals — traffic, errors, latency (p50/p95/p99), saturation. |
| `dashboards/voice_pipeline.json` | End-to-end voice turn latency + per-stage breakdown (VAD / STT / LLM TTFT / TTS TTFB). |
| `dashboards/rag_pipeline.json` | Retrieval vs generation latency, cache hit rate, LLM provider latency/failures. |
| `dashboards/tenants.json` | Per-company request rate, p95 latency, error rate, agent mix — for noisy-neighbor detection. |
| `dashboards/stress_test.json` | Live Locust load vs backend p95 — the knee on this chart is the sustainable throughput ceiling. |
| `dashboards/resilience.json` | Circuit breaker state (closed/half-open/open) and LLM provider health. |

## Import

Grafana Cloud → **Dashboards → Import → Upload JSON file** for each
dashboard. When prompted, pick your Prometheus datasource for the
`DS_PROMETHEUS` variable.

For alerts: **Alerting → Alert rules → Import** and paste the contents
of `alerts.yaml`.

## Scrape target setup (7D — external step)

The backend exposes `/metrics` on its existing HTTP port; the voice
worker exposes `/metrics` on `VOICE_METRICS_PORT` (default 9100) inside
each Modal container.

To wire up Grafana Cloud:

1. In Grafana Cloud, create an **API key** with `MetricsPublisher` scope
   and copy the **Prometheus remote_write URL**.
2. Run Grafana Agent alongside the backend (`grafana-agent-flow` or
   sidecar) with scrape targets pointing at `backend:8000/metrics` and
   per-worker voice metrics endpoints, plus `remote_write` pointing at
   the Grafana Cloud URL from step 1.
3. Example `agent.river` snippet:

   ```hcl
   prometheus.scrape "fsai" {
     targets = [
       { "__address__" = "backend:8000", "__metrics_path__" = "/metrics" },
       { "__address__" = "voice-worker:9100" },
       { "__address__" = "locust:9646" },    # optional — see stress_tests/locustfile.py
     ]
     forward_to = [prometheus.remote_write.grafana_cloud.receiver]
     scrape_interval = "15s"
   }

   prometheus.remote_write "grafana_cloud" {
     endpoint {
       url = env("GRAFANA_CLOUD_PROM_URL")
       basic_auth {
         username = env("GRAFANA_CLOUD_USER")
         password = env("GRAFANA_CLOUD_API_KEY")
       }
     }
   }
   ```

4. Verify the metrics arrive by running:
   `curl $GRAFANA_CLOUD_PROM_URL/api/v1/query?query=http_requests_total`
   or by opening the `service_health` dashboard.

## SLO enforcement in CI

`stress_tests/test_phase7_observability.py` (follow-up) will assert:

- `/metrics` returns valid Prometheus exposition format.
- Every declared instrument appears in at least one dashboard target.
- No high-cardinality labels (`user_id`, `request_id`, ...) leaked in.
