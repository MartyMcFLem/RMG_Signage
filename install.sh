#!/bin/bash
set -e
# RMG Signage — Installateur unifié pour Raspberry Pi OS Lite
# Usage: sudo bash install.sh [--user <username>] [--media-dir <path>]
#                             [--branch <branch>] [--port <port>]
#                             [--service-name <name>]
#
# Ce script doit être lancé depuis un clone du repo :
#   git clone https://github.com/MartyMcFLem/RMG_Signage.git
#   cd RMG_Signage && sudo bash install.sh
#
# Pour une installation en une ligne (sans clone préalable), utilisez bootstrap.sh :
#   curl -sSL https://raw.githubusercontent.com/MartyMcFLem/RMG_Signage/main/bootstrap.sh | sudo bash
# Version DEV :
#   curl -sSL https://raw.githubusercontent.com/MartyMcFLem/RMG_Signage/DEV/bootstrap-dev.sh | sudo bash

# ─── Répertoire projet : priorité à la variable d'environnement (passée par bootstrap.sh),
#     sinon résolu depuis l'emplacement réel du script courant.
PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

# ─── Valeurs par défaut
SERVICE_USER="${RMG_USER:-rmg}"
SERVICE_NAME="rmg_signage"
BRANCH="main"
PORT=5000

# ─── Lecture des arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --user)         SERVICE_USER="$2"; shift 2 ;;
    --media-dir)    MEDIA_DIR_ARG="$2"; shift 2 ;;
    --branch)       BRANCH="$2"; shift 2 ;;
    --port)         PORT="$2"; shift 2 ;;
    --service-name) SERVICE_NAME="$2"; shift 2 ;;
    *) echo "Argument inconnu: $1"; exit 1 ;;
  esac
done

MEDIA_DIR="${MEDIA_DIR_ARG:-/home/$SERVICE_USER/signage/medias}"
VENV_DIR="$PROJECT_DIR/venv"
LOG_FILE="/home/$SERVICE_USER/${SERVICE_NAME}.log"

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
echo "  Branche   : $BRANCH"
echo "  Port      : $PORT"
echo "  Service   : $SERVICE_NAME"
echo "======================================================"
echo ""

# ─── 1. Paquets système
echo "[1/6] Installation des paquets système..."
apt-get update -qq
apt-get install -y git mpv python3-venv python3-pip fonts-dejavu-core plymouth plymouth-themes

# ─── 2. Utilisateur et groupes
echo "[2/6] Configuration de l'utilisateur '$SERVICE_USER'..."
if ! id "$SERVICE_USER" &>/dev/null; then
  useradd -m -s /bin/bash "$SERVICE_USER"
  echo "  → Utilisateur '$SERVICE_USER' créé"
fi
usermod -aG video,render,input,tty "$SERVICE_USER" 2>/dev/null || true

# ─── 2b. Génération du numéro de série et configuration du hostname
echo "[2b/6] Génération du numéro de série..."

_generate_serial_suffix() {
  # Méthode 1 : CPU serial du Raspberry Pi complet (chars [10:26], 16 chars hex)
  local serial
  serial=$(grep -m1 "^Serial" /proc/cpuinfo 2>/dev/null | cut -c11-26 | tr -cd '0-9a-f')
  if [ ${#serial} -eq 16 ]; then
    echo "$serial"
    return
  fi
  # Méthode 2 : UUID aléatoire (persisté dans /etc/rmg_serial)
  local serial_file="/etc/rmg_serial"
  if [ -f "$serial_file" ] && [ "$(wc -c < "$serial_file")" -ge 16 ]; then
    head -c 16 "$serial_file"
    return
  fi
  serial=$(cat /proc/sys/kernel/random/uuid 2>/dev/null | tr -d '-' | cut -c1-16)
  if [ -z "$serial" ]; then
    serial=$(date +%s%N | sha256sum | cut -c1-16)
  fi
  echo "$serial" > "$serial_file"
  echo "$serial"
}

DEVICE_SERIAL=$(_generate_serial_suffix)
NEW_HOSTNAME="rmg-sign-${DEVICE_SERIAL}"
CURRENT_HOSTNAME=$(hostname 2>/dev/null || echo "")

if [ "$CURRENT_HOSTNAME" != "$NEW_HOSTNAME" ]; then
  hostnamectl set-hostname "$NEW_HOSTNAME" 2>/dev/null || hostname "$NEW_HOSTNAME" || true
  # Mettre à jour /etc/hosts
  if grep -q "127.0.1.1" /etc/hosts 2>/dev/null; then
    sed -i "s/127\.0\.1\.1.*/127.0.1.1\t$NEW_HOSTNAME/" /etc/hosts
  else
    echo -e "127.0.1.1\t$NEW_HOSTNAME" >> /etc/hosts
  fi
  echo "  → Hostname défini : $NEW_HOSTNAME"
else
  echo "  → Hostname déjà correct : $NEW_HOSTNAME"
fi

# Autoriser le service à corriger le hostname si nécessaire (fallback Flask)
HOSTNAMECTL_PATH=$(command -v hostnamectl 2>/dev/null || echo "/usr/bin/hostnamectl")
echo "$SERVICE_USER ALL=(ALL) NOPASSWD: $HOSTNAMECTL_PATH" > /etc/sudoers.d/rmg_hostname
chmod 440 /etc/sudoers.d/rmg_hostname

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
  # Désactiver le splash arc-en-ciel du GPU Pi (≠ Plymouth)
  if ! grep -q "disable_splash" "$CONFIG_FILE"; then
    printf "\n# RMG Signage — boot silencieux\ndisable_splash=1\n" >> "$CONFIG_FILE"
  fi
  if [ -f "$CMDLINE_FILE" ] && ! grep -q "quiet" "$CMDLINE_FILE"; then
    cp "$CMDLINE_FILE" "$CMDLINE_FILE.bak"
    # Redirectionner les messages boot vers tty3 (écran vide pour l'utilisateur)
    sed -i 's/console=tty1/console=tty3/g' "$CMDLINE_FILE"
    # Masquer les messages kernel, le logo, le curseur et activer Plymouth
    sed -i 's/$/ quiet splash loglevel=3 logo.nologo vt.global_cursor_default=0 rd.systemd.show_status=false plymouth.ignore-serial-consoles/' "$CMDLINE_FILE"
  elif [ -f "$CMDLINE_FILE" ] && ! grep -q "splash" "$CMDLINE_FILE"; then
    # quiet déjà présent mais pas splash — l'ajouter
    sed -i 's/quiet/quiet splash plymouth.ignore-serial-consoles/' "$CMDLINE_FILE"
  fi
  echo "  → Boot silencieux + Plymouth configuré ($CONFIG_FILE)"
else
  echo "  ⚠️  /boot/config.txt introuvable — boot silencieux non appliqué (normal hors Pi)"
fi

# ─── 5b. Thème Plymouth (splash au boot Linux)
echo "[5b/6] Configuration du thème Plymouth..."
PLYMOUTH_THEME_DIR="/usr/share/plymouth/themes/rmg-signage"
mkdir -p "$PLYMOUTH_THEME_DIR"

# Copier l'image splash
if [ -f "$PROJECT_DIR/static/splash.png" ]; then
  cp "$PROJECT_DIR/static/splash.png" "$PLYMOUTH_THEME_DIR/splash.png"
  echo "  → splash.png copié"
fi

# Descripteur du thème
cat > "$PLYMOUTH_THEME_DIR/rmg-signage.plymouth" << 'PLYM_EOF'
[Plymouth Theme]
Name=RMG Signage
Description=RMG Signage boot splash
ModuleName=script

[script]
ImageDir=/usr/share/plymouth/themes/rmg-signage
ScriptFile=/usr/share/plymouth/themes/rmg-signage/rmg-signage.script
PLYM_EOF

# Script Plymouth (affiche splash.png centré sur fond noir)
cat > "$PLYMOUTH_THEME_DIR/rmg-signage.script" << 'PLYM_EOF'
screen_width  = Window.GetWidth();
screen_height = Window.GetHeight();

// Fond noir
bg = Sprite(Image.Fill(screen_width, screen_height, 0, 0, 0, 1.0));
bg.SetZ(-100);

// Logo centré, redimensionné si nécessaire
logo_image = Image("splash.png");
lw = logo_image.GetWidth();
lh = logo_image.GetHeight();

max_w = Math.Floor(screen_width  * 0.7);
max_h = Math.Floor(screen_height * 0.7);
if (lw > max_w || lh > max_h) {
    if ((lw * max_h) > (lh * max_w)) {
        new_h = Math.Floor(lh * max_w / lw);
        new_w = max_w;
    } else {
        new_w = Math.Floor(lw * max_h / lh);
        new_h = max_h;
    }
    logo_image = logo_image.Scale(new_w, new_h);
    lw = new_w;
    lh = new_h;
}

logo = Sprite(logo_image);
logo.SetX(Math.Floor((screen_width  - lw) / 2));
logo.SetY(Math.Floor((screen_height - lh) / 2));
PLYM_EOF

# Activer le thème et reconstruire l'initramfs
if command -v plymouth-set-default-theme &>/dev/null; then
  plymouth-set-default-theme rmg-signage -R 2>/dev/null \
    && echo "  → Thème Plymouth activé + initramfs mis à jour" \
    || echo "  ⚠️  Impossible de reconstruire l'initramfs (plymouth-set-default-theme -R)"
else
  echo "  ⚠️  plymouth-set-default-theme introuvable — Plymouth non configuré"
fi

# ─── 6. Service systemd (généré dynamiquement avec les chemins réels)
echo "[6/6] Déploiement du service systemd..."
cat > "/etc/systemd/system/${SERVICE_NAME}.service" << EOF
[Unit]
Description=RMG Signage - Flask & MPV (${BRANCH})
After=network.target plymouth.service
Wants=network.target
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$PROJECT_DIR
RuntimeDirectory=$SERVICE_NAME
RuntimeDirectoryMode=0755
Environment=RMG_SIGNAGE_DIR=$PROJECT_DIR
Environment=RMG_SIGNAGE_MEDIA_DIR=$MEDIA_DIR
Environment=RMG_SIGNAGE_LOG=$LOG_FILE
Environment=RMG_SIGNAGE_BRANCH=$BRANCH
Environment=RMG_SIGNAGE_PORT=$PORT
Environment="PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStartPre=/bin/bash -c 'mkdir -p \$RMG_SIGNAGE_MEDIA_DIR'
ExecStart=/bin/bash $PROJECT_DIR/start_rmg_signage.sh
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=$SERVICE_NAME
TimeoutStartSec=180

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
# Masquer getty@tty1 pour éviter l'affichage d'un terminal entre Plymouth et MPV
systemctl mask getty@tty1 2>/dev/null || true
systemctl enable "${SERVICE_NAME}.service"
systemctl restart "${SERVICE_NAME}.service" || true

echo ""
echo "======================================================"
echo "  ✅ Installation terminée !"
echo "======================================================"
echo ""
echo "  Projet    : $PROJECT_DIR"
echo "  Médias    : $MEDIA_DIR"
echo "  Logs app  : $LOG_FILE"
echo "  Logs svc  : sudo journalctl -u $SERVICE_NAME -f"
echo "  Série     : $NEW_HOSTNAME"
echo "  Interface : http://$NEW_HOSTNAME.local:$PORT  (ou via IP)"
echo ""
echo "  Redémarrez le Pi pour appliquer le boot silencieux :"
echo "  sudo reboot"
echo ""

exit 0
