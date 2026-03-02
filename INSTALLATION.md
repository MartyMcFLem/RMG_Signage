# 🖼️ Installation du Cadre Photo au Démarrage

## 🚀 Installation Rapide

Sur votre Raspberry Pi, dans le dossier contenant vos fichiers :

```bash
# Rendre le script d'installation exécutable
chmod +x install.sh

# Lancer l'installation
./install.sh
```

Le script vous proposera 3 options :
1. **Service systemd** (recommandé) - Démarre automatiquement au boot
2. **Autostart X11** - Démarre avec la session graphique
3. **Les deux** - Maximum de fiabilité

---

## 📝 Installation Manuelle

### Option 1 : Service systemd (Recommandé)

```bash
# Copier le fichier de service
sudo cp photoframe.service /etc/systemd/system/

# Activer et démarrer le service
sudo systemctl daemon-reload
sudo systemctl enable photoframe.service
sudo systemctl start photoframe.service

# Vérifier le statut
sudo systemctl status photoframe
```

### Raspberry Pi OS Lite (sans interface graphique)

Si vous utilisez Raspberry Pi OS Lite (no GUI) — le projet peut tourner en mode kiosque
directement sur le framebuffer grâce à MPV (`--vo=drm`) et `fbi` pour le splash.

Commandes recommandées :

```bash
# Mettre à jour le système et installer dépendances
sudo apt update
sudo apt install -y python3-pip mpv fbi

# S'assurer que l'utilisateur (ici `pi`) a accès au périphérique vidéo
sudo usermod -aG video,input pi

# Copier le service et activer
sudo cp photoframe.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable photoframe.service
sudo systemctl start photoframe.service

# Suivre les logs
sudo journalctl -u photoframe -f
```

Notes importantes :
- Le fichier `photoframe.service` fourni a été adapté pour ne pas dépendre de X11/Wayland.
- `start_photoframe.sh` désactive `DISPLAY` pour forcer MPV à utiliser DRM/framebuffer.
- Si MPV échoue, consultez `/home/pi/cadre/photoframe-mpv.log` et `journalctl`.


**Commandes utiles :**
- `sudo systemctl start photoframe` - Démarrer
- `sudo systemctl stop photoframe` - Arrêter
- `sudo systemctl restart photoframe` - Redémarrer
- `sudo systemctl status photoframe` - Statut
- `sudo journalctl -u photoframe -f` - Logs en temps réel

### Option 2 : Autostart X11

```bash
# Rendre le script exécutable
chmod +x start_photoframe.sh

# Créer le dossier autostart
mkdir -p ~/.config/autostart

# Créer le fichier .desktop
cat > ~/.config/autostart/photoframe.desktop <<EOF
[Desktop Entry]
Type=Application
Name=Cadre Photo Numérique
Exec=/home/pi/Documents/start_photoframe.sh
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
EOF
```

---

## ⚙️ Configuration

**Avant d'installer, vérifiez les chemins dans les fichiers :**

### Dans `photoframe.service` :
```ini
WorkingDirectory=/home/pi/Documents  # ← Votre dossier
ExecStart=/usr/bin/python3 /home/pi/Documents/upload.py  # ← Chemin vers upload.py
```

### Dans `start_photoframe.sh` :
```bash
SCRIPT_DIR="/home/pi/Documents"  # ← Votre dossier
```

---

## 🔍 Résolution de problèmes

### Le service ne démarre pas
```bash
# Voir les logs détaillés
sudo journalctl -u photoframe -n 50

# Tester manuellement
cd /home/pi/Documents
python3 upload.py
```

### MPV ne s'affiche pas
- Vérifiez que X11 est bien lancé
- Ajoutez un délai plus long dans `start_photoframe.sh` (augmentez `sleep 5`)

### Voir les logs
```bash
# Logs du service
sudo journalctl -u photoframe -f

# Logs du script autostart
tail -f /home/pi/photoframe.log
```

---

## 🗑️ Désinstallation

```bash
# Arrêter et désactiver le service
sudo systemctl stop photoframe
sudo systemctl disable photoframe
sudo rm /etc/systemd/system/photoframe.service
sudo systemctl daemon-reload

# Supprimer l'autostart
rm ~/.config/autostart/photoframe.desktop
```

---

## 📌 Notes

- Le service attend que l'interface graphique soit prête (`graphical.target`)
- Le script se relance automatiquement en cas d'erreur (`Restart=always`)
- Les logs sont disponibles via `journalctl` ou dans `/home/pi/photoframe.log`
- Adaptez les chemins selon votre configuration
