#!/usr/bin/env bash
set -euo pipefail

: "${FINERACT_BASE_URL:=https://localhost:8443/fineract-provider/api/v1}"
: "${FINERACT_USERNAME:=mifos}"
: "${FINERACT_PASSWORD:=password}"
: "${FINERACT_TENANT:=default}"
: "${FINERACT_CLIENT_EXTERNAL_ID:=dify-demo-customer-001}"
: "${FINERACT_CLIENT_FIRSTNAME:=María Fernanda}"
: "${FINERACT_CLIENT_LASTNAME:=Demo Ecuador}"
: "${FINERACT_PRODUCT_SHORT_NAME:=DIFY}"

command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }
command -v jq >/dev/null || { echo "jq is required" >&2; exit 1; }

curl_args=(
  --silent --show-error --fail-with-body --insecure
  --user "${FINERACT_USERNAME}:${FINERACT_PASSWORD}"
  --header "Fineract-Platform-TenantId: ${FINERACT_TENANT}"
  --header "Content-Type: application/json"
)

api() {
  local method="$1"
  local path="$2"
  local body="${3:-}"
  if [[ -n "$body" ]]; then
    curl "${curl_args[@]}" -X "$method" "${FINERACT_BASE_URL%/}/${path}" --data "$body"
  else
    curl "${curl_args[@]}" -X "$method" "${FINERACT_BASE_URL%/}/${path}"
  fi
}

wait_for_core() {
  for _ in $(seq 1 60); do
    if curl "${curl_args[@]}" "${FINERACT_BASE_URL%/}/clients?limit=1" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  echo "Fineract did not become ready at ${FINERACT_BASE_URL}" >&2
  return 1
}

wait_for_core

client_response="$(api GET "clients?limit=100")"
client_id="$(jq -r --arg external_id "$FINERACT_CLIENT_EXTERNAL_ID" '
  [.pageItems[]? | select(.externalId == $external_id) | .id][0] // empty
' <<<"$client_response")"

if [[ -z "$client_id" ]]; then
  today="$(LC_ALL=C date -u '+%d %B %Y')"
  client_response="$(api POST clients "$(jq -nc \
    --arg firstname "$FINERACT_CLIENT_FIRSTNAME" \
    --arg lastname "$FINERACT_CLIENT_LASTNAME" \
    --arg external_id "$FINERACT_CLIENT_EXTERNAL_ID" \
    --arg today "$today" \
    '{firstname:$firstname, lastname:$lastname, externalId:$external_id,
      dateFormat:"dd MMMM yyyy", locale:"en", active:true,
      activationDate:$today, submittedOnDate:$today, officeId:1}')")"
  client_id="$(jq -r '.clientId // empty' <<<"$client_response")"
fi

if [[ -z "$client_id" ]]; then
  echo "Fineract did not return a client id" >&2
  exit 1
fi

products_response="$(api GET "savingsproducts?limit=100")"
product_id="$(jq -r --arg short_name "$FINERACT_PRODUCT_SHORT_NAME" '
  [.pageItems[]? | select(.shortName == $short_name) | .id][0] //
  ([.[]? | select(.shortName == $short_name) | .id][0] // empty)
' <<<"$products_response")"

if [[ -z "$product_id" ]]; then
  product_response="$(api POST savingsproducts "$(jq -nc \
    --arg short_name "$FINERACT_PRODUCT_SHORT_NAME" \
    '{name:"Dify Demo Savings", shortName:$short_name,
      description:"Synthetic savings product for the Dify financial agent demo",
      currencyCode:"USD", digitsAfterDecimal:2, inMultiplesOf:0,
      locale:"en", nominalAnnualInterestRate:"5.0",
      interestCompoundingPeriodType:1, interestPostingPeriodType:4,
      interestCalculationType:1, interestCalculationDaysInYearType:365,
      accountingRule:1}')")"
  product_id="$(jq -r '.resourceId // empty' <<<"$product_response")"
fi

if [[ -z "$product_id" ]]; then
  echo "Fineract did not return a savings product id" >&2
  exit 1
fi

accounts_response="$(api GET "clients/${client_id}/accounts")"
account_id="$(jq -r --argjson product_id "$product_id" '
  [.savingsAccounts[]? | select(.savingsProductId == $product_id) | .id][0] // empty
' <<<"$accounts_response")"

if [[ -z "$account_id" ]]; then
  today="$(LC_ALL=C date -u '+%d %B %Y')"
  account_response="$(api POST savingsaccounts "$(jq -nc \
    --argjson client_id "$client_id" \
    --argjson product_id "$product_id" \
    --arg today "$today" \
    '{clientId:$client_id, productId:$product_id, locale:"en",
      dateFormat:"dd MMMM yyyy", submittedOnDate:$today}')")"
  account_id="$(jq -r '.savingsId // .resourceId // empty' <<<"$account_response")"
fi

if [[ -z "$account_id" ]]; then
  echo "Fineract did not return a savings account id" >&2
  exit 1
fi

echo "FINERACT_DEMO_CLIENT_ID=${client_id}"
echo "FINERACT_DEMO_SAVINGS_PRODUCT_ID=${product_id}"
echo "FINERACT_DEMO_SAVINGS_ACCOUNT_ID=${account_id}"
echo "Use FINERACT_DEMO_CLIENT_ID=${client_id} with docker-compose.gateway.yaml."
