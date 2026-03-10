#!/bin/bash
set -e
# RMG Signage — Bootstrap
# Clone le repo et lance install.sh. Conçu pour être utilisé via :
#   curl -sSL https://raw.githubusercontent.com/MartyMcFLem/RMG_Signage/main/bootstrap.sh | sudo bash
#
# Options :
#   --dev          Installe la version DEV (branche DEV, port 5001, service rmg_signage_dev)
#   (aucune)       Installe la version production (branche main, port 5000, service rmg_signage)
#
# Exemples :
#   curl -sSL .../bootstrap.sh | sudo bash              # production
#   curl -sSL .../bootstrap.sh | sudo bash -s -- --dev  # développement

REPO_URL="https://github.com/MartyMcFLem/RMG_Signage.git"

# ─── Valeurs par défaut (production)
BRANCH="main"
INSTALL_DIR="/opt/rmg_signage"
SERVICE_NAME="rmg_signage"
PORT=5000

# ─── Lecture des arguments
FORWARD_ARGS=()
while [[ $# -gt 0 ]]; do
  case $1 in
    --dev)
      BRANCH="DEV"
      INSTALL_DIR="/opt/rmg_signage_dev"
      SERVICE_NAME="rmg_signage_dev"
      PORT=5001
      shift
      ;;
    *) FORWARD_ARGS+=("$1"); shift ;;
  esac
done

if [ "$EUID" -ne 0 ]; then
  echo "Ce script doit être lancé avec sudo : curl -sSL ... | sudo bash"
  exit 1
fi

echo "=== RMG Signage — Bootstrap (${BRANCH}) ==="
echo "  Répertoire : $INSTALL_DIR"
echo "  Service    : $SERVICE_NAME"
echo "  Port       : $PORT"
echo ""

# Installer git si absent
if ! command -v git &>/dev/null; then
  echo "Installation de git..."
  apt-get update -qq
  apt-get install -y git -qq
fi

# Cloner ou mettre à jour le repo sur la bonne branche
if [ -d "$INSTALL_DIR/.git" ]; then
  echo "Mise à jour du repo dans $INSTALL_DIR (branche $BRANCH)..."
  git config --global --add safe.directory "$INSTALL_DIR"
  git -C "$INSTALL_DIR" fetch origin
  git -C "$INSTALL_DIR" checkout "$BRANCH"
  git -C "$INSTALL_DIR" reset --hard "origin/$BRANCH"
else
  echo "Clonage de la branche $BRANCH dans $INSTALL_DIR..."
  rm -rf "$INSTALL_DIR"
  git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi

echo "Lancement de install.sh..."
PROJECT_DIR="$INSTALL_DIR" bash "$INSTALL_DIR/install.sh" \
  --branch "$BRANCH" \
  --port "$PORT" \
  --service-name "$SERVICE_NAME" \
  "${FORWARD_ARGS[@]}"
