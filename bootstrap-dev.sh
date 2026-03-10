#!/bin/bash
# RMG Signage — Bootstrap DEV (raccourci)
# Équivalent à : curl -sSL .../bootstrap.sh | sudo bash -s -- --dev
#
# Usage :
#   curl -sSL https://raw.githubusercontent.com/MartyMcFLem/RMG_Signage/DEV/bootstrap-dev.sh | sudo bash

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/bootstrap.sh" --dev "$@"
