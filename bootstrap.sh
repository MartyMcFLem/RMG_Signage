#!/bin/bash
set -e
# RMG Signage — Bootstrap
# Clone le repo et lance install.sh. Conçu pour être utilisé via :
#   curl -sSL https://raw.githubusercontent.com/MartyMcFLem/RMG_Signage/main/bootstrap.sh | sudo bash

REPO_URL="https://github.com/MartyMcFLem/RMG_Signage.git"
INSTALL_DIR="/opt/rmg_signage"

if [ "$EUID" -ne 0 ]; then
  echo "Ce script doit être lancé avec sudo : curl -sSL ... | sudo bash"
  exit 1
fi

echo "=== RMG Signage — Bootstrap ==="

# Installer git si absent
if ! command -v git &>/dev/null; then
  echo "Installation de git..."
  apt-get update -qq
  apt-get install -y git -qq
fi

# Cloner ou mettre à jour le repo
if [ -d "$INSTALL_DIR/.git" ]; then
  echo "Mise à jour du repo dans $INSTALL_DIR..."
  git config --global --add safe.directory "$INSTALL_DIR"
  git -C "$INSTALL_DIR" pull --ff-only origin main
else
  echo "Clonage dans $INSTALL_DIR..."
  rm -rf "$INSTALL_DIR"
  git clone "$REPO_URL" "$INSTALL_DIR"
fi

echo "Lancement de install.sh..."
# PROJECT_DIR passé explicitement en variable d'environnement au sous-processus
PROJECT_DIR="$INSTALL_DIR" bash "$INSTALL_DIR/install.sh" "$@"
