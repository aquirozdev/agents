#!/usr/bin/env bash
set -eu

: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"
: "${FINERACT_DB_USER:?FINERACT_DB_USER is required}"
: "${FINERACT_DB_PASS:?FINERACT_DB_PASS is required}"
: "${FINERACT_TENANTS_DB_NAME:?FINERACT_TENANTS_DB_NAME is required}"
: "${FINERACT_TENANT_DEFAULT_DB_NAME:?FINERACT_TENANT_DEFAULT_DB_NAME is required}"

export PGPASSWORD="${POSTGRES_PASSWORD}"

psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname postgres \
  --set=db_user="${FINERACT_DB_USER}" \
  --set=db_password="${FINERACT_DB_PASS}" \
  --set=tenants_db="${FINERACT_TENANTS_DB_NAME}" \
  --set=default_db="${FINERACT_TENANT_DEFAULT_DB_NAME}" <<'EOSQL'
CREATE USER :"db_user" WITH PASSWORD :'db_password';
CREATE DATABASE :"tenants_db";
CREATE DATABASE :"default_db";
GRANT ALL PRIVILEGES ON DATABASE :"tenants_db" TO :"db_user";
GRANT ALL PRIVILEGES ON DATABASE :"default_db" TO :"db_user";

\connect :tenants_db
GRANT ALL ON SCHEMA public TO :"db_user";

\connect :default_db
GRANT ALL ON SCHEMA public TO :"db_user";
EOSQL
