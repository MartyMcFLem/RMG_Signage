#!/bin/bash
# Simple installer pour Raspberry Pi OS Lite
# Déploie uniquement le service systemd (mode headless)

set -e

PROJECT_DIR="/home/pi/PhotoFrame"

if [ ! -d "/home/pi" ]; then
    echo "⚠️  Ce script est prévu pour Raspberry Pi avec l'utilisateur 'pi'. Adaptez si besoin."
fi

echo "📦 Déploiement du service systemd (headless)"

# Installer git si absent (requis pour les mises à jour depuis GitHub)
if ! command -v git &>/dev/null; then
    echo "📥 Installation de git..."
    sudo apt-get update -qq
    sudo apt-get install -y git
fi
if [ ! -f "$PROJECT_DIR/photoframe.service" ]; then
    echo "Erreur: $PROJECT_DIR/photoframe.service introuvable" >&2
    exit 1
fi

sudo cp "$PROJECT_DIR/photoframe.service" /etc/systemd/system/ || {
    echo "Erreur: impossible de copier photoframe.service" >&2
    exit 1
}
        # Créer le dossier média et appliquer la bonne propriété
        sudo mkdir -p /home/pi/cadre
        sudo chown -R pi:pi /home/pi/PhotoFrame /home/pi/cadre || true

sudo systemctl daemon-reload
sudo systemctl enable photoframe.service
sudo systemctl restart photoframe.service || true

echo "✅ Service systemd déployé. Suivez les logs: sudo journalctl -u photoframe -f"
echo "⚙️  Vérifiez les chemins dans photoframe.service (User/WorkingDirectory/ExecStart)"

exit 0
