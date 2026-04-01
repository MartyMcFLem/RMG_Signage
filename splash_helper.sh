#!/bin/bash
# Helper pour afficher/effacer le splash RMG sur tty1 via fbi (framebuffer)
# Utilise par le service systemd en ExecStartPre / ExecStopPost.
#
# fbi (paquet fbida) affiche l'image directement sur le framebuffer /dev/fb0
# sans serveur graphique (X11/Wayland).  Si fbi n'est pas disponible, l'écran
# est simplement mis à noir.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SPLASH_IMG="$SCRIPT_DIR/static/splash.png"
PIDFILE="/run/rmg_signage/splash.pid"
FBI_BIN="$(command -v fbi 2>/dev/null || echo "")"

READY_FILES=(
  "/run/rmg_signage/ready"
  "$HOME/rmg_signage-ready"
  "/tmp/rmg_signage-ready"
)

SPLASH_TIMEOUT="${SPLASH_TIMEOUT:-120}"

_blackout_tty() {
  if [ -c /dev/tty1 ]; then
    printf "\033[?25l\033[40m\033[2J\033[H" > /dev/tty1 2>/dev/null || true
  fi
}

_kill_splash() {
  if [ -f "$PIDFILE" ]; then
    local pid
    pid=$(cat "$PIDFILE" 2>/dev/null)
    if [ -n "$pid" ]; then
      _blackout_tty
      kill "$pid" >/dev/null 2>&1 || true
      local waited=0
      while kill -0 "$pid" 2>/dev/null && [ "$waited" -lt 30 ]; do
        sleep 0.1
        waited=$((waited + 1))
      done
      kill -0 "$pid" 2>/dev/null && kill -9 "$pid" >/dev/null 2>&1 || true
      rm -f "$PIDFILE"
    fi
  fi
  _blackout_tty
}

_ready_exists() {
  local f
  for f in "${READY_FILES[@]}"; do
    [ -f "$f" ] && return 0
  done
  return 1
}

_cleanup_ready_files() {
  local f
  for f in "${READY_FILES[@]}"; do
    rm -f "$f" 2>/dev/null || true
  done
}

case "$1" in
  start)
    _cleanup_ready_files
    mkdir -p /run/rmg_signage 2>/dev/null || true
    _blackout_tty

    if [ -n "$FBI_BIN" ] && [ -f "$SPLASH_IMG" ]; then
      # fbi : affichage de l'image sur le framebuffer en plein écran
      # -T 1  : virtuel terminal 1
      # -noverbose : pas de texte superposé
      # -a    : auto-fit (adapte l'image à la résolution de l'écran)
      nohup "$FBI_BIN" -T 1 -noverbose -a "$SPLASH_IMG" >/dev/null 2>&1 &
      SPLASH_PID=$!
      echo "$SPLASH_PID" > "$PIDFILE"
    fi

    # Watcher : attend le signal de readiness puis tue le splash
    (
      TIMEOUT_DS=$((SPLASH_TIMEOUT * 10))
      elapsed_ds=0
      until _ready_exists || [ "$elapsed_ds" -ge "$TIMEOUT_DS" ]; do
        sleep 0.1
        elapsed_ds=$((elapsed_ds + 1))
      done
      _kill_splash
    ) >/dev/null 2>&1 &
    ;;
  stop)
    _kill_splash
    _cleanup_ready_files
    ;;
  *)
    echo "Usage: $0 {start|stop}"
    exit 2
    ;;
esac

