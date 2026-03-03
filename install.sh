#!/bin/bash
# Simple installer pour Raspberry Pi OS Lite
# Déploie uniquement le service systemd (mode headless)

set -e

PROJECT_DIR="/home/pi/PhotoFrame"

if [ ! -d "/home/pi" ]; then
    echo "⚠️  Ce script est prévu pour Raspberry Pi avec l'utilisateur 'pi'. Adaptez si besoin."
fi

echo "📦 Déploiement du service systemd (headless)"
if [ ! -f "$PROJECT_DIR/photoframe.service" ]; then
    echo "Erreur: $PROJECT_DIR/photoframe.service introuvable" >&2
    exit 1
fi

sudo cp "$PROJECT_DIR/photoframe.service" /etc/systemd/system/ || {
    echo "Erreur: impossible de copier photoframe.service" >&2
    exit 1
}
# Ensure MPV_ROTATE is set in the installed systemd unit (default 180°)
# Remove any existing line then insert after PHOTOFRAME_LOG or append if not found
sudo sed -i '/^Environment=MPV_ROTATE=/d' /etc/systemd/system/photoframe.service || true
if sudo grep -q '^Environment=PHOTOFRAME_LOG=' /etc/systemd/system/photoframe.service; then
    sudo sed -i '/^Environment=PHOTOFRAME_LOG=/a Environment=MPV_ROTATE=180' /etc/systemd/system/photoframe.service
else
    echo 'Environment=MPV_ROTATE=180' | sudo tee -a /etc/systemd/system/photoframe.service >/dev/null
fi
        # Créer le dossier média et appliquer la bonne propriété
        sudo mkdir -p /home/pi/cadre
        sudo chown -R inloc:inloc /home/pi/PhotoFrame /home/pi/cadre || true

sudo systemctl daemon-reload
sudo systemctl enable photoframe.service
sudo systemctl restart photoframe.service || true

echo "✅ Service systemd déployé. Suivez les logs: sudo journalctl -u photoframe -f"
echo "⚙️  Vérifiez les chemins dans photoframe.service (User/WorkingDirectory/ExecStart)"

exit 0
