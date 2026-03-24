# RMG Signage — Installation (Raspberry Pi OS Lite)

Ce document décrit l'installation sur Raspberry Pi OS Lite (headless, sans interface graphique).

## Prérequis

- Raspberry Pi (3B+, 4, 5 ou Zero 2W recommandé)
- Raspberry Pi OS Lite (Bookworm ou Bullseye) flashé sur carte SD
- Accès SSH ou clavier/écran

## Installation en une commande

> **Le dépôt doit être public** pour utiliser la méthode HTTPS sans credentials.
> Si votre fork est privé, consultez la section [Dépôt privé](#dépôt-privé) ci-dessous.

### Production (branche `main`)

```bash
curl -sSL https://raw.githubusercontent.com/MartyMcFLem/RMG_Signage/main/bootstrap.sh | sudo bash
```

### Développement (branche `DEV`)

```bash
curl -sSL https://raw.githubusercontent.com/MartyMcFLem/RMG_Signage/main/bootstrap.sh | sudo bash -s -- --dev
```

| | Production | Développement |
|---|---|---|
| Branche | `main` | `DEV` |
| Répertoire | `/opt/rmg_signage` | `/opt/rmg_signage_dev` |
| Service | `rmg_signage` | `rmg_signage_dev` |
| Port | `5000` | `5001` |

### Option B — Clone puis install

```bash
# 1. Cloner le projet
git clone https://github.com/MartyMcFLem/RMG_Signage.git
cd RMG_Signage

# 2. Lancer l'installateur (en tant que root)
sudo bash install.sh           # production
sudo bash install.sh --dev     # développement  (via bootstrap-dev.sh ou flag)
```

Ce script `install.sh` fait tout automatiquement :
- Installe les paquets système (`mpv`, `python3-venv`, `git`)
- Génère le **numéro de série** de l'appareil et configure le hostname (`rmg-sign-XXXXXXXXX`)
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

# Définir le quota média (en MB, défaut : 4096 = 4 Go)
sudo bash install.sh --media-quota 8192

# Installer sur une branche et un port spécifiques
sudo bash install.sh --branch DEV --port 5001 --service-name rmg_signage_dev

# Combiné
sudo bash install.sh --user pi --media-quota 8192
```

## Numéro de série de l'appareil

À l'installation, un numéro de série unique est automatiquement généré et appliqué comme hostname du Pi :

```
rmg-sign-XXXXXXXXXXXXXXXX
```

- Basé sur le CPU serial complet du Raspberry Pi — 16 caractères hex (priorité)
- Sinon : UUID aléatoire 16 chars persisté dans `/etc/rmg_serial`
- Le Pi est accessible sur le réseau via `rmg-sign-XXXXXXXXX.local`
- Le serial est exposé dans l'API : `GET /api/status` → champ `serial`

## Partition média dédiée

L'installation crée une **partition média isolée** (image disque loop montée sur le répertoire média).
Cela protège l'OS : même si la partition média est pleine, le système continue de fonctionner.

- Image : `/var/lib/rmg_signage/media.img`
- Licence : `/etc/rmg_signage/license.json`
- Taille par défaut : 4 Go (configurable via `--media-quota` ou licence)

### Redimensionner la partition média

```bash
# Augmenter à 8 Go
sudo bash /opt/rmg_signage/resize_media.sh --quota 8192

# Réduire à 2 Go (si les données tiennent)
sudo bash /opt/rmg_signage/resize_media.sh --quota 2048
```

Le script arrête le service, redimensionne, puis relance automatiquement.

## Boot silencieux

Après installation et reboot, le Pi démarre silencieusement :
- Pas de carré arc-en-ciel RPi
- Pas de messages kernel défilants
- Écran noir → splash Plymouth RMG → médias

Pour le splash personnalisé, déposez votre image dans `static/splash.png`
(PNG recommandé, résolution de l'écran cible).

## Emplacements importants

### Production

| Élément | Chemin |
|---|---|
| Projet | `/opt/rmg_signage` |
| Médias | `/home/rmg/signage/medias` (partition dédiée) |
| Image disque | `/var/lib/rmg_signage/media.img` |
| Licence | `/etc/rmg_signage/license.json` |
| Log service | `sudo journalctl -u rmg_signage -f` |
| Log MPV | `/home/rmg/signage/medias/rmg_signage-mpv.log` |
| Service systemd | `/etc/systemd/system/rmg_signage.service` |
| Interface web | `http://rmg-sign-XXXXXXXXX.local:5000` |

### Développement

| Élément | Chemin |
|---|---|
| Projet | `/opt/rmg_signage_dev` |
| Médias | `/home/rmg/signage/medias` |
| Log service | `sudo journalctl -u rmg_signage_dev -f` |
| Service systemd | `/etc/systemd/system/rmg_signage_dev.service` |
| Interface web | `http://rmg-sign-XXXXXXXXX.local:5001` |

## Gestion du service

```bash
# Voir les logs en temps réel
sudo journalctl -u rmg_signage -f        # production
sudo journalctl -u rmg_signage_dev -f    # développement

# Statut
sudo systemctl status rmg_signage

# Redémarrer
sudo systemctl restart rmg_signage

# Arrêter
sudo systemctl stop rmg_signage
```

## Mise à jour

Depuis l'interface web → section "Mise à jour" → bouton "Mettre à jour".

La mise à jour cible automatiquement la bonne branche (`main` ou `DEV`) selon l'environnement installé.

Ou manuellement :
```bash
cd /opt/rmg_signage       # ou /opt/rmg_signage_dev
git pull origin main      # ou DEV
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

## Licences

Le système utilise des clés de licence pour contrôler le quota de stockage média.

### Tiers disponibles

| Tier | Stockage | Medias max | Clé |
|---|---|---|---|
| Sans licence | 2 Go | 10 | -- |
| Standard | 4 Go | 100 | `RMGS-XXXXX-XXXXX-XXXXX` |
| Business | 12 Go | 1 000 | `RMGS-XXXXX-XXXXX-XXXXX` |
| Unlimited | 24 Go | Illimité | `RMGS-XXXXX-XXXXX-XXXXX` |

### Activer une licence

1. Ouvrir l'interface web → **Paramètres** → **Licence**
2. Entrer la clé et cliquer **Activer**
3. Le quota et la limite de fichiers sont immédiatement mis à jour

### Générer des clés (admin)

```bash
# Générer 1 clé standard
python3 generate_keys.py --tier standard

# Générer 10 clés business
python3 generate_keys.py --tier business --count 10

# Valider une clé
python3 generate_keys.py --validate "RMGS-XXXXX-XXXXX-XXXXX"

# Lister les tiers
python3 generate_keys.py --list-tiers
```

> **Note** : `generate_keys.py` est un outil admin, il ne devrait pas être déployé sur les appareils clients.

## Dépannage

**Le service ne démarre pas**
```bash
sudo journalctl -u rmg_signage -n 50
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
