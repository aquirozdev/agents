#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BANKING_E2E_BASE_URL:-http://127.0.0.1:8080}"
CUSTOMER_TOKEN="${BANKING_GATEWAY_TOKEN:-local-demo-token}"
PUBLIC_TOKEN="${BANKING_PUBLIC_GATEWAY_TOKEN:-local-public-token}"
IDENTITY_SECRET="${BANKING_DIFY_IDENTITY_SECRET:-local-dify-identity-secret}"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

request_status() {
  local output="$1"
  shift
  curl --max-time 15 -sS -o "$output" -w '%{http_code}' "$@"
}

assert_status() {
  local expected="$1"
  local actual="$2"
  local description="$3"
  if [[ "$actual" != "$expected" ]]; then
    printf 'FAIL %s: expected %s, got %s\n' "$description" "$expected" "$actual" >&2
    cat "$4" >&2 || true
    exit 1
  fi
  printf 'PASS %s\n' "$description"
}

health_status="$(request_status "$WORK_DIR/health.json" "$BASE_URL/v1/health")"
assert_status 200 "$health_status" "gateway health" "$WORK_DIR/health.json"

public_capabilities_status="$(request_status "$WORK_DIR/public-capabilities.json" \
  -H "Authorization: Bearer $PUBLIC_TOKEN" "$BASE_URL/v1/capabilities")"
assert_status 200 "$public_capabilities_status" "public capabilities" "$WORK_DIR/public-capabilities.json"
python3 - "$WORK_DIR/public-capabilities.json" <<'PY'
import json
import sys

capabilities = json.load(open(sys.argv[1], encoding="utf-8"))
assert capabilities["savings_products"] is True
assert capabilities["loan_products"] is True
assert capabilities["accounts"] is False
assert capabilities["account_transactions"] is False
assert capabilities["loans"] is False
assert capabilities["transfers"] is False
PY
printf 'PASS public capability least privilege\n'

public_private_status="$(request_status "$WORK_DIR/public-private.json" \
  -H "Authorization: Bearer $PUBLIC_TOKEN" "$BASE_URL/v1/me/accounts")"
assert_status 403 "$public_private_status" "public agent blocked from private data" "$WORK_DIR/public-private.json"

start_status="$(request_status "$WORK_DIR/auth-start.json" \
  -H 'Content-Type: application/json' \
  -d '{"identifier":"+593 999 292 849","channel":"web","delivery":"sms"}' \
  "$BASE_URL/channels/auth/start")"
assert_status 200 "$start_status" "OTP challenge creation" "$WORK_DIR/auth-start.json"
challenge_id="$(python3 - "$WORK_DIR/auth-start.json" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["challengeId"])
PY
)"

verify_status="$(request_status "$WORK_DIR/auth-verify.json" \
  -H 'Content-Type: application/json' \
  -d "{\"challengeId\":\"$challenge_id\",\"code\":\"123456\"}" \
  "$BASE_URL/channels/auth/verify-otp")"
assert_status 200 "$verify_status" "OTP verification" "$WORK_DIR/auth-verify.json"
python3 - "$WORK_DIR/auth-verify.json" <<'PY'
import json
import sys

session = json.load(open(sys.argv[1], encoding="utf-8"))
assert session["sessionState"] == "VERIFIED"
assert session["identityAssertion"].startswith("banking:v1:")
assert session["difyIdentitySignature"].startswith("sha256=")
PY
printf 'PASS verified session assertion\n'

identity="$(python3 - "$WORK_DIR/auth-verify.json" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["identityAssertion"])
PY
)"
signature="$(python3 - "$identity" "$IDENTITY_SECRET" <<'PY'
import hashlib
import hmac
import sys
print("sha256=" + hmac.new(sys.argv[2].encode(), sys.argv[1].encode(), hashlib.sha256).hexdigest())
PY
)"

customer_status="$(request_status "$WORK_DIR/customer-accounts.json" \
  -H "Authorization: Bearer $CUSTOMER_TOKEN" \
  -H "X-Dify-End-User-ID: $identity" \
  -H "X-Dify-End-User-Signature: $signature" \
  "$BASE_URL/v1/me/accounts")"
assert_status 200 "$customer_status" "authenticated customer accounts" "$WORK_DIR/customer-accounts.json"

transfer_status="$(request_status "$WORK_DIR/transfer.json" \
  -H "Authorization: Bearer $CUSTOMER_TOKEN" \
  -H "X-Dify-End-User-ID: $identity" \
  -H "X-Dify-End-User-Signature: $signature" \
  -H 'Idempotency-Key: e2e-transfer-001' \
  -H 'Content-Type: application/json' \
  -d '{"sourceAccountId":"account-demo-001","beneficiaryId":"beneficiary-demo-001","amount":{"amount":10,"currency":"USD"},"consent":{"accepted":true,"version":"e2e"}}' \
  "$BASE_URL/v1/transfers")"
assert_status 404 "$transfer_status" "money movement absent from customer gateway policy" "$WORK_DIR/transfer.json"

printf 'E2E banking agent flow passed against %s\n' "$BASE_URL"
