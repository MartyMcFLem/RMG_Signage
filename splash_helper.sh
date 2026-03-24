#!/bin/bash
# Helper pour afficher/effacer le splash RMG sur tty1 via mpv (DRM/KMS)
# Utilise par le service systemd en ExecStartPre / ExecStopPost.
#
# mpv --vo=drm affiche l'image en plein ecran et gere automatiquement
# le scaling a la resolution native de l'ecran connecte (pas besoin
# de detecter manuellement la resolution).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SPLASH_IMG="$SCRIPT_DIR/static/splash.png"
PIDFILE="/run/rmg_signage/splash.pid"
MPV_BIN="$(command -v mpv 2>/dev/null || echo /usr/bin/mpv)"

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
      # Attendre la mort effective (max 3s)
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

    if [ -x "$MPV_BIN" ] && [ -f "$SPLASH_IMG" ]; then
      mkdir -p /run/rmg_signage 2>/dev/null || true
      _blackout_tty

      # mpv --vo=drm detecte automatiquement la resolution de l'ecran connecte
      # et scale l'image pour remplir l'ecran (--panscan=0 = fit, image entiere visible).
      # --video-unscaled=no force le scaling meme si l'image est plus petite que l'ecran.
      nohup "$MPV_BIN" \
        --fs \
        --no-terminal \
        --no-osc \
        --no-input-default-bindings \
        --vo=drm \
        --really-quiet \
        --image-display-duration=inf \
        --loop-file=inf \
        --panscan=0.0 \
        --video-unscaled=no \
        --background=0.0/0.0/0.0 \
        "$SPLASH_IMG" >/dev/null 2>&1 &
      SPLASH_PID=$!
      echo "$SPLASH_PID" > "$PIDFILE"

      # Watcher : attend le signal de readiness puis tue le splash
      (
        TIMEOUT_DS=$((SPLASH_TIMEOUT * 10))
        elapsed_ds=0
        until _ready_exists || [ "$elapsed_ds" -ge "$TIMEOUT_DS" ]; do
          sleep 0.1
          elapsed_ds=$((elapsed_ds + 1))
        done
        _kill_splash
        # Attendre la liberation effective du DRM
        if [ -n "$SPLASH_PID" ]; then
          local_wait=0
          while kill -0 "$SPLASH_PID" 2>/dev/null && [ "$local_wait" -lt 30 ]; do
            sleep 0.1
            local_wait=$((local_wait + 1))
          done
        fi
      ) >/dev/null 2>&1 &
    else
      _blackout_tty
    fi
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
