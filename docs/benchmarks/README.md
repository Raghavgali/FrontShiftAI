# Benchmarks

Recorded evidence for the latency and resilience claims in the roadmap. A
phase's performance work is only "validated" once a run is recorded here;
until then the README describes it as "implemented", not "production
validated".

## How a benchmark run is produced

1. Deploy the backend (or point at a running instance).
2. Run the gated workflow **Integration & Stress Tests** (Actions tab,
   `workflow_dispatch`), or locally:

   ```bash
   STRESS_TEST_BACKEND_URL=<url> STRESS_TEST_JWT=<token> \
     pytest stress_tests/ -v -s 2>&1 | tee run.log
   ```

3. Copy `TEMPLATE.md` to `YYYY-MM-DD-<scope>.md` (for example
   `2026-07-20-phase1-quick-wins.md`), fill in every field from the run
   output, and commit it together with a link to the workflow run.

## Rules

- Never edit a recorded benchmark after the fact; record a new run instead.
- Every entry must state date, commit, environment, sample size, and
  pass/fail against the targets defined in `plan.md`.
- Failed runs are recorded too. A failed benchmark with a follow-up fix is
  stronger evidence than a missing one.

## Recorded runs

| Date | Scope | Commit | Result | File |
|------|-------|--------|--------|------|
| (none recorded yet) | | | | |
