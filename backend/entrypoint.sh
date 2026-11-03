#!/bin/sh
set -e

echo "[entrypoint] applying database migrations"
alembic upgrade head

if [ "${SEED_ON_START:-true}" = "true" ]; then
  echo "[entrypoint] seeding demo data (idempotent)"
  python -m ecommerce_cs.seed_data --with-index
fi

exec "$@"
