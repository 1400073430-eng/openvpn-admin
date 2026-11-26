#!/usr/bin/env bash
# Compatibility entry point; destructive confirmation is in the canonical script.
set -euo pipefail
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scripts/uninstall.sh" "$@"
