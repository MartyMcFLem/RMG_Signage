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

# Signal de readiness pour splash_helper.sh.
# On attend que Flask soit réellement en écoute sur :5000 (pas juste un sleep fixe)
# afin que le splash soit visible assez longtemps et que le device DRM soit libéré
# AVANT que le mpv principal prenne la main.
log "Attente de Flask sur :5000 (max 30s)..."
FLASK_READY=0
for _i in $(seq 1 30); do
  if ! kill -0 "$PY_PID" 2>/dev/null; then
    log "⚠️  Le processus Python s'est arrêté prématurément (PID $PY_PID)"
    break
  fi
  if python3 -c \
    "import socket,sys; s=socket.socket(); s.settimeout(1); r=s.connect_ex(('127.0.0.1',5000)); s.close(); sys.exit(0 if r==0 else 1)" \
    2>/dev/null; then
    FLASK_READY=1
    log "Flask prêt après ${_i}s"
    break
  fi
  sleep 1
done

if [ "$FLASK_READY" -eq 1 ]; then
  READY_FILE="/run/rmg_signage/ready"
  if touch "$READY_FILE" 2>/dev/null; then
    log "Readiness signalée : $READY_FILE"
  else
    touch "$HOME/rmg_signage-ready" 2>/dev/null || touch "/tmp/rmg_signage-ready" 2>/dev/null || true
    log "Readiness signalée (fallback)"
  fi
elif ! kill -0 "$PY_PID" 2>/dev/null; then
  log "⚠️  Python mort avant que Flask soit prêt"
else
  log "⚠️  Flask non disponible après 30s — readiness signalée quand même"
  touch "/run/rmg_signage/ready" 2>/dev/null || touch "/tmp/rmg_signage-ready" 2>/dev/null || true
fi

wait $PY_PID
log "=== Service rmg_signage arrêté ==="
