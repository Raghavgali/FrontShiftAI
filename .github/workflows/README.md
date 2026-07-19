# GitHub Actions Workflows

This directory contains the CI/CD pipelines for the FrontShiftAI project. The workflows are categorized by their function: Deployment, Agents, Evaluation, and Testing.

## 🚀 Deployment

| Workflow | Description | Triggers |
|----------|-------------|----------|
| `deploy-frontend.yml` | Deploys the frontend application to **Google Cloud Run**. | Push to `main` |
| `deploy-backend.yml` | Deploys the backend application to **Google Cloud Run**. | Push to `main` (backend paths) |
| `deploy-vercel.yml` | Deploys the frontend application to **Vercel**. | Push to `main` |
| `model_deploy.yml` | Standalone workflow to deploy models to the registry. Can be called by other workflows or manually. | Workflow Call, Manual |
| `rollback.yml` | **Manual** workflow to rollback the model to a previous version. Requires approval for production. | Manual |

## 🤖 Agents

| Workflow | Description | Triggers |
|----------|-------------|----------|
| `agent_LLM_backend_config.yml` | Validates the LLM backend configuration, checks for API keys, and verifies database models. | Push, PR |
| `agent_evaluation.yml` | Runs a comprehensive evaluation of all agents using `agents.evaluation.run_evaluation` and logs to Weights & Biases. | Push, PR, Manual |
| `agent_hr-ticket.yml` | Runs tests for the **HR Ticket Agent** (tools, nodes, state, workflow) with coverage. | Push, PR |
| `agent_pto.yml` | Runs tests for the **PTO Agent** (tools, nodes, state, workflow) with coverage. | Push, PR |
| `agent_website-extraction.yml` | Runs tests for the **Website Extraction Agent** (tools, nodes, state, workflow) with coverage. | Push, PR |

## 📊 Evaluation


| Workflow | Description | Triggers |
|----------|-------------|----------|
| `core_eval.yml` | Runs the **Core Evaluation** experiment using `chat_pipeline` and deploys the model if successful. | Manual |
| `core_eval_self_hosted.yml` | Runs the **Core Evaluation** on a **self-hosted macOS runner** using a local Llama model. | Manual |
| `full_eval.yaml` | Runs the **Full Evaluation** experiment using `chat_pipeline`. | Manual |
| `full_eval_self_hosted.yml` | Runs the **Full Evaluation** on a **self-hosted macOS runner** using a local Llama model. | Manual |

## 🧪 Testing

| Workflow | Description | Triggers |
|----------|-------------|----------|
| `test_backend.yml` | Runs unit and integration tests for the backend (DB, API, Services) with coverage. | Push, PR |
| `test_frontend.yml` | Builds the frontend and checks for build errors. | Push, PR |
| `component_test.yml` | Fast feedback tests (Unit Tests, Code Quality) for feature branches. | Manual |
| `smoke_test.yaml` | Runs smoke tests for the `chat_pipeline` with mocked dependencies. | Manual |
