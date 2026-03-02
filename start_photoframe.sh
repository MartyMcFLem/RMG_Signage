#!/bin/bash
# Script de démarrage du cadre photo numérique

# Fichier de log
LOG_FILE="/home/pi/photoframe.log"

# Fonction de log
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [PhotoFrame] $1" >> "$LOG_FILE"
}

log "=== Démarrage du service PhotoFrame ==="

# Attendre que X11 ou Wayland soit prêt
log "Démarrage en mode headless / kiosque (pas d'X par défaut)"
# Pour Raspberry Pi OS Lite (sans X), forçons l'absence de DISPLAY
# afin que MPV choisisse un backend framebuffer/DRM (--vo=drm)
unset DISPLAY || true
log "DISPLAY unset"
log "USER=$USER"
log "HOME=$HOME"
log "PATH=$PATH"

# Vérifier que le dossier des médias existe
mkdir -p /home/pi/cadre
log "Dossier média: /home/pi/cadre"

# Vérifier les permissions du fichier de socket
rm -f /tmp/mpv-socket 2>/dev/null
log "Socket MPV préparé"

# Chemin vers le script Python
SCRIPT_DIR="/home/pi/PhotoFrame"
SCRIPT_PATH="$SCRIPT_DIR/upload.py"

log "Lancement de: python3 $SCRIPT_PATH"

# Démarrer l'application
cd "$SCRIPT_DIR"
python3 "$SCRIPT_PATH" >> "$LOG_FILE" 2>&1 &
PY=$!
# When the python process is up and running, signal readiness so splash_helper can stop if needed
sleep 1
if [ -n "$PY" ]; then
    touch /run/photoframe-ready
fi

wait $PY

log "=== Service PhotoFrame arrêté ==="
