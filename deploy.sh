#!/usr/bin/env bash
# Deploys the whole app (UI + API) to Cloud Run from source.
# Prerequisites: gcloud CLI logged in (`gcloud auth login`), a project with billing, and backend/.env filled in.
# Usage: PROJECT_ID=my-project ./deploy.sh
set -euo pipefail
: "${PROJECT_ID:?Set PROJECT_ID}"
REGION="${REGION:-asia-southeast1}"
SERVICE="${SERVICE:-early-warning}"

set -a; source backend/.env; set +a   # NEO4J_*, GEMINI_*

gcloud config set project "$PROJECT_ID"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com

# Secrets go in as env vars here for speed. For production use Secret Manager (--set-secrets).
gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --allow-unauthenticated \
  --memory 1Gi --cpu 1 --min-instances 1 --timeout 120 \
  --set-env-vars "NEO4J_URI=$NEO4J_URI,NEO4J_USERNAME=$NEO4J_USERNAME,NEO4J_PASSWORD=$NEO4J_PASSWORD,NEO4J_DATABASE=${NEO4J_DATABASE:-neo4j},GEMINI_API_KEY=$GEMINI_API_KEY,GEMINI_MODEL=${GEMINI_MODEL:-gemini-3.1-flash-lite}"
gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)'
