#!/usr/bin/env bash
set -euo pipefail
umask 077
# game 자동 배포와 같은 잠금을 공유합니다. 후보 검사부터 drain까지 직렬화합니다.
if [ "${SWAP_LOCK_HELD:-}" != 1 ]; then
    exec env SWAP_LOCK_HELD=1 flock -n -E 75 "${LOCK_FILE:-/tmp/game-swap.lock}" "$0" "$@"
fi
[ "${GROUP_LABEL:?}" = wordonline.group=dev-game ]
: "${LEARNED_BOT_PLAN:?}" "${LEARNED_BOT_SERVER_TOKEN_FILE:?}"
test -s "$LEARNED_BOT_SERVER_TOKEN_FILE"
pending="${LEARNED_BOT_PLAN}.pending"
python3 /usr/local/lib/learned-bot/release.py prepare "$1" --output "$pending"
# drain 중인 이전 슬롯은 ACTIVE로 되돌릴 수 없습니다. 같은 계획의 재시도만 허용합니다.
if [ "$(docker ps --filter "label=${GROUP_LABEL}" -q | wc -l)" -gt 1 ]; then
    test -f "$LEARNED_BOT_PLAN"
    [ "$(jq -r .identity "$pending")" = "$(jq -r .identity "$LEARNED_BOT_PLAN")" ]
fi
# 후보 검증 실패 시 기존 계획을 건드리지 않습니다. 교체 중단은 같은 계획으로 재시도합니다.
if [ -f "$LEARNED_BOT_PLAN" ] && [ "$(jq -r .identity "$pending")" != "$(jq -r .identity "$LEARNED_BOT_PLAN")" ]; then
    cp "$LEARNED_BOT_PLAN" "${LEARNED_BOT_PLAN}.previous"
fi
mv "$pending" "$LEARNED_BOT_PLAN"
exec /usr/local/bin/swap.sh
