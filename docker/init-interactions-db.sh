#!/bin/sh
set -eu

database_name="${INTERACTIONS_POSTGRES_DB:-ai_author_forum_interactions}"

psql --set ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    --set interactions_database="$database_name" <<'SQL'
SELECT format('CREATE DATABASE %I OWNER %I', :'interactions_database', current_user)
WHERE NOT EXISTS (
    SELECT 1 FROM pg_database WHERE datname = :'interactions_database'
)
\gexec
SQL
