#!/bin/bash
set -e

echo "Starting InvoiceIQ application..."

# Load environment variables if .env file exists (for local development)
if [ -f .env ]; then
    echo "Loading environment variables from .env file..."
    export $(cat .env | grep -v '^#' | xargs)
fi

# Check if DATABASE_URL is set
if [ -z "$DATABASE_URL" ]; then
    echo "ERROR: DATABASE_URL environment variable is not set"
    exit 1
fi

echo "Database URL configured"

# Run database migrations
echo "Running database migrations..."
alembic upgrade head
echo "Database migrations completed"

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
