#!/usr/bin/env bash
#
# Deploy the current checkout on the server.
#
#   cd /opt/hospitalityos && ./infra/scripts/deploy.sh
#
# Ordering is the whole point of this script:
#
#   1. build images        - a failed build must not take the site down
#   2. run migrations      - once, in a one-off container, never racing
#   3. start the new app   - only after the schema is ready
#   4. verify              - and say plainly if it did not come up
#
# The dev image runs `alembic upgrade head` in its CMD so `make dev` just
# works. The production image deliberately does not: with more than one
# replica that is N containers migrating the same database simultaneously.

set -euo pipefail

cd "$(dirname "$0")/../.."

COMPOSE="docker compose -f infra/docker/docker-compose.prod.yml --env-file .env.prod"

fail() { echo "error: $*" >&2; exit 1; }

[ -f .env.prod ] || fail ".env.prod not found. Copy .env.prod.example and fill it in."

# Fail before touching anything if a required secret is blank. Finding out
# from a 503 after the old containers are gone is a worse way to learn it.
for key in DOMAIN ACME_EMAIL POSTGRES_PASSWORD JWT_SECRET; do
  value=$(grep -E "^${key}=" .env.prod | head -1 | cut -d= -f2- || true)
  [ -n "$value" ] || fail "$key is empty in .env.prod"
done

if grep -qE '^STAFF_API_TOKEN=.+' .env.prod; then
  echo "WARNING: STAFF_API_TOKEN is set. It grants cross-tenant access with"
  echo "         no identity and no audit trail. Real accounts replaced it."
  echo "         Leave it empty unless you are mid-upgrade."
fi

echo "==> 1/5  Building images"
$COMPOSE build

echo "==> 2/5  Starting the database"
# Postgres must be up and healthy BEFORE migrations. An earlier version of
# this script ran the migration with --no-deps, so on a FIRST deploy -
# when nothing is running yet - alembic came up against a database that
# was not there. It worked on every redeploy and failed on the one deploy
# that mattered.
$COMPOSE up -d postgres redis

echo "    waiting for postgres"
for _ in $(seq 1 30); do
  $COMPOSE exec -T postgres pg_isready -q 2>/dev/null && break
  sleep 2
done
$COMPOSE exec -T postgres pg_isready -q 2>/dev/null \
  || fail "postgres did not become ready. Check: $COMPOSE logs postgres"

echo "==> 3/5  Running migrations"
# A one-off container that exits when alembic does, so a failure here
# stops the deploy with the old app containers still serving.
$COMPOSE run --rm backend alembic upgrade head \
  || fail "migrations failed. The running site was not touched."

echo "==> 4/5  Starting services"
$COMPOSE up -d --remove-orphans

echo "==> 5/5  Waiting for health"
domain=$(grep -E '^DOMAIN=' .env.prod | head -1 | cut -d= -f2-)
for i in $(seq 1 30); do
  if curl -fsS --max-time 5 "https://${domain}/health" >/dev/null 2>&1; then
    echo
    echo "Deployed. https://${domain}"
    echo "  staff:  https://${domain}/staff/login"
    echo "  widget: https://${domain}/widget"
    exit 0
  fi
  sleep 5
done

echo >&2
echo "error: the site did not answer /health within 150s." >&2
echo "  A brand-new certificate can take a minute; check the logs:" >&2
echo "    $COMPOSE logs --tail 50 traefik backend" >&2
exit 1
