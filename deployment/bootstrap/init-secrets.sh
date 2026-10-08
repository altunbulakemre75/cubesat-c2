#!/bin/sh
# Generates this installation's credentials into the shared secrets volume.
#
# Runs as a one-shot compose service before anything else starts. Each
# secret is created once (random, 40 alphanumeric chars) and then kept, so
# restarts and upgrades reuse the same values. A non-empty environment
# variable with the upper-cased name (e.g. POSTGRES_PASSWORD) overrides the
# file — that is how you pin a value, e.g. for a database volume created by
# an older version with a known password.
#
#   docker compose run --rm secrets show   # print the Grafana admin password
set -eu

DIR=/run/secrets/cubesat
NAMES="jwt_secret_key postgres_password redis_password nats_password nats_groundstation_password grafana_admin_password"

mkdir -p "$DIR"
for name in $NAMES; do
    file="$DIR/$name"
    override=$(printenv "$(echo "$name" | tr 'a-z' 'A-Z')" || true)
    if [ -n "$override" ]; then
        printf '%s' "$override" > "$file"
        echo "secrets: $name set from environment"
    elif [ ! -s "$file" ]; then
        head -c 64 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 40 > "$file"
        echo "secrets: generated $name"
    fi
    # Services run as different UIDs (postgres, redis, grafana); the volume
    # is only mounted into this stack's containers.
    chmod 0444 "$file"
done

if [ "${1:-}" = "show" ]; then
    echo "Grafana  (http://localhost:3001)  user: admin  password: $(cat "$DIR/grafana_admin_password")"
    echo "C2 admin: docker compose exec backend cat /var/lib/cubesat/admin_bootstrap"
fi
