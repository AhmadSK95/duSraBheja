#!/bin/bash
# duSraBheja — Start (compiled, single-process)
export PATH="/opt/homebrew/bin:/opt/homebrew/opt/postgresql@16/bin:$PATH"
cd /Users/moenuddeenahmadshaik/Desktop/duSraBheja
mkdir -p logs
exec node --max-old-space-size=256 dist/index.js >> logs/main.log 2>&1
