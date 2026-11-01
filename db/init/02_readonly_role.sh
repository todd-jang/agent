#!/bin/bash
# Runs once on first start of the postgres container (after 01_schema.sql).
# Creates the SELECT-only role the agent uses. AGENT_RO_PASSWORD comes from the container environment.
set -euo pipefail

psql -v ON_ERROR_STOP=1 -v pw="${AGENT_RO_PASSWORD:?AGENT_RO_PASSWORD is required}" -v DBNAME="$POSTGRES_DB" \
     --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'EOSQL'
CREATE ROLE agent_ro LOGIN PASSWORD :'pw';
GRANT CONNECT ON DATABASE :"DBNAME" TO agent_ro;
GRANT USAGE ON SCHEMA public TO agent_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO agent_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO agent_ro;
ALTER ROLE agent_ro SET default_transaction_read_only = on;
ALTER ROLE agent_ro SET statement_timeout = '5s';
EOSQL
