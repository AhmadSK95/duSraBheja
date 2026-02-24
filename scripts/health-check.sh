#!/bin/bash
# duSraBheja Health Check Script
# Verifies all Phase 0 infrastructure services are running

set -uo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

PASS=0
FAIL=0
WARN=0

check() {
    local name="$1"
    local cmd="$2"
    local expected="$3"

    if result=$(eval "$cmd" 2>&1); then
        if [[ -z "$expected" ]] || echo "$result" | grep -q "$expected"; then
            echo -e "  ${GREEN}✓${NC} $name"
            PASS=$((PASS + 1))
            return 0
        fi
    fi
    echo -e "  ${RED}✗${NC} $name"
    FAIL=$((FAIL + 1))
    return 1
}

warn() {
    local name="$1"
    echo -e "  ${YELLOW}⚠${NC} $name"
    WARN=$((WARN + 1))
}

echo ""
echo "═══════════════════════════════════════════"
echo "  duSraBheja Health Check"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "═══════════════════════════════════════════"
echo ""

# PostgreSQL
echo "PostgreSQL 16:"
check "Server running" "/opt/homebrew/opt/postgresql@16/bin/pg_isready -q" ""
check "Database exists" "/opt/homebrew/opt/postgresql@16/bin/psql -d dusrabheja -c 'SELECT 1' -t -q" "1"
check "pgvector extension" "/opt/homebrew/opt/postgresql@16/bin/psql -d dusrabheja -c \"SELECT extname FROM pg_extension WHERE extname='vector'\" -t -q" "vector"
check "Tables created (11)" "/opt/homebrew/opt/postgresql@16/bin/psql -d dusrabheja -c \"SELECT count(*) FROM information_schema.tables WHERE table_schema='public'\" -t -q" "11"
echo ""

# NATS
echo "NATS Server:"
check "Server running" "nats pub health.ping 'test' 2>&1" "Published"
check "JetStream enabled" "nats account info" "JetStream Account"
check "Pub/sub works" "nats pub health.check 'ping' 2>&1" "Published"
echo ""

# Ollama
echo "Ollama:"
check "Server running" "curl -s http://localhost:11434/api/tags > /dev/null" ""
check "llama3.1:8b model" "ollama list" "llama3.1:8b"
check "nomic-embed-text model" "ollama list" "nomic-embed-text"
echo ""

# Temporal
echo "Temporal:"
check "Server running (Docker)" "docker ps --filter name=dusrabheja-temporal --format '{{.Status}}' | head -1" "Up"
check "UI accessible (port 8233)" "curl -s -o /dev/null -w '%{http_code}' http://localhost:8233" "200"
echo ""

# Docker
echo "Docker:"
check "Daemon running" "docker info > /dev/null 2>&1" ""
echo ""

# Cloudflare Tunnel
echo "Cloudflare Tunnel:"
if command -v cloudflared &> /dev/null; then
    check "cloudflared installed" "cloudflared --version" ""
    warn "Tunnel not yet configured (Phase 0 optional)"
else
    warn "cloudflared not installed"
fi
echo ""

# Summary
echo "═══════════════════════════════════════════"
echo -e "  Results: ${GREEN}${PASS} passed${NC}, ${RED}${FAIL} failed${NC}, ${YELLOW}${WARN} warnings${NC}"
echo "═══════════════════════════════════════════"
echo ""

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
