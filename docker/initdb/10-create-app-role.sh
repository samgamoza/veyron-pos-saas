#!/bin/bash
# Runs once, on first initialisation of the Postgres data directory.
#
# Creates the role the application connects as. This role is deliberately
# NOSUPERUSER + NOBYPASSRLS: a Postgres superuser bypasses Row-Level Security
# entirely, which would silently disable tenant isolation.
#
# The role owns schema `public` so the app's init_db() can create tables. Owning
# the tables is fine because the RLS migration uses FORCE ROW LEVEL SECURITY,
# which applies policies to the table owner too.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE ROLE "${APP_DB_USER}" LOGIN PASSWORD '${APP_DB_PASSWORD}'
      NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;

    GRANT CONNECT ON DATABASE "${POSTGRES_DB}" TO "${APP_DB_USER}";
    ALTER SCHEMA public OWNER TO "${APP_DB_USER}";
    GRANT ALL ON SCHEMA public TO "${APP_DB_USER}";
EOSQL

echo "Created non-superuser application role: ${APP_DB_USER}"
