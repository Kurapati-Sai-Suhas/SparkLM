#!/bin/sh
# Manual production snapshot (M8). Neon's point-in-time restore is the
# PRIMARY recovery mechanism (console.neon.tech -> Backup & Restore);
# this script takes an explicit local dump for belt-and-suspenders
# moments — e.g. right before a demo or a risky data operation.
#
# Usage:
#   NEON_URL="postgresql://user:pass@host/neondb?sslmode=require" ./scripts/backup_prod.sh
#
# Requires pg_dump (any PG >= 15 client; the docker container works:
#   docker exec -e NEON_URL="$NEON_URL" learnlm_postgres sh -c \
#     'pg_dump "$NEON_URL" --no-owner --no-privileges' > backup.sql )
set -eu

if [ -z "${NEON_URL:-}" ]; then
  echo "Set NEON_URL to the production connection string first." >&2
  exit 1
fi

stamp=$(date +%Y%m%d_%H%M%S)
out="sparklm_prod_${stamp}.sql"
pg_dump "$NEON_URL" --no-owner --no-privileges > "$out"
echo "Wrote $out ($(wc -c < "$out") bytes). Keep it OUT of git."
