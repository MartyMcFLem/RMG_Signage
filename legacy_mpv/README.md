# Legacy MPV — Backup de la version pré-Chromium

Ce dossier contient la version originale de RMG Signage basée sur **MPV** comme lecteur d'affichage.

- **Commit de référence :** `e0d5a2f`
- **Date du backup :** 2026-03-30
- **Raison :** Migration vers Chromium kiosk + système de templates/widgets

## Contenu

- `upload.py` — Application Flask + gestion MPV
- `templates/index.html` — Interface d'administration
- `install.sh` — Script d'installation (avec mpv)
- `splash_helper.sh` — Splash screen via mpv --vo=drm
- `start_rmg_signage.sh` — Script de démarrage
- `requirements.txt` — Dépendances Python

## Pour restaurer cette version

```bash
cp legacy_mpv/upload.py upload.py
cp legacy_mpv/templates/index.html templates/index.html
cp legacy_mpv/install.sh install.sh
cp legacy_mpv/splash_helper.sh splash_helper.sh
cp legacy_mpv/start_rmg_signage.sh start_rmg_signage.sh
```
