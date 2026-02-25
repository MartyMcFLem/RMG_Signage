#!/bin/bash
# Script d'installation automatique pour le cadre photo numérique

echo "🖼️  Installation du cadre photo numérique au démarrage"
echo ""

# Vérifier qu'on est sur Raspberry Pi
if [ ! -d "/home/pi" ]; then
    echo "⚠️  Ce script est conçu pour Raspberry Pi avec l'utilisateur 'pi'"
    echo "   Modifiez les chemins si nécessaire"
fi

echo "📂 Quelle méthode préférez-vous ?"
echo ""
echo "1) Service systemd (recommandé - démarre automatiquement)"
echo "2) Autostart X11 (démarre avec la session graphique)"
echo "3) Les deux (maximum de fiabilité)"
echo ""
read -p "Votre choix (1/2/3): " choice

case $choice in
    1|3)
        echo ""
        echo "📦 Installation du service systemd..."
        
        # Copier le fichier de service
        sudo cp photoframe.service /etc/systemd/system/
        
        # Recharger systemd
        sudo systemctl daemon-reload
        
        # Activer le service
        sudo systemctl enable photoframe.service
        
        echo "✅ Service installé et activé"
        echo ""
        echo "Commandes utiles:"
        echo "  sudo systemctl start photoframe      # Démarrer maintenant"
        echo "  sudo systemctl stop photoframe       # Arrêter"
        echo "  sudo systemctl status photoframe     # Voir le statut"
        echo "  sudo journalctl -u photoframe -f     # Voir les logs en temps réel"
        echo ""
        echo "⚠️  Si vous mettez à jour le fichier .service ultérieurement, relancez :"
        echo "  sudo cp photoframe.service /etc/systemd/system/"
        echo "  sudo systemctl daemon-reload"
        echo "  sudo systemctl restart photoframe"
        ;&
esac

case $choice in
    2|3)
        echo ""
        echo "📦 Installation de l'autostart X11..."
        
        # Créer le dossier autostart s'il n'existe pas
        mkdir -p ~/.config/autostart
        
        # Rendre le script exécutable
        chmod +x start_photoframe.sh
        
        # Créer le fichier .desktop
        cat > ~/.config/autostart/photoframe.desktop <<EOF
[Desktop Entry]
Type=Application
Name=Cadre Photo Numérique
Exec=$(pwd)/start_photoframe.sh
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
EOF
        
        echo "✅ Autostart configuré"
        echo ""
        echo "Le cadre photo démarrera automatiquement avec votre session"
        ;;
esac

echo ""
echo "🎉 Installation terminée !"
echo ""
echo "⚙️  N'oubliez pas de vérifier les chemins dans les fichiers:"
echo "   - start_photoframe.sh (ligne SCRIPT_DIR)"
echo "   - photoframe.service (ligne WorkingDirectory et ExecStart)"
echo ""
echo "🔄 Redémarrez votre Raspberry Pi pour tester"
