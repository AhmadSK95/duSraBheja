#!/bin/bash
# duSraBheja — Start all services
# Runs the gateway, inbox processor, and responder

export PATH="/opt/homebrew/bin:/opt/homebrew/opt/postgresql@16/bin:$PATH"
cd /Users/moenuddeenahmadshaik/Desktop/duSraBheja

LOG_DIR="$HOME/Desktop/duSraBheja/logs"
mkdir -p "$LOG_DIR"

echo "[$(date)] Starting duSraBheja..." >> "$LOG_DIR/main.log"

# Start the main process
exec npx tsx src/index.ts >> "$LOG_DIR/main.log" 2>&1
