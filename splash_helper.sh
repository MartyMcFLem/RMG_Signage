#!/bin/bash
# Helper pour afficher/effacer le splash RMG sur tty1 via mpv (DRM/KMS)
# Utilisé par le service systemd en ExecStartPre / ExecStopPost.
#
# NOTE : On utilise mpv (déjà requis par l'appli) plutôt que fbi.
# fbi utilise l'interface legacy /dev/fb0 qui entre en conflit avec le
# --vo=drm de mpv principal (même pipeline DRM/KMS sous vc4-kms-v3d).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SPLASH_IMG="$SCRIPT_DIR/static/splash.png"
PIDFILE="/run/rmg_signage/splash.pid"
MPV_BIN="$(command -v mpv 2>/dev/null || echo /usr/bin/mpv)"

# Emplacements possibles du fichier "ready" (cohérent avec start_rmg_signage.sh)
READY_FILES=(
  "/run/rmg_signage/ready"
  "$HOME/rmg_signage-ready"
  "/tmp/rmg_signage-ready"
)

_kill_splash() {
  if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    # Blackout tty1 AVANT de tuer mpv : quand DRM revient sur la VT,
    # le buffer est déjà noir → pas de flash du terminal
    if [ -c /dev/tty1 ]; then
      printf "\033[?25l\033[40m\033[2J\033[H" > /dev/tty1 2>/dev/null || true
    fi
    # Tuer proprement : SIGTERM puis SIGKILL si nécessaire
    kill "$PID" >/dev/null 2>&1 || true
    sleep 0.3
    kill -0 "$PID" >/dev/null 2>&1 && kill -9 "$PID" >/dev/null 2>&1 || true
    rm -f "$PIDFILE"
  fi
  # Maintenir le blackout : curseur masqué + écran noir (évite le flash si DRM déjà libéré)
  if [ -c /dev/tty1 ]; then
    printf "\033[?25l\033[40m\033[2J\033[H" > /dev/tty1 2>/dev/null || true
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
    if [ -x "$MPV_BIN" ] && [ -f "$SPLASH_IMG" ]; then
      # S'assurer que /run/rmg_signage existe (créé par RuntimeDirectory= systemd
      # mais on crée en fallback au cas où)
      mkdir -p /run/rmg_signage 2>/dev/null || true

      # Préparer tty1 : curseur masqué + fond noir avant que mpv prenne le DRM
      # (évite le flash du terminal si le boot n'a pas encore noirci l'écran)
      if [ -c /dev/tty1 ]; then
        printf "\033[?25l\033[40m\033[2J\033[H" > /dev/tty1 2>/dev/null || true
      fi

      # Lancer mpv en mode DRM pour afficher le splash.
      # --vo=drm     : même backend que le mpv principal → pas de conflit
      # --no-terminal / --really-quiet : silencieux
      # --image-display-duration=inf   : tient jusqu'au signal ready
      # --loop-file=inf                : boucle (au cas où mpv finirait)
      nohup "$MPV_BIN" \
        --fs \
        --no-terminal \
        --no-osc \
        --no-input-default-bindings \
        --vo=drm \
        --really-quiet \
        --image-display-duration=inf \
        --loop-file=inf \
        "$SPLASH_IMG" >/dev/null 2>&1 &
      SPLASH_PID=$!
      echo $SPLASH_PID > "$PIDFILE"

      # Watcher en arrière-plan : attend le signal de readiness puis retire le splash
      # Attend aussi que le processus soit vraiment mort avant d'exit
      # (garantit que le device DRM est libéré avant que le mpv principal démarre)
      (
        TIMEOUT=120
        ELAPSED=0
        until _ready_exists || [ "$ELAPSED" -ge "$TIMEOUT" ]; do
          sleep 0.5
          ELAPSED=$((ELAPSED + 1))
        done
        _kill_splash
        # Attendre la mort effective du processus (libération DRM)
        if [ -n "$SPLASH_PID" ]; then
          for _w in $(seq 1 10); do
            kill -0 "$SPLASH_PID" 2>/dev/null || break
            sleep 0.3
          done
        fi
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
