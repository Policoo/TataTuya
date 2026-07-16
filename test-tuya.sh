#!/usr/bin/env bash
set -euo pipefail

BASE_URL="https://openapi.tuyaeu.com"
DEVICE_ID="bfd45fd0ecb9a92b417ybx"

read -r -p "Tuya Access ID: " TUYA_CLIENT_ID
read -r -s -p "Tuya Access Secret: " TUYA_SECRET
echo

EMPTY_SHA256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

now_ms() {
  python3 -c 'import time; print(time.time_ns() // 1_000_000)'
}

# ------------------------------------------------------------
# 1. Get a fresh Tuya access token
# ------------------------------------------------------------

TOKEN_PATH="/v1.0/token?grant_type=1"
TIMESTAMP="$(now_ms)"

STRING_TO_SIGN="$(printf 'GET\n%s\n\n%s' "$EMPTY_SHA256" "$TOKEN_PATH")"
SIGN_PAYLOAD="${TUYA_CLIENT_ID}${TIMESTAMP}${STRING_TO_SIGN}"

TOKEN_SIGN="$(
  printf '%s' "$SIGN_PAYLOAD" \
    | openssl dgst -sha256 -hmac "$TUYA_SECRET" -hex \
    | awk '{print toupper($NF)}'
)"

TOKEN_RESPONSE="$(
  curl -sS \
    -H "client_id: $TUYA_CLIENT_ID" \
    -H "sign: $TOKEN_SIGN" \
    -H "t: $TIMESTAMP" \
    -H "sign_method: HMAC-SHA256" \
    -H "lang: en" \
    "${BASE_URL}${TOKEN_PATH}"
)"

echo "Token response:"
echo "$TOKEN_RESPONSE" | jq .

ACCESS_TOKEN="$(echo "$TOKEN_RESPONSE" | jq -r '.result.access_token')"

if [[ -z "$ACCESS_TOKEN" || "$ACCESS_TOKEN" == "null" ]]; then
  echo "Could not get an access token."
  exit 1
fi

# ------------------------------------------------------------
# 2. Read the live status of the meter
# ------------------------------------------------------------

STATUS_PATH="/v1.0/iot-03/devices/${DEVICE_ID}/status"
TIMESTAMP="$(now_ms)"

STRING_TO_SIGN="$(printf 'GET\n%s\n\n%s' "$EMPTY_SHA256" "$STATUS_PATH")"
SIGN_PAYLOAD="${TUYA_CLIENT_ID}${ACCESS_TOKEN}${TIMESTAMP}${STRING_TO_SIGN}"

STATUS_SIGN="$(
  printf '%s' "$SIGN_PAYLOAD" \
    | openssl dgst -sha256 -hmac "$TUYA_SECRET" -hex \
    | awk '{print toupper($NF)}'
)"

STATUS_RESPONSE="$(
  curl -sS \
    -H "client_id: $TUYA_CLIENT_ID" \
    -H "access_token: $ACCESS_TOKEN" \
    -H "sign: $STATUS_SIGN" \
    -H "t: $TIMESTAMP" \
    -H "sign_method: HMAC-SHA256" \
    -H "lang: en" \
    "${BASE_URL}${STATUS_PATH}"
)"

echo
echo "Device status:"
echo "$STATUS_RESPONSE" | jq .
