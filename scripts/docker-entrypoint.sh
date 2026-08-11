#!/bin/sh
set -eu

python -m enterprise_message_bot.db_cli wait
alembic upgrade head

exec "$@"
