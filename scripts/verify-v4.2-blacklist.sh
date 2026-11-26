#!/usr/bin/env bash
# Compatibility alias for the unversioned diagnostic script.
set -euo pipefail
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/verify-blacklist.sh" "$@"
