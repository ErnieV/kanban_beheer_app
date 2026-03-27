#!/usr/bin/env bash

set -euo pipefail

# Creates the public Azure Function App used for kanban QR scans.
# Prerequisites:
# - Azure CLI logged in: az login
# - Correct subscription selected: az account set --subscription "<subscription>"
# - Optional: GitHub CLI logged in if you want to set the repo variable: gh auth login

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-kanban}"
LOCATION="${LOCATION:-westeurope}"
FUNCTION_APP_NAME="${FUNCTION_APP_NAME:-kanban-scan-function}"
STORAGE_ACCOUNT_NAME="${STORAGE_ACCOUNT_NAME:-kanbanscan$(date +%s | tail -c 7)}"
APP_SERVICE_PLAN="${APP_SERVICE_PLAN:-asp-kanban-scan}"
RUNTIME="${RUNTIME:-python}"
RUNTIME_VERSION="${RUNTIME_VERSION:-3.12}"
SKU="${SKU:-Y1}"
REPO_OWNER="${REPO_OWNER:-}"
REPO_NAME="${REPO_NAME:-kanban_beheer_app}"

echo "Using settings:"
echo "  RESOURCE_GROUP=$RESOURCE_GROUP"
echo "  LOCATION=$LOCATION"
echo "  FUNCTION_APP_NAME=$FUNCTION_APP_NAME"
echo "  STORAGE_ACCOUNT_NAME=$STORAGE_ACCOUNT_NAME"
echo "  APP_SERVICE_PLAN=$APP_SERVICE_PLAN"

az group show --name "$RESOURCE_GROUP" >/dev/null

if ! az storage account show --name "$STORAGE_ACCOUNT_NAME" --resource-group "$RESOURCE_GROUP" >/dev/null 2>&1; then
  az storage account create \
    --name "$STORAGE_ACCOUNT_NAME" \
    --location "$LOCATION" \
    --resource-group "$RESOURCE_GROUP" \
    --sku Standard_LRS \
    --allow-blob-public-access false \
    --min-tls-version TLS1_2 >/dev/null
fi

if [[ "$SKU" != "Y1" ]]; then
  if ! az functionapp plan show --name "$APP_SERVICE_PLAN" --resource-group "$RESOURCE_GROUP" >/dev/null 2>&1; then
    az functionapp plan create \
      --name "$APP_SERVICE_PLAN" \
      --resource-group "$RESOURCE_GROUP" \
      --location "$LOCATION" \
      --sku "$SKU" \
      --is-linux >/dev/null
  fi
fi

if ! az functionapp show --name "$FUNCTION_APP_NAME" --resource-group "$RESOURCE_GROUP" >/dev/null 2>&1; then
  if [[ "$SKU" == "Y1" ]]; then
    az functionapp create \
      --name "$FUNCTION_APP_NAME" \
      --resource-group "$RESOURCE_GROUP" \
      --storage-account "$STORAGE_ACCOUNT_NAME" \
      --consumption-plan-location "$LOCATION" \
      --runtime "$RUNTIME" \
      --runtime-version "$RUNTIME_VERSION" \
      --functions-version 4 \
      --os-type Linux >/dev/null
  else
    az functionapp create \
      --name "$FUNCTION_APP_NAME" \
      --resource-group "$RESOURCE_GROUP" \
      --storage-account "$STORAGE_ACCOUNT_NAME" \
      --plan "$APP_SERVICE_PLAN" \
      --runtime "$RUNTIME" \
      --runtime-version "$RUNTIME_VERSION" \
      --functions-version 4 \
      --os-type Linux >/dev/null
  fi
fi

az functionapp config appsettings set \
  --name "$FUNCTION_APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --settings \
    SCM_DO_BUILD_DURING_DEPLOYMENT=true \
    ENABLE_ORYX_BUILD=true \
    FUNCTIONS_EXTENSION_VERSION=~4 \
    PYTHON_ISOLATE_WORKER_DEPENDENCIES=1 >/dev/null

FUNCTION_URL="$(az functionapp show --name "$FUNCTION_APP_NAME" --resource-group "$RESOURCE_GROUP" --query defaultHostName -o tsv)"

echo
echo "Function app ready:"
echo "  https://$FUNCTION_URL"
echo
echo "Set these app settings on the Function App if not already present:"
echo "  DB_SERVER"
echo "  DB_NAME"
echo "  DB_USER"
echo "  DB_PASS"
echo
echo "Set this app setting on the web app:"
echo "  KANBAN_SCAN_BASE_URL=https://$FUNCTION_URL"

if [[ -n "$REPO_OWNER" ]]; then
  gh variable set AZURE_SCAN_FUNCTION_APP_NAME \
    --repo "$REPO_OWNER/$REPO_NAME" \
    --body "$FUNCTION_APP_NAME"
  echo
  echo "GitHub repo variable set:"
  echo "  AZURE_SCAN_FUNCTION_APP_NAME=$FUNCTION_APP_NAME"
else
  echo
  echo "Optional GitHub step:"
  echo "  gh variable set AZURE_SCAN_FUNCTION_APP_NAME --repo <owner>/$REPO_NAME --body $FUNCTION_APP_NAME"
fi
