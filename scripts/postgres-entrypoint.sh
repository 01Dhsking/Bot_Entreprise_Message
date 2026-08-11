#!/bin/sh
set -eu

SYNC_MARKER=/tmp/postgres-password-synced
rm -f "$SYNC_MARKER"

/usr/local/bin/docker-entrypoint.sh "$@" &
postgres_pid=$!

shutdown() {
    kill -TERM "$postgres_pid" 2>/dev/null || true
    wait "$postgres_pid" 2>/dev/null || true
}

trap shutdown INT TERM

attempt=1
while :; do
    if ! kill -0 "$postgres_pid" 2>/dev/null; then
        wait "$postgres_pid"
        exit $?
    fi

    # On a fresh volume, the official entrypoint starts a temporary server while
    # initializing the database. Wait until that shell has exec'ed the final
    # PostgreSQL process before synchronizing credentials and reporting healthy.
    process_name=$(cat "/proc/$postgres_pid/comm" 2>/dev/null || true)
    if [ "$process_name" = "postgres" ] \
        && pg_isready --quiet --username "$POSTGRES_USER" --dbname "$POSTGRES_DB"; then
        break
    fi

    if [ "$attempt" -ge 60 ]; then
        echo "PostgreSQL did not become ready for password synchronization" >&2
        shutdown
        exit 1
    fi
    attempt=$((attempt + 1))
    sleep 1
done

# Local socket authentication remains available inside the database container,
# allowing an existing volume to adopt the configured password without data loss.
psql \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    --set=role_name="$POSTGRES_USER" \
    --set=role_password="$POSTGRES_PASSWORD" \
    --no-psqlrc \
    --set=ON_ERROR_STOP=1 <<'SQL'
SELECT format(
    'ALTER ROLE %I WITH LOGIN PASSWORD %L',
    :'role_name',
    :'role_password'
) \gexec
SQL

touch "$SYNC_MARKER"
echo "PostgreSQL role password synchronized"

wait "$postgres_pid"
