# RMG Signage — Installation (Raspberry Pi OS Lite)

Ce document décrit l'installation sur Raspberry Pi OS Lite (headless, sans interface graphique).

## Prérequis

- Raspberry Pi (3B+, 4, 5 ou Zero 2W recommandé)
- Raspberry Pi OS Lite (Bookworm ou Bullseye) flashé sur carte SD
- Accès SSH ou clavier/écran

## Installation en une commande

> **Le dépôt doit être public** pour utiliser la méthode HTTPS sans credentials.
> Si votre fork est privé, consultez la section [Dépôt privé](#dépôt-privé) ci-dessous.

### Option A — Bootstrap (tout-en-un, recommandé)

Une seule commande : télécharge et exécute l'installateur directement.

```bash
curl -sSL https://raw.githubusercontent.com/MartyMcFLem/RMG_Signage/main/bootstrap.sh | sudo bash
```

### Option B — Clone puis install

```bash
# 1. Cloner le projet
git clone https://github.com/MartyMcFLem/RMG_Signage.git
cd RMG_Signage

# 2. Lancer l'installateur (en tant que root)
sudo bash install.sh
```

C'est tout. Le script `install.sh` fait tout automatiquement :
- Installe les paquets système (`mpv`, `fbi`, `python3-venv`, `git`)
- Crée les dossiers nécessaires
- Met en place le virtualenv Python et installe les dépendances
- **Configure le boot silencieux** (supprime le splash RPi et les messages kernel)
- Génère et déploie le service systemd avec les chemins réels du projet

### Options disponibles

```bash
# Changer l'utilisateur système (défaut : rmg)
sudo bash install.sh --user pi

# Changer le dossier des médias
sudo bash install.sh --media-dir /mnt/usb/medias

# Combiné
sudo bash install.sh --user pi --media-dir /mnt/usb/medias
```

## Boot silencieux

Après installation et reboot, le Pi démarre silencieusement :
- Pas de carré arc-en-ciel RPi
- Pas de messages kernel défilants
- Écran noir → splash RMG (si `static/splash.png` existe) → médias

Pour le splash personnalisé, déposez votre image dans `static/splash.png`
(PNG recommandé, résolution de l'écran cible).

## Emplacements importants

| Élément | Chemin |
|---|---|
| Projet | Auto-détecté (chemin du `git clone`) |
| Médias | `/home/rmg/signage/medias` |
| Log application | `/home/rmg/rmg_signage.log` |
| Log MPV | `/home/rmg/signage/medias/rmg_signage-mpv.log` |
| Service systemd | `/etc/systemd/system/rmg_signage.service` |
| Interface web | `http://<ip-du-pi>:5000` |

## Gestion du service

```bash
# Voir les logs en temps réel
sudo journalctl -u rmg_signage -f

# Statut
sudo systemctl status rmg_signage

# Redémarrer
sudo systemctl restart rmg_signage

# Arrêter
sudo systemctl stop rmg_signage
```

## Mise à jour

Depuis l'interface web → section "Mise à jour" → bouton "Mettre à jour".

Ou manuellement :
```bash
cd /chemin/vers/RMG_Signage
git pull origin main
sudo systemctl restart rmg_signage
```

## Dépôt privé

Si vous travaillez sur un fork privé, utilisez une clé SSH deploy :

```bash
# Générer une clé SSH dédiée (sans passphrase pour l'automatisation)
ssh-keygen -t ed25519 -C "rmg-signage-deploy" -f ~/.ssh/rmg_deploy -N ""

# Afficher la clé publique → copiez-la dans GitHub :
# Settings → Deploy keys → Add deploy key (lecture seule suffit)
cat ~/.ssh/rmg_deploy.pub

# Cloner via SSH
git clone git@github.com:MartyMcFLem/RMG_Signage.git
```

## Dépannage

**Le service ne démarre pas**
```bash
sudo journalctl -u rmg_signage -n 50
cat /home/rmg/rmg_signage.log
```

**MPV ne lit pas les médias**
```bash
cat /home/rmg/signage/medias/rmg_signage-mpv.log
```

**Réinstaller proprement**
```bash
sudo systemctl stop rmg_signage
sudo bash install.sh
```

