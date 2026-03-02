# 🖼️ PhotoFrame — Installation (Raspberry Pi OS Lite)

Ce document décrit l'installation et le dépannage pour un usage headless
sur Raspberry Pi OS Lite (sans serveur graphique). L'installation fournie
déploie un service systemd qui lance le script de démarrage.

## Installation rapide

Depuis le dossier du projet sur le Raspberry Pi :

```bash
# Rendre le script exécutable (optionnel)
chmod +x install.sh

# Déployer le service systemd
sudo ./install.sh
```

Suivre les logs :

```bash
sudo journalctl -u photoframe -f
```

## Dépendances système recommandées

```bash
sudo apt update
sudo apt install -y mpv fbi python3-venv python3-pip
sudo usermod -aG video,input pi
```

## Virtualenv (optionnel)

```bash
python3 -m venv /home/pi/PhotoFrame/venv
source /home/pi/PhotoFrame/venv/bin/activate
pip install -r /home/pi/PhotoFrame/requirements.txt
deactivate
```

## Emplacements importants

- Service systemd : `/etc/systemd/system/photoframe.service` (vérifiez `User`, `WorkingDirectory`, `ExecStart`)
- Dossier médias : `/home/pi/cadre` (modifiable via `PHOTOFRAME_MEDIA_DIR`)
- Script de démarrage : `start_photoframe.sh`

## Dépannage

- Voir les logs du service : `sudo journalctl -u photoframe -f`
- Tester manuellement :

```bash
cd /home/pi/PhotoFrame
python3 upload.py
```

---

Si vous souhaitez réactiver une option graphique (autostart/.desktop), dites‑le et je
préparerai une branche séparée contenant les fichiers et instructions correspondants.
