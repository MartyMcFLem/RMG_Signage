#!/bin/bash
set -e
# RMG Signage — Installateur unifié pour Raspberry Pi OS Lite
# Usage: sudo bash install.sh [--user <username>] [--media-dir <path>]
#
# Ce script doit être lancé depuis un clone du repo :
#   git clone https://github.com/MartyMcFLem/RMG_Signage.git
#   cd RMG_Signage && sudo bash install.sh
#
# Pour une installation en une ligne (sans clone préalable), utilisez bootstrap.sh :
#   curl -sSL https://raw.githubusercontent.com/MartyMcFLem/RMG_Signage/main/bootstrap.sh | sudo bash

# ─── Répertoire projet : priorité à la variable d'environnement (passée par bootstrap.sh),
#     sinon résolu depuis l'emplacement réel du script courant.
PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

# ─── Valeurs par défaut
SERVICE_USER="${RMG_USER:-rmg}"
SERVICE_NAME="rmg_signage.service"

# ─── Lecture des arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --user)      SERVICE_USER="$2"; shift 2 ;;
    --media-dir) MEDIA_DIR_ARG="$2"; shift 2 ;;
    *) echo "Argument inconnu: $1"; exit 1 ;;
  esac
done

MEDIA_DIR="${MEDIA_DIR_ARG:-/home/$SERVICE_USER/signage/medias}"
VENV_DIR="$PROJECT_DIR/venv"
LOG_FILE="/home/$SERVICE_USER/rmg_signage.log"

# ─── Vérification des droits root
if [ "$EUID" -ne 0 ]; then
  echo "Ce script doit être lancé avec sudo : sudo bash install.sh"
  exit 1
fi

echo ""
echo "======================================================"
echo "  RMG Signage — Installation"
echo "  Projet    : $PROJECT_DIR"
echo "  User      : $SERVICE_USER"
echo "  Médias    : $MEDIA_DIR"
echo "======================================================"
echo ""

# ─── 1. Paquets système
echo "[1/6] Installation des paquets système..."
apt-get update -qq
apt-get install -y git mpv python3-venv python3-pip fonts-dejavu-core
  # Note : fbi retiré — le splash utilise désormais mpv (--vo=drm) pour éviter
  # le conflit DRM/KMS avec le processus mpv principal (vc4-kms-v3d sur Pi OS Bookworm)

# ─── 2. Utilisateur et groupes
echo "[2/6] Configuration de l'utilisateur '$SERVICE_USER'..."
if ! id "$SERVICE_USER" &>/dev/null; then
  useradd -m -s /bin/bash "$SERVICE_USER"
  echo "  → Utilisateur '$SERVICE_USER' créé"
fi
usermod -aG video,render,input,tty "$SERVICE_USER" 2>/dev/null || true

# ─── 3. Dossiers et permissions
echo "[3/6] Création des dossiers..."
mkdir -p "$MEDIA_DIR"
mkdir -p /run/rmg_signage
chown -R "$SERVICE_USER:$SERVICE_USER" "$PROJECT_DIR" "$MEDIA_DIR"
chown "$SERVICE_USER:$SERVICE_USER" /run/rmg_signage
chmod +x "$PROJECT_DIR"/*.sh 2>/dev/null || true

# ─── 4. Virtualenv Python
echo "[4/6] Mise en place du virtualenv Python..."
if [ ! -d "$VENV_DIR" ]; then
  python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --upgrade pip -q
if [ -f "$PROJECT_DIR/requirements.txt" ]; then
  "$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements.txt" -q
fi
chown -R "$SERVICE_USER:$SERVICE_USER" "$VENV_DIR"

# ─── 5. Boot silencieux
echo "[5/6] Configuration du boot silencieux..."
CONFIG_FILE=""
CMDLINE_FILE=""
for BOOT_DIR in /boot/firmware /boot; do
  if [ -f "$BOOT_DIR/config.txt" ]; then
    CONFIG_FILE="$BOOT_DIR/config.txt"
    CMDLINE_FILE="$BOOT_DIR/cmdline.txt"
    break
  fi
done

if [ -n "$CONFIG_FILE" ]; then
  if ! grep -q "disable_splash" "$CONFIG_FILE"; then
    printf "\n# RMG Signage — boot silencieux\ndisable_splash=1\n" >> "$CONFIG_FILE"
  fi
  if [ -f "$CMDLINE_FILE" ] && ! grep -q "quiet" "$CMDLINE_FILE"; then
    cp "$CMDLINE_FILE" "$CMDLINE_FILE.bak"
    # Redirectionner les messages boot vers tty3 (écran vide pour l'utilisateur)
    sed -i 's/console=tty1/console=tty3/g' "$CMDLINE_FILE"
    # Masquer les messages kernel, le logo et le curseur clignotant
    sed -i 's/$/ quiet loglevel=3 logo.nologo vt.global_cursor_default=0 rd.systemd.show_status=false/' "$CMDLINE_FILE"
  fi
  echo "  → Boot silencieux configuré ($CONFIG_FILE)"
else
  echo "  ⚠️  /boot/config.txt introuvable — boot silencieux non appliqué (normal hors Pi)"
fi

# ─── 6. Service systemd (généré dynamiquement avec les chemins réels)
echo "[6/6] Déploiement du service systemd..."
cat > "/etc/systemd/system/$SERVICE_NAME" << EOF
[Unit]
Description=RMG Signage - Flask & MPV
After=network.target
Wants=network.target
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$PROJECT_DIR
RuntimeDirectory=rmg_signage
RuntimeDirectoryMode=0755
Environment=RMG_SIGNAGE_DIR=$PROJECT_DIR
Environment=RMG_SIGNAGE_MEDIA_DIR=$MEDIA_DIR
Environment=RMG_SIGNAGE_LOG=$LOG_FILE
Environment="PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStartPre=/bin/bash -c 'mkdir -p \$RMG_SIGNAGE_MEDIA_DIR'
ExecStartPre=+/bin/bash $PROJECT_DIR/splash_helper.sh start
ExecStart=/bin/bash $PROJECT_DIR/start_rmg_signage.sh
ExecStopPost=+/bin/bash $PROJECT_DIR/splash_helper.sh stop
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=rmg_signage
TimeoutStartSec=180

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME" || true

echo ""
echo "======================================================"
echo "  ✅ Installation terminée !"
echo "======================================================"
echo ""
echo "  Projet    : $PROJECT_DIR"
echo "  Médias    : $MEDIA_DIR"
echo "  Logs app  : $LOG_FILE"
echo "  Logs svc  : sudo journalctl -u rmg_signage -f"
echo "  Interface : http://<ip-du-pi>:5000"
echo ""
echo "  Redémarrez le Pi pour appliquer le boot silencieux :"
echo "  sudo reboot"
echo ""

exit 0
