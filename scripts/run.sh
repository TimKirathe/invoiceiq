#!/bin/bash
set -e

echo "Starting InvoiceIQ application..."

# Load environment variables if .env file exists (for local development)
if [ -f .env ]; then
    echo "Loading environment variables from .env file..."
    export $(cat .env | grep -v '^#' | xargs)
fi

# Check if Supabase credentials are set
if [ -z "$SUPABASE_URL" ]; then
    echo "ERROR: SUPABASE_URL environment variable is not set"
    exit 1
fi

if [ -z "$SUPABASE_SECRET_KEY" ]; then
    echo "ERROR: SUPABASE_SECRET_KEY environment variable is not set"
    exit 1
fi

echo "Supabase credentials configured"

# Determine host and reload settings based on environment
if [ "$ENVIRONMENT" = "production" ]; then
    HOST="0.0.0.0"
    RELOAD_FLAG=""
    echo "Starting production server..."
else
    HOST="0.0.0.0"
    RELOAD_FLAG="--reload"
    echo "Starting development server with auto-reload..."
fi

# Start uvicorn server
exec uvicorn src.app.main:app \
    --host "$HOST" \
    --port 8000 \
    $RELOAD_FLAG
