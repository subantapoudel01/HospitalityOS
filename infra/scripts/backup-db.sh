#!/usr/bin/env bash
#
# Dump the production database.
#
#   ./infra/scripts/backup-db.sh                    # -> ./backups/
#   ./infra/scripts/backup-db.sh /mnt/backups       # -> elsewhere
#
# Add to the deploy user's crontab:
#   0 3 * * * cd /opt/hospitalityos && ./infra/scripts/backup-db.sh >> /var/log/hos-backup.log 2>&1
#
# A dump sitting on the same VPS as the database is not a backup - it dies
# with the server. Copy these off the box (the provider's object storage,
# or `rsync` to somewhere else) or the first real incident takes both.
#
# What is in here: guest conversation transcripts. Treat the files as
# personal data - restricted permissions, encrypted at rest wherever they
# are copied to.

set -euo pipefail

cd "$(dirname "$0")/../.."

DEST="${1:-./backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
COMPOSE="docker compose -f infra/docker/docker-compose.prod.yml --env-file .env.prod"

[ -f .env.prod ] || { echo "error: .env.prod not found" >&2; exit 1; }

DB=$(grep -E '^POSTGRES_DB=' .env.prod | cut -d= -f2- || echo hospitalityos)
USER=$(grep -E '^POSTGRES_USER=' .env.prod | cut -d= -f2- || echo hospitalityos)

mkdir -p "$DEST"
chmod 700 "$DEST"

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
FILE="$DEST/hospitalityos-$STAMP.sql.gz"

echo "==> Dumping $DB"
# Written to a .part first, then renamed. A backup job killed halfway
# through otherwise leaves a truncated file that looks like a good one.
$COMPOSE exec -T postgres pg_dump -U "$USER" -d "$DB" --clean --if-exists \
  | gzip > "$FILE.part"
mv "$FILE.part" "$FILE"
chmod 600 "$FILE"

echo "==> Verifying"
gzip -t "$FILE" || { echo "error: dump is corrupt" >&2; rm -f "$FILE"; exit 1; }
SIZE=$(du -h "$FILE" | cut -f1)

echo "==> Pruning dumps older than ${KEEP_DAYS} days"
find "$DEST" -name 'hospitalityos-*.sql.gz' -mtime "+$KEEP_DAYS" -delete

echo "$FILE ($SIZE)"
echo
echo "Restore with:"
echo "  gunzip -c $FILE | $COMPOSE exec -T postgres psql -U $USER -d $DB"
echo
echo "Test that restore on a scratch database occasionally. An untested"
echo "backup is a guess."
