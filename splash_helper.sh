#!/bin/bash
# Helper to display/clear a splash image on tty1 using fbi (framebuffer imageviewer)
SPLASH_IMG="/home/pi/PhotoFrame/static/splash.png"
PIDFILE="/run/photoframe-splash.pid"

case "$1" in
  start)
    if [ -x "/usr/bin/fbi" ] && [ -f "$SPLASH_IMG" ]; then
      # Switch to tty1 and display image
      chvt 1 >/dev/null 2>&1 || true
      nohup /usr/bin/fbi -T 1 -noverbose -a "$SPLASH_IMG" >/dev/null 2>&1 &
      FBIPID=$!
      echo $FBIPID > "$PIDFILE"
      # Start a watcher that will remove the splash when PhotoFrame signals readiness
      (
        while [ ! -f /run/photoframe/ready ]; do
          sleep 0.5
        done
        # kill the fbi process and cleanup
        if [ -f "$PIDFILE" ]; then
          PID=$(cat "$PIDFILE")
          kill "$PID" >/dev/null 2>&1 || true
          rm -f "$PIDFILE"
        fi
        # clear tty1
        if [ -c /dev/tty1 ]; then
          chvt 1 >/dev/null 2>&1 || true
          printf "\033c" > /dev/tty1 || true
        fi
      ) >/dev/null 2>&1 &
    fi
    ;;
  stop)
    if [ -f "$PIDFILE" ]; then
      PID=$(cat "$PIDFILE")
      kill "$PID" >/dev/null 2>&1 || true
      rm -f "$PIDFILE"
      # clear tty1
      if [ -c /dev/tty1 ]; then
        chvt 1 >/dev/null 2>&1 || true
        printf "\033c" > /dev/tty1 || true
      fi
    fi
    ;;
  *)
    echo "Usage: $0 {start|stop}"
    exit 2
    ;;
esac
