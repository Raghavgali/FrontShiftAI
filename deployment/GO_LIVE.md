# Going live: `deployment/go_live.sh`

An interactive wizard that walks you through the parts of taking FrontShiftAI
live that only a human can do: clicking through consoles, pasting API keys, and
approving things that cost money. Everything it can do for you with `gcloud`,
`gh` and `modal` it does.

```bash
./deployment/go_live.sh --dry-run   # see the whole plan, change nothing
./deployment/go_live.sh             # do it
```

It is idempotent and resumable. You are not expected to finish in one sitting.
Ctrl-C at any point, come back later, run it again: finished stages are skipped
and cloud resources that already exist are detected rather than recreated.

## What you end up with

| Piece | Where it runs | Deployed by |
| --- | --- | --- |
| Backend API | Cloud Run, in a NEW GCP project you own | `.github/workflows/deploy-backend.yml` |
| Database | Neon serverless Postgres, free plan | you, in the browser |
| Landing page | GitHub Pages at `/FrontShiftAI/` | `.github/workflows/deploy-pages.yml` |
| Chat app | GitHub Pages at `/FrontShiftAI/app/` | `.github/workflows/deploy-pages.yml` |
| Voice agent | Modal, plus LiveKit Cloud | `modal deploy` |

## What you need before you start

Installed and authenticated:

- `gcloud`, logged in with `gcloud auth login`. The wizard makes one real API
  call to check this, because `gcloud auth list` reads only local config and
  will happily show an active account whose token expired months ago.
- `gh`, logged in with the `repo` and `workflow` scopes:
  `gh auth login --scopes repo,workflow`
- `modal`, for the voice stages only: `pip install modal && modal token new`
- `curl`, `git`, and either `openssl` or `python3` for generating secrets.

You do NOT need Docker locally. The backend image is built inside the GitHub
Actions runner.

Accounts you will be asked to create or log into: Google Cloud (with billing),
Neon, Groq, Brave Search, Inception Labs (Mercury), LiveKit Cloud, Deepgram,
OpenAI, and optionally AssemblyAI, Cartesia and Weights and Biases.

## Options

| Flag | Effect |
| --- | --- |
| `--dry-run` | Print every mutating command instead of running it. Read-only probes still run, so the plan reflects what actually exists. Writes nothing, not even the state file. |
| `--status` | Print the state file and exit. |
| `--force` | Re-run every stage, ignoring the state file. |
| `--force-stage NAME` | Re-run one stage. Repeatable. Unknown names are rejected. |
| `--reset` | Delete the state file. Touches no cloud resource, so the next run re-detects everything. |
| `-h`, `--help` | Usage. |

## The twelve stages

1. **PREFLIGHT** Tool and auth checks. Anything missing prints the install or
   login command and stops before configuring anything.
2. **GCP_PROJECT** Creates a new project (id validated, the old `frontshiftai`
   and `frontshiftai-deploy` ids refused), links billing, enables the APIs, and
   creates the Artifact Registry repo the workflow pushes to.
3. **WIF** Deploy service account, workload identity pool, OIDC provider pinned
   to `Raghavgali/FrontShiftAI`, the `roles/iam.workloadIdentityUser` binding,
   and the project roles. Prints the provider resource name.
4. **NEON** Browser-only. Validates the connection string you paste, offers a
   connectivity test, and stores it as the `DATABASE_URL` secret.
5. **APP_SECRETS** Generates a fresh `JWT_SECRET_KEY`, collects the API keys,
   creates every Secret Manager entry the deploy mounts, and grants the Cloud
   Run runtime service account read access to each one.
6. **SEED_SECRETS** The four `SEED_*` passwords, plus the blocker below.
7. **GITHUB_CONFIG** The three repo secrets, and the `DEPLOY_BACKEND_ENABLED`
   gate (set to `false` at this point).
8. **BACKEND_DEPLOY** Flips the gate to `true`, dispatches the deploy, polls the
   run, resolves the Cloud Run URL, probes `/health/ready`.
9. **PAGES_DEPLOY** Sets `BACKEND_URL` and rebuilds Pages so the chat app talks
   to the real backend instead of `localhost:8000`.
10. **VOICE_KEYS** LiveKit and the provider keys, written into the three Modal
    secrets `livekit-credentials`, `voice-agent-providers` and
    `voice-agent-backend`.
11. **MODAL_DEPLOY** `modal deploy`, then `VOICE_API_URL` and another Pages
    rebuild.
12. **VERIFY** URL summary, automated health checks, and a click-through list.

## Everything it sets, and where

### Google Secret Manager

The five the deploy workflow mounts through `--set-secrets`. All five must
exist or `gcloud run deploy` fails outright, so the wizard will create one with
a placeholder value rather than leave a gap.

| Secret | Read by |
| --- | --- |
| `DATABASE_URL` | `backend/db/connection.py`, required in production |
| `JWT_SECRET_KEY` | `backend/api/auth.py`, raises at import if unset |
| `GROQ_API_KEY` | agents' primary LLM, and the RAG generator's fallback |
| `INCEPTION_API_KEY` | `chat_pipeline/rag/generator.py`, the Mercury path |
| `BRAVE_API_KEY` | web search and extraction |

Also created, but NOT mounted by the workflow as it stands:

| Secret | Why |
| --- | --- |
| `SEED_SUPER_ADMIN_PASSWORD` | see the blocker below |
| `SEED_ADMIN_PASSWORD` | see the blocker below |
| `SEED_USER_PASSWORD` | see the blocker below |
| `SEED_DEMO_PASSWORD` | see the blocker below |
| `MERCURY_API_URL` | optional, see the Mercury naming mismatch below |
| `MERCURY_API_KEY` | optional, see the Mercury naming mismatch below |

### GitHub repository secrets

| Secret | Used by |
| --- | --- |
| `GCP_PROJECT_ID` | `deploy-backend.yml`, `env.PROJECT_ID` |
| `GCP_SERVICE_ACCOUNT` | `deploy-backend.yml`, `google-github-actions/auth` |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | `deploy-backend.yml`, `google-github-actions/auth` |

### GitHub repository variables

| Variable | Used by | Note |
| --- | --- | --- |
| `DEPLOY_BACKEND_ENABLED` | `deploy-backend.yml` job-level `if` | Must be the literal string `true`. `True` skips the job. |
| `BACKEND_URL` | `deploy-pages.yml`, becomes `VITE_API_URL` | Baked in at build time, so changing it needs a Pages rebuild. |
| `VOICE_API_URL` | `deploy-pages.yml`, becomes `VITE_VOICE_API_URL` | Same. |

### Modal secrets

Names and key names both come from `voice_pipeline/modal_deploy.py`.

| Secret | Keys |
| --- | --- |
| `livekit-credentials` | `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` |
| `voice-agent-providers` | `OPENAI_API_KEY`, `DEEPGRAM_API_KEY`, `ASSEMBLYAI_API_KEY`, `CARTESIA_API_KEY` |
| `voice-agent-backend` | `VOICE_AGENT_BACKEND_URL`, `VOICE_AGENT_JWT` |
| `wandb-credentials` | `WANDB_API_KEY`, optional |

## Two things the wizard reports but will not fix

Both are code or workflow changes, which is outside a provisioning wizard's
remit. It tells you and moves on.

### Blocker: the `SEED_*` passwords are not mounted

`backend/main.py`'s lifespan calls `seed_initial_data()` on every startup.
`backend/db/seed.py` resolves the four passwords before it opens a session, and
`_seed_password()` raises `RuntimeError` when `ENVIRONMENT=production` and the
variable is unset. That happens on every cold start, not just the first, because
the "already seeded, skipping" check comes afterwards.

`deploy-backend.yml`'s `--set-secrets` does not include them. So the container
cannot start, `/health/ready` never returns 200, the workflow's health gate
gives up after 18 attempts, traffic rolls back, and the run goes red after a
fifteen minute build.

A manual `gcloud run services update --update-secrets` does not fix it durably:
`gcloud run deploy --set-secrets` replaces the whole list on every revision, so
the next CI deploy wipes it. The list has to change in the workflow. Append this
to the `--set-secrets` line in the "Deploy to Cloud Run" step:

```
,SEED_SUPER_ADMIN_PASSWORD=SEED_SUPER_ADMIN_PASSWORD:latest,SEED_ADMIN_PASSWORD=SEED_ADMIN_PASSWORD:latest,SEED_USER_PASSWORD=SEED_USER_PASSWORD:latest,SEED_DEMO_PASSWORD=SEED_DEMO_PASSWORD:latest
```

Stage 8 detects whether the edit has been made and refuses to flip the deploy
gate until it has, unless you explicitly override it.

### Mercury has two different sets of environment variable names

- `chat_pipeline/rag/generator.py` reads `INCEPTION_API_KEY` and
  `INCEPTION_API_BASE`.
- `backend/agents/utils/llm_client.py` reads `MERCURY_API_KEY` and
  `MERCURY_API_URL`.

`deploy-backend.yml` supplies only the `INCEPTION_*` pair. So chat and RAG work,
but `backend/agents/utils/llm_config.py` sets
`FALLBACK_CHAIN = ["groq", "mercury", "openai", "local"]` and in production that
chain is effectively just Groq: `_call_mercury` raises "Mercury credentials not
configured" immediately, `OPENAI_API_KEY` is not mounted either, and `local`
Ollama does not exist on Cloud Run.

Your options:

1. Accept it. Chat and RAG are unaffected; only the agents lose their fallbacks.
2. Add `MERCURY_API_URL` (`https://api.inceptionlabs.ai/v1`) and
   `MERCURY_API_KEY` (the same Inception key) to the workflow's `--set-secrets`.
   The wizard offers to create both secrets so this is a one-line workflow edit.
3. Add `OPENAI_API_KEY` to the workflow and let OpenAI be the agents' fallback.

## Manual fallback for every stage

If a stage fails or you would rather do it by hand. Set `PROJECT_ID` and
`REGION=us-central1` first. `deployment/DEPLOYMENT_GUIDE.md` is the longer
reference.

**Stage 2, project and APIs**

```bash
gcloud projects create "$PROJECT_ID" --name="FrontShiftAI"
gcloud billing accounts list
gcloud billing projects link "$PROJECT_ID" --billing-account=<ACCOUNT_ID>
gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com iamcredentials.googleapis.com iam.googleapis.com \
  sts.googleapis.com cloudresourcemanager.googleapis.com compute.googleapis.com \
  --project "$PROJECT_ID"
gcloud artifacts repositories create frontshiftai-backend \
  --repository-format=docker --location=us-central1 --project "$PROJECT_ID"
```

**Stage 3, Workload Identity Federation**

```bash
SA="github-actions-deploy@$PROJECT_ID.iam.gserviceaccount.com"
NUM=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')

gcloud iam service-accounts create github-actions-deploy \
  --display-name="GitHub Actions Deployer" --project "$PROJECT_ID"

gcloud iam workload-identity-pools create github-actions-pool \
  --location=global --display-name="GitHub Actions Pool" --project "$PROJECT_ID"

gcloud iam workload-identity-pools providers create-oidc github-provider \
  --location=global --workload-identity-pool=github-actions-pool \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.actor=assertion.actor,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner" \
  --attribute-condition="assertion.repository=='Raghavgali/FrontShiftAI'" \
  --project "$PROJECT_ID"

gcloud iam service-accounts add-iam-policy-binding "$SA" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/$NUM/locations/global/workloadIdentityPools/github-actions-pool/attribute.repository/Raghavgali/FrontShiftAI" \
  --project "$PROJECT_ID"

for R in roles/run.admin roles/artifactregistry.writer \
         roles/iam.serviceAccountUser roles/secretmanager.viewer; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$SA" --role="$R" --condition=None
done
```

The attribute condition is not optional: Google rejects an OIDC provider with a
well-known public issuer and no condition. It is also what stops any other
repository on GitHub from impersonating your deploy account.

**Stage 4 and 5, secrets**

```bash
printf '%s' "$VALUE" | gcloud secrets create NAME --data-file=- --project "$PROJECT_ID"
printf '%s' "$VALUE" | gcloud secrets versions add NAME --data-file=- --project "$PROJECT_ID"

RUNTIME_SA="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')-compute@developer.gserviceaccount.com"
gcloud secrets add-iam-policy-binding NAME \
  --member="serviceAccount:$RUNTIME_SA" \
  --role=roles/secretmanager.secretAccessor --project "$PROJECT_ID"
```

`deploy-backend.yml` does not pass `--service-account`, so the runtime identity
is the Compute Engine default account. That is why `compute.googleapis.com` is in
the API list: without it, the account does not exist.

**Stage 7, GitHub**

```bash
export GH_REPO=Raghavgali/FrontShiftAI   # this clone has two remotes
gh secret set GCP_PROJECT_ID
gh secret set GCP_SERVICE_ACCOUNT
gh secret set GCP_WORKLOAD_IDENTITY_PROVIDER
gh variable set DEPLOY_BACKEND_ENABLED --body true
gh variable set BACKEND_URL --body "https://..."
gh variable set VOICE_API_URL --body "https://..."
```

**Stage 8 and 9, deploys**

```bash
gh workflow run deploy-backend.yml --ref main
gh run list --workflow deploy-backend.yml --limit 1
gcloud run services describe frontshiftai-backend --region us-central1 \
  --project "$PROJECT_ID" --format='value(status.url)'
gh workflow run deploy-pages.yml --ref main
```

**Stage 10 and 11, voice**

```bash
modal profile list          # confirm the workspace before you deploy
modal secret create livekit-credentials \
  LIVEKIT_URL=wss://... LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=... --force
modal secret create voice-agent-providers \
  OPENAI_API_KEY=... DEEPGRAM_API_KEY=... ASSEMBLYAI_API_KEY=... CARTESIA_API_KEY=... --force
modal secret create voice-agent-backend \
  VOICE_AGENT_BACKEND_URL=https://... VOICE_AGENT_JWT=... --force
modal deploy voice_pipeline/modal_deploy.py
```

Values on a `modal secret create` command line are visible in your local process
list and land in your shell history. The wizard uses `--from-dotenv` with a 0600
temp file instead. If you do it by hand, prefer the same:

```bash
umask 077
cat > /tmp/lk.env <<'EOF'
LIVEKIT_URL=wss://...
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
EOF
modal secret create livekit-credentials --from-dotenv /tmp/lk.env --force
rm -f /tmp/lk.env
```

## How secrets are handled

- Secret values are read with hidden input and never printed.
- They reach `gcloud` and `modal` through a 0600 file inside a 0700 temp
  directory, not on a command line, so they never appear in your process list.
  The temp directory is removed by an EXIT trap.
- They are never written to the state file. The state file records only
  non-secret values (project id, service accounts, deployed URLs) and per-stage
  completion markers, and there is a guard that refuses to persist any key whose
  name looks like credential material.
- Because nothing is cached locally, re-running a stage detects an existing
  secret in Secret Manager and offers to rotate it rather than asking you to
  paste it again.
- Read a generated value back with:
  `gcloud secrets versions access latest --secret=NAME --project "$PROJECT_ID"`

## State file

`deployment/.go_live.state`, gitignored. Plain `KEY=value`. Delete it with
`--reset` if it gets confusing; nothing in the cloud depends on it, and the next
run re-detects what exists.

## Costs, stated plainly

Nothing here is free forever.

- **Cloud Run**: `min-instances 0`, so an idle service is effectively free. You
  pay per request-second while it serves. Expect a slow cold start while torch
  and the MiniLM embedding model load.
- **Artifact Registry**: charged on stored image layers. The backend image is
  large. Delete old image versions periodically.
- **New Google Cloud accounts**: a 300 USD, 90 day credit. Billing must still be
  linked or the APIs will not enable.
- **Neon**: free plan, no card, with storage and compute-hour limits that can
  change. A free-plan branch auto-suspends when idle.
- **Modal**: monthly free credits, then per-second CPU billing while a voice
  worker runs. Workers exit when the call ends.
- **OpenAI**: no free tier. `gpt-4o-mini` is cheap per token, not zero. Set a
  spend limit.
- **Groq, Brave, Deepgram, AssemblyAI, Cartesia, Inception**: each has its own
  free tier or trial credit with its own limits.

Set a Cloud Billing budget alert. The expensive failure mode here is a runaway
loop against an LLM API, not Cloud Run.

## Things marked "unverified" in the wizard output

Console layouts and dashboard URLs change, and some could not be confirmed while
the wizard was written. Anywhere that is true, the wizard says so with a
`? unverified:` line rather than presenting a guess as fact. That covers Neon's
region list, the Brave plan page, the Inception Labs console URL, the LiveKit
key page, the AssemblyAI and Cartesia dashboard paths, Modal's web endpoint URL
scheme, and whether a LiveKit plugin raises at construction time when a fallback
provider's key is missing.
