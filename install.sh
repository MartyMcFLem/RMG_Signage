#!/bin/bash
set -e
# RMG Signage — Installateur unifié pour Raspberry Pi OS Lite
# Usage: sudo bash install.sh [--user <username>] [--media-dir <path>]
#                             [--branch <branch>] [--port <port>]
#                             [--service-name <name>] [--media-quota <MB>]
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
MEDIA_QUOTA_MB=""

# ─── Lecture des arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --user)         SERVICE_USER="$2"; shift 2 ;;
    --media-dir)    MEDIA_DIR_ARG="$2"; shift 2 ;;
    --branch)       BRANCH="$2"; shift 2 ;;
    --port)         PORT="$2"; shift 2 ;;
    --service-name) SERVICE_NAME="$2"; shift 2 ;;
    --media-quota)  MEDIA_QUOTA_MB="$2"; shift 2 ;;
    *) echo "Argument inconnu: $1"; exit 1 ;;
  esac
done

MEDIA_DIR="${MEDIA_DIR_ARG:-/home/$SERVICE_USER/signage/medias}"
VENV_DIR="$PROJECT_DIR/venv"
LOG_FILE="/home/$SERVICE_USER/${SERVICE_NAME}.log"
LICENSE_DIR="/etc/rmg_signage"
LICENSE_FILE="$LICENSE_DIR/license.json"
MEDIA_IMG="/var/lib/rmg_signage/media.img"

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
echo "[1/8] Installation des paquets système..."
apt-get update -qq
apt-get install -y git mpv python3-venv python3-pip fonts-dejavu-core plymouth plymouth-themes

# ─── 2. Utilisateur et groupes
echo "[2/8] Configuration de l'utilisateur '$SERVICE_USER'..."
if ! id "$SERVICE_USER" &>/dev/null; then
  useradd -m -s /bin/bash "$SERVICE_USER"
  echo "  → Utilisateur '$SERVICE_USER' créé"
fi
usermod -aG video,render,input,tty "$SERVICE_USER" 2>/dev/null || true

# ─── 2b. Génération du numéro de série et configuration du hostname
echo "[2b/8] Génération du numéro de série..."

_generate_serial_suffix() {
  local serial
  serial=$(grep -m1 "^Serial" /proc/cpuinfo 2>/dev/null | cut -c11-26 | tr -cd '0-9a-f')
  if [ ${#serial} -eq 16 ]; then
    echo "$serial"
    return
  fi
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
  if grep -q "127.0.1.1" /etc/hosts 2>/dev/null; then
    sed -i "s/127\.0\.1\.1.*/127.0.1.1\t$NEW_HOSTNAME/" /etc/hosts
  else
    echo -e "127.0.1.1\t$NEW_HOSTNAME" >> /etc/hosts
  fi
  echo "  → Hostname défini : $NEW_HOSTNAME"
else
  echo "  → Hostname déjà correct : $NEW_HOSTNAME"
fi

# Autoriser le service à corriger le hostname et redémarrer
rm -f /etc/sudoers.d/rmg_hostname 2>/dev/null || true
HOSTNAMECTL_PATH=$(command -v hostnamectl 2>/dev/null || echo "/usr/bin/hostnamectl")
SYSTEMCTL_PATH=$(command -v systemctl 2>/dev/null || echo "/usr/bin/systemctl")
cat > /etc/sudoers.d/rmg_signage << SUDOEOF
$SERVICE_USER ALL=(ALL) NOPASSWD: $HOSTNAMECTL_PATH
$SERVICE_USER ALL=(ALL) NOPASSWD: $SYSTEMCTL_PATH restart $SERVICE_NAME
SUDOEOF
chmod 440 /etc/sudoers.d/rmg_signage

# ─── 3. Licence et partition média
echo "[3/8] Configuration du stockage média..."
mkdir -p "$LICENSE_DIR"
mkdir -p "$(dirname "$MEDIA_IMG")"
mkdir -p "$MEDIA_DIR"

# Déterminer le quota média
# Priorité : argument --media-quota > licence existante > défaut (4096 MB = 4 Go)
DEFAULT_QUOTA_MB=4096
if [ -n "$MEDIA_QUOTA_MB" ]; then
  QUOTA_MB="$MEDIA_QUOTA_MB"
elif [ -f "$LICENSE_FILE" ]; then
  QUOTA_MB=$(python3 -c "import json; print(json.load(open('$LICENSE_FILE')).get('media_quota_mb', $DEFAULT_QUOTA_MB))" 2>/dev/null || echo "$DEFAULT_QUOTA_MB")
else
  QUOTA_MB="$DEFAULT_QUOTA_MB"
fi

# Valider que le quota est un nombre > 0
if ! [[ "$QUOTA_MB" =~ ^[0-9]+$ ]] || [ "$QUOTA_MB" -lt 64 ]; then
  echo "  ⚠️  Quota invalide ($QUOTA_MB MB), utilisation du défaut ($DEFAULT_QUOTA_MB MB)"
  QUOTA_MB="$DEFAULT_QUOTA_MB"
fi

# Créer/mettre à jour le fichier de licence
if [ -f "$LICENSE_FILE" ]; then
  # Mettre à jour seulement le quota si --media-quota a été passé
  if [ -n "$MEDIA_QUOTA_MB" ]; then
    python3 -c "
import json
try:
    with open('$LICENSE_FILE', 'r') as f:
        lic = json.load(f)
except:
    lic = {}
lic['media_quota_mb'] = $QUOTA_MB
with open('$LICENSE_FILE', 'w') as f:
    json.dump(lic, f, indent=2)
" 2>/dev/null
  fi
else
  cat > "$LICENSE_FILE" << LICEOF
{
  "tier": "standard",
  "media_quota_mb": $QUOTA_MB,
  "created": "$(date -Iseconds)"
}
LICEOF
fi
chmod 644 "$LICENSE_FILE"
echo "  → Licence : $LICENSE_FILE (quota média : ${QUOTA_MB} MB)"

# Créer l'image disque si elle n'existe pas
if [ ! -f "$MEDIA_IMG" ]; then
  echo "  → Création de l'image disque média (${QUOTA_MB} MB)..."
  # Utiliser fallocate (rapide) si disponible, sinon dd
  if command -v fallocate &>/dev/null; then
    fallocate -l "${QUOTA_MB}M" "$MEDIA_IMG"
  else
    dd if=/dev/zero of="$MEDIA_IMG" bs=1M count="$QUOTA_MB" status=progress
  fi
  mkfs.ext4 -q -F -L rmg_media "$MEDIA_IMG"
  echo "  → Image formatée (ext4, label=rmg_media)"
else
  # L'image existe déjà — vérifier si un redimensionnement est nécessaire
  CURRENT_SIZE_MB=$(du -m "$MEDIA_IMG" | cut -f1)
  if [ "$CURRENT_SIZE_MB" -ne "$QUOTA_MB" ]; then
    echo "  → Image existante (${CURRENT_SIZE_MB} MB) différente du quota (${QUOTA_MB} MB)"
    echo "    Utilisez resize_media.sh pour redimensionner :"
    echo "    sudo bash $PROJECT_DIR/resize_media.sh"
  else
    echo "  → Image disque existante (${QUOTA_MB} MB) — OK"
  fi
fi

# Monter l'image si pas déjà montée
if ! mountpoint -q "$MEDIA_DIR" 2>/dev/null; then
  mount -o loop,noatime "$MEDIA_IMG" "$MEDIA_DIR"
  echo "  → Image montée sur $MEDIA_DIR"
else
  echo "  → $MEDIA_DIR déjà monté"
fi

# Ajouter à fstab si pas déjà présent
FSTAB_LINE="$MEDIA_IMG $MEDIA_DIR ext4 loop,noatime,nofail 0 2"
if ! grep -qF "$MEDIA_IMG" /etc/fstab 2>/dev/null; then
  echo "" >> /etc/fstab
  echo "# RMG Signage — partition média" >> /etc/fstab
  echo "$FSTAB_LINE" >> /etc/fstab
  echo "  → Entrée fstab ajoutée"
else
  echo "  → Entrée fstab déjà présente"
fi

# ─── 4. Dossiers et permissions
echo "[4/8] Création des dossiers..."
mkdir -p /run/rmg_signage
chown -R "$SERVICE_USER:$SERVICE_USER" "$PROJECT_DIR" "$MEDIA_DIR"
chown "$SERVICE_USER:$SERVICE_USER" /run/rmg_signage
chmod +x "$PROJECT_DIR"/*.sh 2>/dev/null || true

# ─── 5. Virtualenv Python
echo "[5/8] Mise en place du virtualenv Python..."
if [ ! -d "$VENV_DIR" ]; then
  python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --upgrade pip -q
if [ -f "$PROJECT_DIR/requirements.txt" ]; then
  "$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements.txt" -q
fi
chown -R "$SERVICE_USER:$SERVICE_USER" "$VENV_DIR"

# ─── 6. Boot silencieux
echo "[6/8] Configuration du boot silencieux..."
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
    sed -i 's/console=tty1/console=tty3/g' "$CMDLINE_FILE"
    sed -i 's/$/ quiet splash loglevel=3 logo.nologo vt.global_cursor_default=0 rd.systemd.show_status=false plymouth.ignore-serial-consoles/' "$CMDLINE_FILE"
  elif [ -f "$CMDLINE_FILE" ] && ! grep -q "splash" "$CMDLINE_FILE"; then
    sed -i 's/quiet/quiet splash plymouth.ignore-serial-consoles/' "$CMDLINE_FILE"
  fi
  echo "  → Boot silencieux + Plymouth configuré ($CONFIG_FILE)"
else
  echo "  ⚠️  /boot/config.txt introuvable — boot silencieux non appliqué (normal hors Pi)"
fi

# ─── 6b. Thème Plymouth (splash au boot Linux)
echo "[6b/8] Configuration du thème Plymouth..."
PLYMOUTH_THEME_DIR="/usr/share/plymouth/themes/rmg-signage"
mkdir -p "$PLYMOUTH_THEME_DIR"

if [ -f "$PROJECT_DIR/static/splash.png" ]; then
  cp "$PROJECT_DIR/static/splash.png" "$PLYMOUTH_THEME_DIR/splash.png"
  echo "  → splash.png copié"
fi

cat > "$PLYMOUTH_THEME_DIR/rmg-signage.plymouth" << 'PLYM_EOF'
[Plymouth Theme]
Name=RMG Signage
Description=RMG Signage boot splash
ModuleName=script

[script]
ImageDir=/usr/share/plymouth/themes/rmg-signage
ScriptFile=/usr/share/plymouth/themes/rmg-signage/rmg-signage.script
PLYM_EOF

cat > "$PLYMOUTH_THEME_DIR/rmg-signage.script" << 'PLYM_EOF'
screen_width  = Window.GetWidth();
screen_height = Window.GetHeight();

// Fallback si les dimensions sont nulles (peut arriver tôt au boot)
if (screen_width  < 1) { screen_width  = 1920; }
if (screen_height < 1) { screen_height = 1080; }

// Fond noir plein écran
bg = Sprite(Image.Fill(screen_width, screen_height, 0, 0, 0, 1.0));
bg.SetZ(-100);

// Logo : scale to fit (conserver le ratio, occuper max 90 % de l'écran)
logo_image = Image("splash.png");
lw = logo_image.GetWidth();
lh = logo_image.GetHeight();

max_w = Math.Floor(screen_width  * 0.9);
max_h = Math.Floor(screen_height * 0.9);

if (lw > 0 && lh > 0) {
    if (lw > max_w || lh > max_h) {
        if ((lw * max_h) > (lh * max_w)) {
            new_w = max_w;
            new_h = Math.Floor(lh * max_w / lw);
        } else {
            new_h = max_h;
            new_w = Math.Floor(lw * max_h / lh);
        }
        logo_image = logo_image.Scale(new_w, new_h);
        lw = new_w;
        lh = new_h;
    }
}

logo = Sprite(logo_image);
logo.SetX(Math.Floor((screen_width  - lw) / 2));
logo.SetY(Math.Floor((screen_height - lh) / 2));
PLYM_EOF

if command -v plymouth-set-default-theme &>/dev/null; then
  plymouth-set-default-theme rmg-signage -R 2>/dev/null \
    && echo "  → Thème Plymouth activé + initramfs mis à jour" \
    || echo "  ⚠️  Impossible de reconstruire l'initramfs (plymouth-set-default-theme -R)"
else
  echo "  ⚠️  plymouth-set-default-theme introuvable — Plymouth non configuré"
fi

# ─── 7. Service systemd (généré dynamiquement avec les chemins réels)
echo "[7/8] Déploiement du service systemd..."
cat > "/etc/systemd/system/${SERVICE_NAME}.service" << EOF
[Unit]
Description=RMG Signage - Flask & MPV (${BRANCH})
After=network.target plymouth.service local-fs.target
Wants=network.target
RequiresMountsFor=$MEDIA_DIR
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
Environment=RMG_SIGNAGE_SERVICE=$SERVICE_NAME
Environment=RMG_SIGNAGE_LICENSE=$LICENSE_FILE
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
systemctl mask getty@tty1 2>/dev/null || true
systemctl enable "${SERVICE_NAME}.service"

# ─── 8. Vérification et résumé
echo "[8/8] Vérification finale..."

# Vérifier que le montage fonctionne
if mountpoint -q "$MEDIA_DIR" 2>/dev/null; then
  MEDIA_USED=$(df -BM "$MEDIA_DIR" --output=used | tail -1 | tr -d ' M')
  MEDIA_AVAIL=$(df -BM "$MEDIA_DIR" --output=avail | tail -1 | tr -d ' M')
  echo "  → Partition média OK : ${MEDIA_USED} MB utilisés, ${MEDIA_AVAIL} MB disponibles sur ${QUOTA_MB} MB"
else
  echo "  ⚠️  Partition média non montée — vérifiez manuellement"
fi

systemctl restart "${SERVICE_NAME}.service" || true

echo ""
echo "======================================================"
echo "  ✅ Installation terminée !"
echo "======================================================"
echo ""
echo "  Projet    : $PROJECT_DIR"
echo "  Médias    : $MEDIA_DIR (partition dédiée ${QUOTA_MB} MB)"
echo "  Licence   : $LICENSE_FILE"
echo "  Logs app  : $LOG_FILE"
echo "  Logs svc  : sudo journalctl -u $SERVICE_NAME -f"
echo "  Série     : $NEW_HOSTNAME"
echo "  Interface : http://$NEW_HOSTNAME.local:$PORT  (ou via IP)"
echo ""
echo "  Pour changer le quota média :"
echo "  sudo bash $PROJECT_DIR/resize_media.sh --quota <MB>"
echo ""
echo "  Redémarrez le Pi pour appliquer le boot silencieux :"
echo "  sudo reboot"
echo ""

exit 0
