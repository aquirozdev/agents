#!/usr/bin/env bash
set -eu

BASE_URL="${BANKING_GATEWAY_BASE_URL:?Set BANKING_GATEWAY_BASE_URL to the institution gateway}"

curl --fail --silent --show-error \
  --header "Accept: application/json" \
  "${BASE_URL%/}/v1/health"

if [ -n "${BANKING_GATEWAY_TOKEN:-}" ]; then
  curl --fail --silent --show-error \
    --header "Accept: application/json" \
    --header "Authorization: Bearer ${BANKING_GATEWAY_TOKEN}" \
    "${BASE_URL%/}/v1/capabilities" >/dev/null
  printf '\nCapabilities endpoint is reachable\n'
fi

printf '\nGateway is reachable at %s\n' "${BASE_URL%/}"
