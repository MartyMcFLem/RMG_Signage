#!/bin/bash
# Helper pour afficher/effacer le splash RMG sur tty1 via mpv (DRM/KMS)
# Utilise par le service systemd en ExecStartPre / ExecStopPost.
#
# NOTE : On utilise mpv (deja requis par l'appli) plutot que fbi.
# fbi utilise l'interface legacy /dev/fb0 qui entre en conflit avec le
# --vo=drm de mpv principal (meme pipeline DRM/KMS sous vc4-kms-v3d).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SPLASH_IMG="$SCRIPT_DIR/static/splash.png"
PIDFILE="/run/rmg_signage/splash.pid"
MPV_BIN="$(command -v mpv 2>/dev/null || echo /usr/bin/mpv)"

# Emplacements possibles du fichier "ready" (coherent avec start_rmg_signage.sh)
READY_FILES=(
  "/run/rmg_signage/ready"
  "$HOME/rmg_signage-ready"
  "/tmp/rmg_signage-ready"
)

# Timeout max en secondes pour le watcher
SPLASH_TIMEOUT="${SPLASH_TIMEOUT:-120}"

_blackout_tty() {
  # Noircit tty1 et masque le curseur pour eviter les flash
  if [ -c /dev/tty1 ]; then
    printf "\033[?25l\033[40m\033[2J\033[H" > /dev/tty1 2>/dev/null || true
  fi
}

_kill_splash() {
  if [ -f "$PIDFILE" ]; then
    local pid
    pid=$(cat "$PIDFILE" 2>/dev/null)
    if [ -n "$pid" ]; then
      # Blackout tty1 AVANT de tuer mpv : quand DRM revient sur la VT,
      # le buffer est deja noir -> pas de flash du terminal
      _blackout_tty
      # Tuer proprement : SIGTERM puis SIGKILL si necessaire
      kill "$pid" >/dev/null 2>&1 || true
      # Attendre la mort effective (max 3s)
      local waited=0
      while kill -0 "$pid" 2>/dev/null && [ "$waited" -lt 30 ]; do
        sleep 0.1
        waited=$((waited + 1))
      done
      # Force kill si toujours vivant
      kill -0 "$pid" 2>/dev/null && kill -9 "$pid" >/dev/null 2>&1 || true
      rm -f "$PIDFILE"
    fi
  fi
  # Maintenir le blackout apres la mort de mpv
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
    # Nettoyer les anciens fichiers ready (evite que le watcher s'arrete immediatement
    # si un fichier ready d'une session precedente traine)
    _cleanup_ready_files

    if [ -x "$MPV_BIN" ] && [ -f "$SPLASH_IMG" ]; then
      # S'assurer que /run/rmg_signage existe (cree par RuntimeDirectory= systemd
      # mais on cree en fallback au cas ou)
      mkdir -p /run/rmg_signage 2>/dev/null || true

      # Preparer tty1 : curseur masque + fond noir avant que mpv prenne le DRM
      _blackout_tty

      # Lancer mpv en mode DRM pour afficher le splash.
      # --vo=drm     : meme backend que le mpv principal -> pas de conflit
      # --no-terminal / --really-quiet : silencieux
      # --image-display-duration=inf   : tient jusqu'au signal ready
      # --loop-file=inf                : boucle (au cas ou mpv finirait)
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
      echo "$SPLASH_PID" > "$PIDFILE"

      # Watcher en arriere-plan : attend le signal de readiness puis retire le splash.
      # Utilise un compteur en dixiemes de seconde pour un timing precis.
      (
        TIMEOUT_DS=$((SPLASH_TIMEOUT * 10))  # en dixiemes de seconde
        elapsed_ds=0
        until _ready_exists || [ "$elapsed_ds" -ge "$TIMEOUT_DS" ]; do
          sleep 0.1
          elapsed_ds=$((elapsed_ds + 1))
        done
        _kill_splash
        # Attendre la mort effective du processus (liberation DRM)
        if [ -n "$SPLASH_PID" ]; then
          local_wait=0
          while kill -0 "$SPLASH_PID" 2>/dev/null && [ "$local_wait" -lt 30 ]; do
            sleep 0.1
            local_wait=$((local_wait + 1))
          done
        fi
      ) >/dev/null 2>&1 &
    else
      # Pas de mpv ou pas de splash.png : blackout tty1 quand meme
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
