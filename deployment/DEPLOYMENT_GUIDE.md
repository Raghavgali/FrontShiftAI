# FrontShiftAI - Deployment Guide

This guide explains how to deploy the FrontShiftAI application to Google Cloud Platform (GCP) from scratch. It is designed for developers who want to set up their own instance of the application.

> **Recommended path**: run the interactive wizard, `./deployment/go_live.sh` (documented in `deployment/GO_LIVE.md`), which automates everything below. This guide is the manual fallback and a reference for what the wizard does under the hood.

## Prerequisites

1.  **GCP Account**: A Google Cloud Platform account with billing enabled.
2.  **Domain Name (Optional)**: For custom domains (Cloud Run provides default URLs).
3.  **Third-Party API Keys**:
    - [Inception Labs](https://platform.inceptionlabs.ai/) (Mercury LLM, read as `INCEPTION_API_KEY`; the API base `chat_pipeline/rag/generator.py` defaults to is `https://api.inceptionlabs.ai/v1`)
    - [Groq](https://groq.com/) (Fallback LLM)
    - [Brave Search](https://brave.com/search/api/) (Web extraction)
    - [HuggingFace](https://huggingface.co/) (Embedding models)
4.  **Tools**:
    - [Google Cloud CLI](https://cloud.google.com/sdk/docs/install) (`gcloud`)
    - [Docker](https://docs.docker.com/get-docker/)
    - [Git](https://git-scm.com/)

---

## Step 1: GCP Project Setup

1.  **Create a Project**:

    > **Warning**: `YOUR_PROJECT_ID` must be a new, globally unique id across all of Google Cloud. Do not reuse the old `frontshiftai` or `frontshiftai-deploy` project ids; they belong to a retired project and cannot be reused.

    ```bash
    gcloud projects create YOUR_PROJECT_ID --name="FrontShiftAI Deploy"
    gcloud config set project YOUR_PROJECT_ID
    ```

2.  **Enable Required APIs**:
    ```bash
    gcloud services enable \
      run.googleapis.com \
      artifactregistry.googleapis.com \
      secretmanager.googleapis.com \
      iamcredentials.googleapis.com \
      iam.googleapis.com \
      sts.googleapis.com \
      cloudresourcemanager.googleapis.com \
      compute.googleapis.com
    ```

---

## Step 2: Infrastructure Setup

### 1. Artifact Registry (Docker Images)
Create the repository for the backend image.

```bash
gcloud artifacts repositories create frontshiftai-backend \
    --repository-format=docker \
    --location=us-central1 \
    --description="Backend Docker repository"
```

> **Legacy / not needed**: a `frontshiftai-frontend` Artifact Registry repository is no longer required. The frontend and landing page are deployed to GitHub Pages (see Step 4), not Cloud Run or Docker.

### 2. Cloud Storage (Data & Vectors): legacy, not needed
Older versions of this guide had you package the ChromaDB vector store into a tar.gz, upload it to a Cloud Storage bucket, and download it at container start. That is no longer necessary: the Chroma store now ships baked into the backend Docker image (`Dockerfile.backend` copies `data_pipeline/`), and `chat_pipeline/rag/data_loader.py:ensure_chroma_store()` returns immediately when the store is already present locally. You can skip this step entirely.

### 3. Neon (Database)
Create a free serverless Postgres database with [Neon](https://neon.tech/):

1.  Sign up and create a new Neon project in the browser.
2.  Copy the connection string Neon gives you. Prefer the pooled connection (the host with the `-pooler` suffix), and keep `sslmode=require` in the URL.
3.  Store that connection string verbatim as the `DATABASE_URL` secret in Step 4 below. No further setup, instance sizing, or IAM roles are required.

### 4. Secret Manager (Configuration)
Store sensitive keys safely. These are the five secrets the deploy workflow mounts:

```bash
# Create and set secrets (repeat for each key)
echo -n "your-mercury-key" | gcloud secrets create INCEPTION_API_KEY --data-file=-
echo -n "your-groq-key" | gcloud secrets create GROQ_API_KEY --data-file=-
echo -n "your-brave-key" | gcloud secrets create BRAVE_API_KEY --data-file=-
echo -n "your-jwt-secret" | gcloud secrets create JWT_SECRET_KEY --data-file=-

# Paste the Neon connection string from Step 3 verbatim
echo -n "your-neon-connection-string" | gcloud secrets create DATABASE_URL --data-file=-
```

> `HF_TOKEN` is optional and not mounted by the deploy workflow; only create it if you need it for local/offline use.

---

## Step 3: GitHub Actions Integration (CI/CD)

To deploy automatically on push, we use **Workload Identity Federation** (Keyless Authentication).

### 1. Setup Workload Identity
Run these commands to verify trust between GitHub and GCP.

```bash
# Create Service Account for GitHub Actions
gcloud iam service-accounts create github-actions-deploy \
    --display-name="GitHub Actions Deployer"

# Create Workload Identity Pool
gcloud iam workload-identity-pools create github-actions-pool \
    --location="global" \
    --description="Pool for GitHub Actions" \
    --display-name="GitHub Actions Pool"

# Create Provider
# --attribute-condition is required, not optional: Google rejects an OIDC
# provider that uses a well-known public issuer with no condition. It is also
# what stops any other repository on GitHub from impersonating this account,
# so replace YOUR_GITHUB_USER/YOUR_REPO with your own repo.
gcloud iam workload-identity-pools providers create-oidc github-provider \
    --workload-identity-pool="github-actions-pool" \
    --location="global" \
    --display-name="GitHub Provider" \
    --attribute-mapping="google.subject=assertion.sub,attribute.actor=assertion.actor,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner" \
    --attribute-condition="assertion.repository=='YOUR_GITHUB_USER/YOUR_REPO'" \
    --issuer-uri="https://token.actions.githubusercontent.com"

# Allow GitHub Repo to impersonate Service Account
# REPLACE 'YOUR_GITHUB_USER/YOUR_REPO' with your actual repo (e.g., 'johndoe/FrontShiftAI')
gcloud iam service-accounts add-iam-policy-binding "github-actions-deploy@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/iam.workloadIdentityUser" \
    --member="principalSet://iam.googleapis.com/projects/YOUR_PROJECT_NUMBER/locations/global/workloadIdentityPools/github-actions-pool/attribute.repository/YOUR_GITHUB_USER/YOUR_REPO"
```

### 2. Grant Permissions
Give the service account access to resources.

```bash
SA_EMAIL="github-actions-deploy@YOUR_PROJECT_ID.iam.gserviceaccount.com"

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID --member="serviceAccount:$SA_EMAIL" --role="roles/run.admin"
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID --member="serviceAccount:$SA_EMAIL" --role="roles/artifactregistry.writer"
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID --member="serviceAccount:$SA_EMAIL" --role="roles/iam.serviceAccountUser"
gcloud projects add-iam-policy-binding YOUR_PROJECT_ID --member="serviceAccount:$SA_EMAIL" --role="roles/secretmanager.viewer"
```

`roles/storage.admin` used to be on this list for the ChromaDB bucket. That bucket is gone (Step 2.2), so the role is no longer needed.

### 2b. Let the Cloud Run runtime account read the secrets

`deploy-backend.yml` does not pass `--service-account`, so the service runs as the Compute Engine default account. Without this the deploy fails, because a revision that cannot read a mounted secret never becomes ready.

```bash
PROJECT_NUMBER=$(gcloud projects describe YOUR_PROJECT_ID --format='value(projectNumber)')
RUNTIME_SA="$PROJECT_NUMBER-compute@developer.gserviceaccount.com"

for S in GROQ_API_KEY BRAVE_API_KEY JWT_SECRET_KEY INCEPTION_API_KEY DATABASE_URL; do
  gcloud secrets add-iam-policy-binding "$S" \
    --member="serviceAccount:$RUNTIME_SA" \
    --role="roles/secretmanager.secretAccessor" \
    --project YOUR_PROJECT_ID
done
```

That account only exists once `compute.googleapis.com` has been enabled, which is why it is in the API list in Step 1.

### 3. Configure GitHub Secrets
Go to your GitHub Repository -> Settings -> Secrets and Variables -> Actions -> New Repository Secret.

| Secret Name | Value |
|-------------|-------|
| `GCP_PROJECT_ID` | `YOUR_PROJECT_ID` |
| `GCP_SERVICE_ACCOUNT` | `github-actions-deploy@YOUR_PROJECT_ID.iam.gserviceaccount.com` |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | `projects/YOUR_PROJECT_NUMBER/locations/global/workloadIdentityPools/github-actions-pool/providers/github-provider` |

`JWT_SECRET_KEY` is not a GitHub secret; it lives only in Secret Manager (Step 2.4).

Also go to Settings -> Secrets and Variables -> Actions -> Variables and add these repository variables, which `deploy-backend.yml` and `deploy-pages.yml` read:

| Variable Name | Value |
|----------------|-------|
| `DEPLOY_BACKEND_ENABLED` | Must be the literal string `true`, or the backend deploy job is skipped |
| `BACKEND_URL` | Your deployed Cloud Run backend URL, baked into the chat app build |
| `VOICE_API_URL` | Your voice API URL, baked into the chat app build |

---

## Step 4: Deploying Your Application

### Automatic Deployment
1.  Push code to the `main` branch that touches backend paths, or trigger the workflow manually from the Actions tab.
2.  GitHub Actions (`deploy-backend.yml`) deploys the backend to Cloud Run, but only if the `DEPLOY_BACKEND_ENABLED` repository variable is set to `true`; otherwise the deploy job is skipped.
3.  The landing page and chat app are deployed separately, to GitHub Pages, by `.github/workflows/deploy-pages.yml`. There is no `deploy-frontend.yml`; it does not exist.

### Manual Deployment (from local machine)
If you want to deploy the backend without GitHub Actions:

```bash
gcloud run deploy frontshiftai-backend \
    --source backend/ \
    --region us-central1 \
    --allow-unauthenticated \
    --set-secrets GROQ_API_KEY=GROQ_API_KEY:latest,BRAVE_API_KEY=BRAVE_API_KEY:latest,JWT_SECRET_KEY=JWT_SECRET_KEY:latest,INCEPTION_API_KEY=INCEPTION_API_KEY:latest,DATABASE_URL=DATABASE_URL:latest
```

The frontend is not deployed with `gcloud run` or Docker; it is static and published via GitHub Pages (`.github/workflows/deploy-pages.yml`).

---

## Step 5: Post-Deployment Verification

1.  **Check URLs**:
    - Go to Cloud Run console and get the service URLs.
    - Visit the Frontend URL.
2.  **Verify Database Connection**:
    - Try to log in (default user logic in code).
    - If you see "Backend Offline", check the browser console and backend logs.
3.  **Check Logs**:
    - `gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=frontshiftai-backend" --limit 20`

## Troubleshooting

- **504 Gateway Timeout**: The backend might be taking too long to start (loading the Chroma vector store baked into the image). Increase timeout to 300s.
- **Database Connection Error**: Ensure the `DATABASE_URL` secret holds the full Neon connection string (with `sslmode=require`) and that the Neon project is active.
- **Permission Denied**: Check IAM roles for the deployment service account.
