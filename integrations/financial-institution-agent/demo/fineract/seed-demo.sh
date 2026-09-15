#!/usr/bin/env bash
set -euo pipefail

: "${FINERACT_BASE_URL:=https://localhost:8443/fineract-provider/api/v1}"
: "${FINERACT_USERNAME:=mifos}"
: "${FINERACT_PASSWORD:=password}"
: "${FINERACT_TENANT:=default}"
: "${FINERACT_CLIENTS:=juan-avila|Juan|Avila|+593999292849;angel-quiroz|Angel|Quiroz|+593985613152}"
: "${FINERACT_PRODUCT_SHORT_NAME:=DIFY}"
: "${FINERACT_PRODUCT_NAME:=Cuenta de Ahorro}"

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

products_response="$(api GET "savingsproducts?limit=100")"
product_id="$(jq -r --arg short_name "$FINERACT_PRODUCT_SHORT_NAME" '
  [.pageItems[]? | select(.shortName == $short_name) | .id][0] //
  ([.[]? | select(.shortName == $short_name) | .id][0] // empty)
' <<<"$products_response")"

if [[ -z "$product_id" ]]; then
  product_response="$(api POST savingsproducts "$(jq -nc \
    --arg short_name "$FINERACT_PRODUCT_SHORT_NAME" \
    --arg product_name "$FINERACT_PRODUCT_NAME" \
    '{name:$product_name, shortName:$short_name,
      description:"Cuenta de ahorro para clientes de la institución",
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

client_response="$(api GET "clients?limit=100")"
IFS=';' read -r -a client_specs <<< "$FINERACT_CLIENTS"

for client_spec in "${client_specs[@]}"; do
  IFS='|' read -r external_id firstname lastname mobile email <<< "$client_spec"
  if [[ -z "$external_id" || -z "$firstname" || -z "$lastname" || -z "$mobile" ]]; then
    echo "Each FINERACT_CLIENTS entry must use external-id|firstname|lastname|mobile|email" >&2
    exit 1
  fi

  client_id="$(jq -r --arg external_id "$external_id" '
    [.pageItems[]? | select(.externalId == $external_id) | .id][0] // empty
  ' <<<"$client_response")"

  if [[ -z "$client_id" ]]; then
    today="$(LC_ALL=C date -u '+%d %B %Y')"
    client_response_for_create="$(api POST clients "$(jq -nc \
      --arg firstname "$firstname" \
      --arg lastname "$lastname" \
      --arg external_id "$external_id" \
      --arg mobile "$mobile" \
      --arg email "${email:-}" \
      --arg today "$today" \
      '{firstname:$firstname, lastname:$lastname, externalId:$external_id,
        mobileNo:$mobile, legalFormId:1, dateFormat:"dd MMMM yyyy", locale:"en", active:true,
        activationDate:$today, submittedOnDate:$today, officeId:1}
       + (if $email == "" then {} else {emailAddress:$email} end)')")"
    client_id="$(jq -r '.clientId // empty' <<<"$client_response_for_create")"
  fi

  if [[ -z "$client_id" ]]; then
    echo "Fineract did not return a client id for ${external_id}" >&2
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
    echo "Fineract did not return a savings account id for ${external_id}" >&2
    exit 1
  fi

  output_key="${external_id//-/_}"
  output_key="${output_key^^}"
  echo "FINERACT_CLIENT_ID_${output_key}=${client_id}"
  echo "FINERACT_SAVINGS_ACCOUNT_ID_${output_key}=${account_id}"
done

echo "FINERACT_SAVINGS_PRODUCT_ID=${product_id}"
