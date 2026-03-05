#!/bin/bash
# Simple installer pour Raspberry Pi OS Lite
# Déploie uniquement le service systemd (mode headless)

set -e

PROJECT_DIR="/home/rmg/PhotoFrame"

if [ ! -d "/home/rmg" ]; then
    echo "⚠️  Le dossier /home/rmg est introuvable. Vérifiez que l'utilisateur 'rmg' existe."
fi

echo "📦 Déploiement du service systemd (headless)"

# Installer git si absent (requis pour les mises à jour depuis GitHub)
if ! command -v git &>/dev/null; then
    echo "📥 Installation de git..."
    sudo apt-get update -qq
    sudo apt-get install -y git
fi
if [ ! -f "$PROJECT_DIR/rmg_signage.service" ]; then
    echo "Erreur: $PROJECT_DIR/rmg_signage.service introuvable" >&2
    exit 1
fi

sudo cp "$PROJECT_DIR/rmg_signage.service" /etc/systemd/system/ || {
    echo "Erreur: impossible de copier rmg_signage.service" >&2
    exit 1
}
        # Créer le dossier média et appliquer la bonne propriété
        sudo mkdir -p /home/rmg/signage/medias
        sudo chown -R rmg:rmg /home/rmg/PhotoFrame /home/rmg/signage || true

sudo systemctl daemon-reload
sudo systemctl enable rmg_signage.service
sudo systemctl restart rmg_signage.service || true

echo "✅ Service systemd déployé. Suivez les logs: sudo journalctl -u rmg_signage -f"
echo "⚙️  Vérifiez les chemins dans rmg_signage.service (User/WorkingDirectory/ExecStart)"

exit 0
