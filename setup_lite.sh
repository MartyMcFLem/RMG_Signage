#!/bin/bash
set -e
# Script d'installation pour Raspberry Pi OS Lite
# Usage: sudo bash setup_lite.sh

PROJECT_DIR="/home/pi/PhotoFrame"
VENV_DIR="$PROJECT_DIR/venv"

if [ "$EUID" -ne 0 ]; then
  echo "Ce script doit être lancé avec sudo: sudo bash setup_lite.sh"
  exit 1
fi

echo "== Mise à jour des paquets et installation des dépendances système =="
apt update
apt install -y git mpv fbi python3-venv python3-pip

echo "== Création des dossiers et permissions =="
mkdir -p /home/pi/cadre
chown -R pi:pi "$PROJECT_DIR" /home/pi/cadre || true
usermod -aG video,input pi || true

echo "== Rendre les scripts exécutables =="
chmod +x "$PROJECT_DIR"/*.sh || true

echo "== Création/activation du virtualenv =="
if [ ! -d "$VENV_DIR" ]; then
  python3 -m venv "$VENV_DIR"
fi

echo "== Installation des dépendances Python dans le venv =="
source "$VENV_DIR/bin/activate"
pip install --upgrade pip
if [ -f "$PROJECT_DIR/requirements.txt" ]; then
  pip install -r "$PROJECT_DIR/requirements.txt"
else
  pip install flask
fi
deactivate

echo "== Déploiement du service systemd =="
if [ -f "$PROJECT_DIR/photoframe.service" ]; then
  if [ -f /etc/systemd/system/photoframe.service ]; then
    cp /etc/systemd/system/photoframe.service /etc/systemd/system/photoframe.service.bak
  fi
  cp "$PROJECT_DIR/photoframe.service" /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable photoframe.service
  systemctl restart photoframe.service || true
else
  echo "Aucun fichier photoframe.service trouvé dans $PROJECT_DIR — merci de le copier manuellement." >&2
fi

echo "== Fait: le service devrait être démarré. Suivez les logs avec:" 
echo "sudo journalctl -u photoframe -f"
echo "Ou consultez /home/pi/photoframe.log et /home/pi/cadre/photoframe-mpv.log"

exit 0
