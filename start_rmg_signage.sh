#!/bin/bash
# Script de démarrage du service rmg_signage

# Fichier de log (modifiable via RMG_SIGNAGE_LOG)
LOG_FILE="${RMG_SIGNAGE_LOG:-/home/rmg/rmg_signage.log}"

# Fonction de log
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [rmg_signage] $1" >> "$LOG_FILE"
}

log "=== Démarrage du service rmg_signage ==="

# Attendre que X11 ou Wayland soit prêt
# Démarrer en mode headless (Pi OS Lite)
log "Démarrage en mode headless (Pi OS Lite)"
# Pour Raspberry Pi OS Lite (sans X), forçons l'absence de DISPLAY
# afin que MPV choisisse un backend framebuffer/DRM (--vo=drm)
unset DISPLAY || true
log "DISPLAY unset"
log "USER=$USER"
log "HOME=$HOME"
log "PATH=$PATH"

# Vérifier que le dossier des médias existe (modifiable via RMG_SIGNAGE_MEDIA_DIR)
MEDIA_DIR="${RMG_SIGNAGE_MEDIA_DIR:-/home/rmg/signage/medias}"
mkdir -p "$MEDIA_DIR"
log "Dossier média: $MEDIA_DIR"

# Vérifier les permissions du fichier de socket
rm -f /tmp/mpv-socket 2>/dev/null
log "Socket MPV préparé"

# Chemin vers le script Python (modifiable via RMG_SIGNAGE_DIR)
SCRIPT_DIR="${RMG_SIGNAGE_DIR:-/home/rmg/PhotoFrame}"
SCRIPT_PATH="$SCRIPT_DIR/upload.py"

# If a virtualenv exists in the project, activate it so dependencies (flask, etc.) are used
VENV_DIR="${SCRIPT_DIR}/venv"
if [ -f "$VENV_DIR/bin/activate" ]; then
    log "Activation du virtualenv: $VENV_DIR"
    # shellcheck disable=SC1090
    . "$VENV_DIR/bin/activate"
    log "Virtualenv activé: $(which python3)"
fi

log "Lancement de: python3 $SCRIPT_PATH"

# Démarrer l'application
cd "$SCRIPT_DIR"
python3 "$SCRIPT_PATH" >> "$LOG_FILE" 2>&1 &
PY=$!
# When the python process is up and running, signal readiness so splash_helper can stop if needed
sleep 1
if [ -n "$PY" ]; then
    # Prefer systemd RuntimeDirectory if available, otherwise fallback to HOME or /tmp
    RUNTIME_READY="/run/rmg_signage/ready"
    HOME_READY="$HOME/rmg_signage-ready"
    FALLBACK_READY="/tmp/rmg_signage-ready"

    # Try to create and touch the runtime ready file
    if mkdir -p /run/rmg_signage 2>/dev/null && touch "$RUNTIME_READY" 2>/dev/null; then
        log "Wrote readiness file: $RUNTIME_READY"
    elif [ -n "$HOME" ] && touch "$HOME_READY" 2>/dev/null; then
        log "Wrote readiness file: $HOME_READY"
    elif touch "$FALLBACK_READY" 2>/dev/null; then
        log "Wrote readiness file: $FALLBACK_READY"
    else
        log "⚠️  Could not write any readiness file (permission issue)"
    fi
fi

wait $PY

log "=== Service rmg_signage arrêté ==="
