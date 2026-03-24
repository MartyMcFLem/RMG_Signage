#!/bin/bash
set -e
# RMG Signage — Redimensionnement de la partition média
# Usage: sudo bash resize_media.sh [--quota <MB>]
#
# Sans argument, lit le quota depuis /etc/rmg_signage/license.json.
# Avec --quota, met à jour la licence ET redimensionne.
#
# Le service rmg_signage est arrêté pendant l'opération, puis redémarré.

LICENSE_FILE="/etc/rmg_signage/license.json"
MEDIA_IMG="/var/lib/rmg_signage/media.img"
DEFAULT_QUOTA_MB=4096
NEW_QUOTA_MB=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --quota) NEW_QUOTA_MB="$2"; shift 2 ;;
    *) echo "Usage: $0 [--quota <MB>]"; exit 1 ;;
  esac
done

if [ "$EUID" -ne 0 ]; then
  echo "Ce script doit être lancé avec sudo"
  exit 1
fi

if [ ! -f "$MEDIA_IMG" ]; then
  echo "Erreur : image média introuvable ($MEDIA_IMG)"
  echo "Lancez d'abord install.sh pour créer l'image."
  exit 1
fi

# Déterminer le quota cible
if [ -n "$NEW_QUOTA_MB" ]; then
  QUOTA_MB="$NEW_QUOTA_MB"
elif [ -f "$LICENSE_FILE" ]; then
  QUOTA_MB=$(python3 -c "import json; print(json.load(open('$LICENSE_FILE')).get('media_quota_mb', $DEFAULT_QUOTA_MB))" 2>/dev/null || echo "$DEFAULT_QUOTA_MB")
else
  echo "Erreur : ni --quota ni licence trouvée"
  exit 1
fi

if ! [[ "$QUOTA_MB" =~ ^[0-9]+$ ]] || [ "$QUOTA_MB" -lt 64 ]; then
  echo "Erreur : quota invalide ($QUOTA_MB MB, minimum 64 MB)"
  exit 1
fi

# Taille actuelle de l'image
CURRENT_SIZE_BYTES=$(stat -c%s "$MEDIA_IMG" 2>/dev/null || stat -f%z "$MEDIA_IMG" 2>/dev/null)
CURRENT_SIZE_MB=$((CURRENT_SIZE_BYTES / 1024 / 1024))
TARGET_SIZE_MB="$QUOTA_MB"

echo ""
echo "======================================================"
echo "  RMG Signage — Redimensionnement partition média"
echo "  Image       : $MEDIA_IMG"
echo "  Taille act. : ${CURRENT_SIZE_MB} MB"
echo "  Cible       : ${TARGET_SIZE_MB} MB"
echo "======================================================"
echo ""

if [ "$CURRENT_SIZE_MB" -eq "$TARGET_SIZE_MB" ]; then
  echo "La partition est déjà à la taille cible. Rien à faire."
  exit 0
fi

# Trouver le point de montage
MOUNT_POINT=$(findmnt -no TARGET "$MEDIA_IMG" 2>/dev/null || true)
if [ -z "$MOUNT_POINT" ]; then
  # Chercher dans fstab
  MOUNT_POINT=$(grep "$MEDIA_IMG" /etc/fstab 2>/dev/null | awk '{print $2}' || true)
fi

# Trouver le service à arrêter
SERVICE_NAME=""
for svc in rmg_signage rmg_signage_dev; do
  if systemctl is-active --quiet "$svc" 2>/dev/null; then
    SERVICE_NAME="$svc"
    break
  fi
done

# Arrêter le service
if [ -n "$SERVICE_NAME" ]; then
  echo "Arrêt du service $SERVICE_NAME..."
  systemctl stop "$SERVICE_NAME" 2>/dev/null || true
  sleep 2
fi

# Démonter la partition
if [ -n "$MOUNT_POINT" ] && mountpoint -q "$MOUNT_POINT" 2>/dev/null; then
  echo "Démontage de $MOUNT_POINT..."
  umount "$MOUNT_POINT" || { echo "Erreur : impossible de démonter $MOUNT_POINT"; exit 1; }
fi

# Vérifier le système de fichiers avant redimensionnement
echo "Vérification du système de fichiers..."
e2fsck -f -y "$MEDIA_IMG" 2>/dev/null || true

if [ "$TARGET_SIZE_MB" -gt "$CURRENT_SIZE_MB" ]; then
  # ─── AGRANDISSEMENT ───
  echo "Agrandissement : ${CURRENT_SIZE_MB} MB → ${TARGET_SIZE_MB} MB..."
  truncate -s "${TARGET_SIZE_MB}M" "$MEDIA_IMG"
  resize2fs "$MEDIA_IMG"
  echo "  ✓ Image agrandie"
else
  # ─── RÉDUCTION ───
  echo "Réduction : ${CURRENT_SIZE_MB} MB → ${TARGET_SIZE_MB} MB..."

  # Vérifier que les données tiennent dans la nouvelle taille
  # (resize2fs refusera si les données ne tiennent pas)
  USED_BLOCKS=$(dumpe2fs -h "$MEDIA_IMG" 2>/dev/null | grep "Block count" | head -1 | awk '{print $NF}')
  FREE_BLOCKS=$(dumpe2fs -h "$MEDIA_IMG" 2>/dev/null | grep "Free blocks" | head -1 | awk '{print $NF}')
  BLOCK_SIZE=$(dumpe2fs -h "$MEDIA_IMG" 2>/dev/null | grep "Block size" | awk '{print $NF}')

  if [ -n "$USED_BLOCKS" ] && [ -n "$FREE_BLOCKS" ] && [ -n "$BLOCK_SIZE" ]; then
    USED_DATA_BLOCKS=$((USED_BLOCKS - FREE_BLOCKS))
    USED_DATA_MB=$(( (USED_DATA_BLOCKS * BLOCK_SIZE) / 1024 / 1024 ))
    # Marge de 10% pour les métadonnées ext4
    MIN_SIZE_MB=$(( USED_DATA_MB + (USED_DATA_MB / 10) + 32 ))
    if [ "$TARGET_SIZE_MB" -lt "$MIN_SIZE_MB" ]; then
      echo "Erreur : impossible de réduire à ${TARGET_SIZE_MB} MB"
      echo "         Les données occupent ~${USED_DATA_MB} MB (minimum requis : ${MIN_SIZE_MB} MB)"
      # Remonter et relancer
      if [ -n "$MOUNT_POINT" ]; then mount -o loop,noatime "$MEDIA_IMG" "$MOUNT_POINT"; fi
      if [ -n "$SERVICE_NAME" ]; then systemctl start "$SERVICE_NAME" 2>/dev/null || true; fi
      exit 1
    fi
  fi

  # Réduire le filesystem d'abord, puis le fichier
  resize2fs "$MEDIA_IMG" "${TARGET_SIZE_MB}M"
  truncate -s "${TARGET_SIZE_MB}M" "$MEDIA_IMG"
  echo "  ✓ Image réduite"
fi

# Mettre à jour la licence
if [ -n "$NEW_QUOTA_MB" ] && [ -f "$LICENSE_FILE" ]; then
  python3 -c "
import json
with open('$LICENSE_FILE', 'r') as f:
    lic = json.load(f)
lic['media_quota_mb'] = $TARGET_SIZE_MB
with open('$LICENSE_FILE', 'w') as f:
    json.dump(lic, f, indent=2)
" 2>/dev/null
  echo "  ✓ Licence mise à jour (${TARGET_SIZE_MB} MB)"
fi

# Remonter la partition
if [ -n "$MOUNT_POINT" ]; then
  mount -o loop,noatime "$MEDIA_IMG" "$MOUNT_POINT"
  echo "  ✓ Partition remontée sur $MOUNT_POINT"
fi

# Relancer le service
if [ -n "$SERVICE_NAME" ]; then
  systemctl start "$SERVICE_NAME" 2>/dev/null || true
  echo "  ✓ Service $SERVICE_NAME relancé"
fi

echo ""
echo "======================================================"
echo "  ✅ Redimensionnement terminé"
echo "  Nouvelle taille : ${TARGET_SIZE_MB} MB"
AVAIL=$(df -BM "$MOUNT_POINT" --output=avail 2>/dev/null | tail -1 | tr -d ' M')
[ -n "$AVAIL" ] && echo "  Espace disponible : ${AVAIL} MB"
echo "======================================================"
echo ""
