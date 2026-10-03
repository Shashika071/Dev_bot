#!/usr/bin/env bash
# Wrapper — run the full auto SSL installer.
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/auto-ssl.sh" "$@"
