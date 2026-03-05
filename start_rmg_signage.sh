#!/bin/bash
# Script de démarrage du service rmg_signage
# Exécuté par systemd — ne pas lancer manuellement.

LOG_FILE="${RMG_SIGNAGE_LOG:-/home/rmg/rmg_signage.log}"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') [rmg_signage] $1" >> "$LOG_FILE"
}

log "=== Démarrage du service rmg_signage ==="

# Mode headless (Pi OS Lite sans X11) : on s'assure que DISPLAY n'est pas défini
# afin que MPV choisisse un backend DRM/framebuffer.
unset DISPLAY 2>/dev/null || true
log "USER=$USER | HOME=$HOME"

# Dossier des médias (créé ici en dernier recours, normalement fait par le service ExecStartPre)
MEDIA_DIR="${RMG_SIGNAGE_MEDIA_DIR:-/home/rmg/signage/medias}"
mkdir -p "$MEDIA_DIR"
log "Dossier média : $MEDIA_DIR"

# Nettoyage du socket MPV
rm -f /tmp/mpv-socket 2>/dev/null || true

# Répertoire et script Python
SCRIPT_DIR="${RMG_SIGNAGE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
SCRIPT_PATH="$SCRIPT_DIR/upload.py"

# Activation du virtualenv si présent
VENV_DIR="$SCRIPT_DIR/venv"
if [ -f "$VENV_DIR/bin/activate" ]; then
  # shellcheck disable=SC1090
  . "$VENV_DIR/bin/activate"
  log "Virtualenv activé : $(which python3)"
fi

log "Lancement : python3 $SCRIPT_PATH"
cd "$SCRIPT_DIR"
python3 "$SCRIPT_PATH" >> "$LOG_FILE" 2>&1 &
PY_PID=$!

# Signal de readiness pour splash_helper.sh (RuntimeDirectory=rmg_signage garantit /run/rmg_signage)
sleep 1
if kill -0 "$PY_PID" 2>/dev/null; then
  READY_FILE="/run/rmg_signage/ready"
  if touch "$READY_FILE" 2>/dev/null; then
    log "Readiness signalée : $READY_FILE"
  else
    # Fallbacks si /run/rmg_signage n'est pas disponible
    touch "$HOME/rmg_signage-ready" 2>/dev/null || touch "/tmp/rmg_signage-ready" 2>/dev/null || true
    log "Readiness signalée (fallback)"
  fi
else
  log "⚠️  Le processus Python ne semble pas avoir démarré (PID $PY_PID)"
fi

wait $PY_PID
log "=== Service rmg_signage arrêté ==="
