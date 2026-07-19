# Benchmark: <scope, e.g. Phase 1 quick wins>

- **Date (UTC):** YYYY-MM-DD
- **Commit:** <short sha>
- **Environment:** <e.g. Cloud Run us-central1, 1 vCPU / 512Mi, min-instances 0>
- **Client:** <e.g. GitHub-hosted runner / local M-series Mac, same region or not>
- **Generation backend:** <groq / mercury, model name>
- **Sample size:** <iterations per test>
- **Workflow run:** <link to Actions run, if applicable>

## Results

| Test | Metric | Target | Measured | Pass |
|------|--------|--------|----------|------|
| e.g. test_phase1_combined | p50 | <= 1.0s | | |
| e.g. test_phase1_combined | p95 | <= 1.8s | | |
| e.g. test_streaming_ttft | TTFT p50 | <= 0.6s | | |

## Notes

<cold starts observed, retries, anomalies, anything that qualifies the numbers>
