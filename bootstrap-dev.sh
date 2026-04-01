#!/bin/bash
# RMG Signage -- Bootstrap DEV (raccourci)
# Equivalent a : curl -sSL .../bootstrap.sh | sudo bash -s -- --dev
#
# Usage :
#   curl -sSL https://raw.githubusercontent.com/MartyMcFLem/RMG_Signage/DEV/bootstrap-dev.sh | sudo bash
#
# Ce script telecharge et execute bootstrap.sh avec le flag --dev.
# Compatible avec l'execution via pipe (curl | bash) et directe.

set -e

# Quand execute via curl | bash, BASH_SOURCE n'est pas defini.
# On telecharge donc bootstrap.sh directement depuis GitHub.
BOOTSTRAP_URL="https://raw.githubusercontent.com/MartyMcFLem/RMG_Signage/DEV/bootstrap.sh"

if [ "$EUID" -ne 0 ]; then
  echo "Ce script doit etre lance avec sudo : curl -sSL ... | sudo bash"
  exit 1
fi

# Verifier que curl ou wget est disponible
if command -v curl &>/dev/null; then
  bash <(curl -sSL "$BOOTSTRAP_URL") --dev "$@"
elif command -v wget &>/dev/null; then
  bash <(wget -qO- "$BOOTSTRAP_URL") --dev "$@"
else
  echo "Erreur : curl ou wget requis"
  exit 1
fi
