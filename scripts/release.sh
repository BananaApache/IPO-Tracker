#!/usr/bin/env sh
# Applies pending migrations, then starts the API.
#
# Migrations run in the release path rather than as a separate service because
# managed platforms have no equivalent of compose's
# `depends_on: service_completed_successfully`. The runner takes a session
# advisory lock, so two instances starting at once cannot double-apply.
set -e
echo "release: applying migrations"
python -m backend.migrate
echo "release: starting api on port ${PORT:-8000}"
exec fastapi run --host 0.0.0.0 --port "${PORT:-8000}"
