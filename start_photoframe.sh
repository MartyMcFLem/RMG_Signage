#!/bin/bash
# Script de démarrage du cadre photo numérique

# Fichier de log
LOG_FILE="/home/pi/photoframe.log"

# Fonction de log
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [PhotoFrame] $1" >> "$LOG_FILE"
}

log "=== Démarrage du service PhotoFrame ==="

# Attendre que X11/Wayland soit prêt (augmentez si nécessaire)
log "Attente du serveur graphique..."
for i in {1..30}; do
    if [ -S /tmp/.X11-unix/X0 ] || [ -S /tmp/.X11-unix/X1 ]; then
        log "Serveur graphique détecté"
        break
    fi
    sleep 1
done
sleep 5

# Vérifier que DISPLAY est défini
if [ -z "$DISPLAY" ]; then
    log "DISPLAY non défini, détection..."
    # Essayer de détecter le display
    DISPLAY=$(ps aux 2>/dev/null | grep -m1 'Xvfb\|/X\|Xwayland' | grep -oP ':\d+' | head -1)
    if [ -z "$DISPLAY" ]; then
        DISPLAY=":0"
    fi
    export DISPLAY
fi

log "DISPLAY=$DISPLAY"
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
python3 "$SCRIPT_PATH" >> "$LOG_FILE" 2>&1

log "=== Service PhotoFrame arrêté ==="
