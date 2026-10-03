#!/usr/bin/env bash
set -euo pipefail

: "${GF_SMOKE_ENV_FILE:?the release gate must provide its bounded smoke env path}"
test -f container-release.json
test ! -L container-release.json
test -f "$GF_SMOKE_ENV_FILE"
test ! -L "$GF_SMOKE_ENV_FILE"

jq -er '
  .preparation == "docker/ci-prepare-smoke.sh"
  and (.smoke_environment | type == "array" and length > 0)
  and all(.smoke_environment[]; test("^[A-Z][A-Z0-9_]{0,63}=[A-Za-z0-9_.,:/@+-]{1,255}$"))
  | if . then . else error("invalid bounded smoke environment") end
' container-release.json >/dev/null
jq -r '.smoke_environment[]' container-release.json > "$GF_SMOKE_ENV_FILE"
