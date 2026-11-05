#!/bin/bash
# Substitutes the DB, user and password from .env (via compose) into init.sql and executes it.
(
    set -euo pipefail

    sql_escape() { local v=${1//\\/\\\\}; printf '%s' "${v//\'/\\\'}"; }
    sed_escape() { printf '%s' "$1" | sed -e 's/[\\|&]/\\&/g'; }

    db=$(sed_escape "${PAYSTAT_DB}")
    user=$(sed_escape "${PAYSTAT_RO_USER}")
    password=$(sed_escape "$(sql_escape "${PAYSTAT_RO_PASSWORD}")")

    sed -e "s|__DB__|${db}|g" \
        -e "s|__RO_USER__|${user}|g" \
        -e "s|__RO_PASSWORD__|${password}|g" \
        /docker/init.sql | clickhouse-client --multiquery
)
