#!/bin/bash
# Helper pour afficher/effacer le splash RMG sur tty1 via fbi (framebuffer)
# Utilisé par le service systemd en ExecStartPre / ExecStopPost.

# L'image splash est cherchée dans static/ du projet courant (chemin relatif au script)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SPLASH_IMG="$SCRIPT_DIR/static/splash.png"
PIDFILE="/run/rmg_signage/splash.pid"

# Emplacements possibles du fichier "ready" (cohérent avec start_rmg_signage.sh)
READY_FILES=(
  "/run/rmg_signage/ready"
  "$HOME/rmg_signage-ready"
  "/tmp/rmg_signage-ready"
)

_kill_splash() {
  if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    kill "$PID" >/dev/null 2>&1 || true
    rm -f "$PIDFILE"
  fi
  if [ -c /dev/tty1 ]; then
    chvt 1 >/dev/null 2>&1 || true
    printf "\033c" > /dev/tty1 || true
  fi
}

_ready_exists() {
  for f in "${READY_FILES[@]}"; do
    [ -f "$f" ] && return 0
  done
  return 1
}

case "$1" in
  start)
    if [ -x "/usr/bin/fbi" ] && [ -f "$SPLASH_IMG" ]; then
      chvt 1 >/dev/null 2>&1 || true
      nohup /usr/bin/fbi -T 1 -noverbose -a "$SPLASH_IMG" >/dev/null 2>&1 &
      echo $! > "$PIDFILE"
      # Watcher en arrière-plan : attend le signal de readiness puis retire le splash
      (
        TIMEOUT=120
        ELAPSED=0
        until _ready_exists || [ "$ELAPSED" -ge "$TIMEOUT" ]; do
          sleep 0.5
          ELAPSED=$((ELAPSED + 1))
        done
        _kill_splash
      ) >/dev/null 2>&1 &
    fi
    ;;
  stop)
    _kill_splash
    ;;
  *)
    echo "Usage: $0 {start|stop}"
    exit 2
    ;;
esac
