from flask import Flask, request, redirect, jsonify, send_from_directory, render_template
from werkzeug.utils import secure_filename
import os
import re
import copy
import threading
import subprocess
import time
import json
import shutil
import uuid as _uuid
import urllib.request

# === CONFIG ===
HOME_DIR = os.path.expanduser("~")
MEDIA_DIR = os.environ.get("RMG_SIGNAGE_MEDIA_DIR", "/home/rmg/signage/medias")
CONFIG_FILE = os.environ.get("RMG_SIGNAGE_CONFIG_FILE", os.path.join(MEDIA_DIR, "config.json"))

CHROMIUM_BINARY = (
    shutil.which("chromium-browser")
    or shutil.which("chromium")
    or "chromium-browser"
)
GIT_BINARY = shutil.which("git") or "/usr/bin/git"
LOG_FILE = os.path.join(MEDIA_DIR, "rmg_signage.log")

# Branche git et port Flask — injectés par systemd selon l'environnement (prod/dev)
GIT_BRANCH = os.environ.get("RMG_SIGNAGE_BRANCH", "main")
FLASK_PORT  = int(os.environ.get("RMG_SIGNAGE_PORT", 5000))

# Nom du service systemd et répertoire projet
SERVICE_NAME = os.environ.get("RMG_SIGNAGE_SERVICE", "rmg_signage")
PROJECT_DIR = os.environ.get("RMG_SIGNAGE_DIR", os.path.dirname(os.path.abspath(__file__)))

# Licence (quota stockage)
LICENSE_FILE = os.environ.get("RMG_SIGNAGE_LICENSE", "/etc/rmg_signage/license.json")
DEFAULT_MEDIA_QUOTA_MB = 2048  # 2 Go par défaut (sans licence)

# ─── Système de clés de licence ───
# Format : RMGS-XXXXX-XXXXX-XXXXX (base32, 15 chars payload)
# Payload = tier_byte (1) + random (5) + HMAC-SHA256[:3] (3) = 9 bytes → 15 chars base32
# Tiers disponibles :
LICENSE_TIERS = {
    0x01: {"name": "standard",  "quota_mb": 4096,  "max_files": 100},
    0x02: {"name": "business",  "quota_mb": 12288, "max_files": 1000},
    0x03: {"name": "unlimited", "quota_mb": 24576, "max_files": 0},   # 0 = illimite
}
# Limite par defaut sans licence
DEFAULT_MAX_FILES = 10
# Clé secrète pour la validation HMAC (suffisante pour un système embarqué offline)
_LICENSE_SECRET = b"RMG-S1gn4g3-2024-s3cr3t-k3y"

# Configuration par défaut
config = {
    "image_duration": 8,
    "shuffle": True,
    "loop": True,
    "dark_mode": False,
    "rotation": 0,           # rotation affichage : 0, 90, 180, 270
    "single_file_mode": False,
    "selected_file": None,
    "file_order": [],        # ordre personnalisé des fichiers
    "file_durations": {},    # durées par fichier {"photo.jpg": 12}
    "playlists": [],         # [{id, name, files, created}]
    "active_playlist": None, # id de la playlist active (None = tous les fichiers)
    "active_page": None,      # id de la page en lecture seule (None = mode normal)
    "pages": [],             # pages de signage avec widgets
}

config = dict(_DEFAULT_CONFIG)
_config_lock = threading.Lock()

if os.path.exists(CONFIG_FILE):
    try:
        with open(CONFIG_FILE, 'r') as f:
            config.update(json.load(f))
    except (IOError, json.JSONDecodeError, ValueError):
        pass

player_process = None
_player_lock = threading.Lock()
# Compteur de génération : incrémenté à chaque restart_chromium().
# La page kiosk le lit via /api/kiosk/state pour détecter un changement de playlist.
_playlist_generation = 0
# Événement utilisé pour signaler «passer au média suivant» depuis l'API.
_kiosk_next_event = threading.Event()
# Événement utilisé pour signaler «afficher l'écran IP» depuis l'API.
_show_ip_event = threading.Event()

_weather_cache = {"data": None, "fetched_at": 0}
_news_cache    = {"data": None, "fetched_at": 0, "url": ""}

_SERIAL_PATTERN = re.compile(r'^rmg-sign-[a-z0-9]{16}$')
_device_serial  = None


def _license_hmac(data_bytes):
    """Calcule un HMAC-SHA256 tronqué à 3 bytes pour la validation de clé."""
    import hashlib, hmac
    return hmac.new(_LICENSE_SECRET, data_bytes, hashlib.sha256).digest()[:3]


def validate_license_key(key_str):
    """Valide une clé de licence et retourne (valid, tier_name, quota_mb).
    Format attendu : RMGS-XXXXX-XXXXX-XXXXX"""
    import base64
    key_str = key_str.strip().upper().replace(" ", "")
    if not key_str.startswith("RMGS-"):
        return False, None, 0
    payload_b32 = key_str[5:].replace("-", "")
    if len(payload_b32) != 15:
        return False, None, 0
    # Padding base32 pour 15 chars → 9 bytes (15 chars * 5 bits = 75 bits → 10 bytes avec padding)
    # Mais 9 bytes = 72 bits → 15 chars base32 (avec 3 bits padding)
    try:
        padded = payload_b32 + "="  # 15 chars + 1 pad = 16 → 10 bytes, on prend 9
        raw = base64.b32decode(padded)[:9]
    except Exception:
        return False, None, 0
    if len(raw) < 9:
        return False, None, 0
    tier_byte = raw[0]
    random_part = raw[1:6]
    provided_mac = raw[6:9]
    expected_mac = _license_hmac(bytes([tier_byte]) + random_part)
    if provided_mac != expected_mac:
        return False, None, 0
    tier_info = LICENSE_TIERS.get(tier_byte)
    if not tier_info:
        return False, None, 0
    return True, tier_info["name"], tier_info["quota_mb"]


def generate_license_key(tier_code):
    """Génère une clé de licence pour le tier donné (usage admin/interne).
    tier_code: 0x01 à 0x05"""
    import base64
    if tier_code not in LICENSE_TIERS:
        raise ValueError(f"Tier inconnu: {tier_code}")
    random_part = os.urandom(5)
    mac = _license_hmac(bytes([tier_code]) + random_part)
    raw = bytes([tier_code]) + random_part + mac  # 9 bytes
    b32 = base64.b32encode(raw).decode().rstrip("=")[:15]
    return f"RMGS-{b32[:5]}-{b32[5:10]}-{b32[10:15]}"


def _read_license():
    """Lit le fichier de licence et retourne ses données."""
    try:
        if os.path.exists(LICENSE_FILE):
            with open(LICENSE_FILE, 'r') as f:
                return json.load(f)
    except (IOError, json.JSONDecodeError, ValueError):
        pass
    return {"tier": "none", "media_quota_mb": DEFAULT_MEDIA_QUOTA_MB}


def _save_license(data):
    """Écrit le fichier de licence."""
    try:
        os.makedirs(os.path.dirname(LICENSE_FILE), exist_ok=True)
        with open(LICENSE_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        return True
    except (IOError, OSError):
        return False


def _count_media_files():
    """Compte le nombre de fichiers media dans MEDIA_DIR."""
    try:
        return len([f for f in os.listdir(MEDIA_DIR)
                    if os.path.isfile(os.path.join(MEDIA_DIR, f)) and is_media_file(f)])
    except OSError:
        return 0


def _get_media_files_size_mb():
    """Calcule la taille totale des fichiers media uniquement (en MB)."""
    total = 0
    try:
        for f in os.listdir(MEDIA_DIR):
            fpath = os.path.join(MEDIA_DIR, f)
            if os.path.isfile(fpath) and is_media_file(f):
                total += os.path.getsize(fpath)
    except OSError:
        pass
    return round(total / (1024 * 1024), 1)


def get_storage_info():
    """Retourne les informations de stockage de la partition media.
    N'affiche que l'espace utilise par les medias (pas l'OS/systeme)."""
    license_data = _read_license()
    tier = license_data.get("tier", "none")
    quota_mb = license_data.get("media_quota_mb", DEFAULT_MEDIA_QUOTA_MB)

    # Limites de fichiers selon le tier
    max_files = DEFAULT_MAX_FILES
    for _code, tinfo in LICENSE_TIERS.items():
        if tinfo["name"] == tier:
            max_files = tinfo["max_files"]
            break

    media_used_mb = _get_media_files_size_mb()
    media_count = _count_media_files()

    # Espace disponible : quota licence - espace utilise par les medias
    available_mb = max(0, round(quota_mb - media_used_mb, 1))
    usage_percent = round((media_used_mb / quota_mb) * 100, 1) if quota_mb > 0 else 0

    return {
        "quota_mb": quota_mb,
        "tier": tier,
        "used_mb": media_used_mb,
        "available_mb": available_mb,
        "total_mb": quota_mb,
        "usage_percent": min(usage_percent, 100),
        "media_count": media_count,
        "max_files": max_files,
        "files_remaining": max(0, max_files - media_count) if max_files > 0 else -1,
    }


def check_upload_quota(file_size_bytes):
    """Verifie si un upload de file_size_bytes octets tient dans le quota.
    Retourne (ok, error_code) avec error_code: '' | 'quota' | 'files'."""
    storage = get_storage_info()
    file_size_mb = file_size_bytes / (1024 * 1024)

    # Limite nombre de fichiers
    max_f = storage["max_files"]
    if max_f > 0 and storage["media_count"] >= max_f:
        return False, "files"

    # Limite espace disque
    if storage["available_mb"] < file_size_mb + 5:
        return False, "quota"

    if storage["usage_percent"] >= 95:
        return False, "quota"

    return True, ""


_SERIAL_PATTERN = re.compile(r'^rmg-sign-[a-z0-9]{16}$')

def _safe_filename(filename, base_dir):
    if not filename:
        return None
    safe = secure_filename(filename)
    if not safe:
        return None
    full_path = os.path.realpath(os.path.join(base_dir, safe))
    if not full_path.startswith(os.path.realpath(base_dir) + os.sep):
        return None
    return full_path


def _save_config():
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    except (IOError, OSError):
        pass


def _generate_serial_suffix():
    try:
        with open('/proc/cpuinfo', 'r') as f:
            for line in f:
                if line.startswith('Serial'):
                    suffix = line[10:26].strip()
                    if len(suffix) == 16 and re.match(r'^[0-9a-f]{16}$', suffix):
                        return suffix
    except (IOError, OSError):
        pass
    serial_file = '/etc/rmg_serial'
    try:
        if os.path.exists(serial_file):
            stored = open(serial_file).read().strip()
            if len(stored) == 16:
                return stored
    except (IOError, OSError):
        pass
    suffix = _uuid.uuid4().hex[:16]
    try:
        with open(serial_file, 'w') as f:
            f.write(suffix)
    except (IOError, OSError):
        pass
    return suffix


def get_device_serial():
    global _device_serial, config
    if _device_serial:
        return _device_serial
    import socket as _socket
    hostname = _socket.gethostname()
    if _SERIAL_PATTERN.match(hostname):
        _device_serial = hostname
        return _device_serial
    stored = config.get('device_serial', '')
    if stored and _SERIAL_PATTERN.match(stored):
        _device_serial = stored
        return _device_serial
    serial = f"rmg-sign-{_generate_serial_suffix()}"
    with _config_lock:
        config['device_serial'] = serial
        _save_config()
    try:
        subprocess.run(
            ['sudo', 'hostnamectl', 'set-hostname', serial],
            capture_output=True, timeout=5
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    _device_serial = serial
    return _device_serial


def notify_kiosk_reload():
    """Incrémente le compteur de génération pour que la page kiosk recharge sa playlist."""
    global _playlist_generation
    _playlist_generation += 1


def update_player_playlist():
    """Met à jour la playlist : notifie la page kiosk ou redémarre Chromium si nécessaire."""
    global player_process
    if player_process is None or player_process.poll() is not None:
        restart_chromium()
        return
    notify_kiosk_reload()



def get_app_version():
    """Retourne la version via `git describe --tags --always`."""
    try:
        result = subprocess.run(
            [GIT_BINARY, '-C', PROJECT_DIR, 'describe', '--tags', '--always', '--dirty=-dev'],
            capture_output=True, text=True, timeout=3
        )
        v = result.stdout.strip()
        return v if v else "unknown"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return "unknown"


def is_media_file(filename):
    if filename.startswith('.'):
        return False
    if filename in ('config.json', 'Thumbs.db', '.DS_Store'):
        return False
    ext = os.path.splitext(filename.lower())[1]
    if ext == '.json':
        return False
    return ext in {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp',
                   '.heic', '.heif',
                   '.mp4', '.avi', '.mkv', '.mov', '.webm', '.m4v'}


def get_kiosk_url():
    """Retourne l'URL de la page kiosk Chromium."""
    return f"http://127.0.0.1:{FLASK_PORT}/kiosk"



def get_local_ip(retries=8, delay=2.0):
    import socket as _socket

    def _try_udp():
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip

    def _try_hostname():
        return _socket.gethostbyname(_socket.gethostname())

    def _try_ifconfig():
        for cmd in (["ip", "-4", "addr", "show", "scope", "global"], ["hostname", "-I"]):
            try:
                out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode()
                if cmd[0] == "hostname":
                    ips = [x for x in out.split() if x and not x.startswith("127.")]
                    if ips:
                        return ips[0]
                else:
                    ips = re.findall(r"inet (\d+\.\d+\.\d+\.\d+)", out)
                    ips = [ip for ip in ips if not ip.startswith("127.")]
                    if ips:
                        return ips[0]
            except (subprocess.CalledProcessError, FileNotFoundError, OSError):
                pass
        return None

    for attempt in range(retries):
        for method in (_try_udp, _try_hostname, _try_ifconfig):
            try:
                ip = method()
                if ip and not ip.startswith("127.") and ip != "0.0.0.0":
                    return ip
            except (OSError, _socket.error):
                pass
        if attempt < retries - 1:
            time.sleep(delay)
    return "?.?.?.?"


def generate_welcome_screen():
    """Génère une image PNG de bienvenue (conservé pour compatibilité)."""
    welcome_path = os.path.join(MEDIA_DIR, ".welcome_screen.png")
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
        ip = get_local_ip()
        url = f"http://{ip}:{FLASK_PORT}"
        W, H = 1920, 1080
        cx, cy = W // 2, H // 2
        script_dir  = os.path.dirname(os.path.abspath(__file__))
        splash_path = os.path.join(script_dir, 'static', 'splash.png')
        if os.path.exists(splash_path):
            try:
                bg = Image.open(splash_path).convert('RGB').resize((W, H), Image.LANCZOS)
                bg = bg.filter(ImageFilter.GaussianBlur(radius=10))
                bg = ImageEnhance.Brightness(bg).enhance(0.35)
                img = bg
            except (IOError, OSError):
                img = Image.new("RGB", (W, H), color=(10, 10, 26))
        else:
            img = Image.new("RGB", (W, H), color=(10, 10, 26))
        overlay = Image.new('RGBA', (W, H), (0, 0, 0, 77))
        img  = Image.alpha_composite(img.convert('RGBA'), overlay).convert('RGB')
        draw = ImageDraw.Draw(img)

        def load_font(name, size):
            for path in [
                f"/usr/share/fonts/truetype/dejavu/{name}",
                f"/usr/share/fonts/truetype/liberation/Liberation{name}",
            ]:
                try:
                    return ImageFont.truetype(path, size)
                except (IOError, OSError):
                    pass
            return ImageFont.load_default()

        font_title = load_font("DejaVuSans-Bold.ttf", 80)
        font_sub   = load_font("DejaVuSans.ttf", 44)
        font_url   = load_font("DejaVuSans-Bold.ttf", 60)
        font_hint  = load_font("DejaVuSans.ttf", 30)
        qr_size = 240
        qr_x = cx - qr_size // 2
        qr_y = cy + 110
        try:
            draw.text((cx, cy - 260), "RMG Signage",
                      fill=(255, 255, 255), font=font_title, anchor="mm")
            draw.line([(cx - 340, cy - 188), (cx + 340, cy - 188)],
                      fill=(70, 70, 110), width=2)
            draw.text((cx, cy - 125), "Aucun media a afficher",
                      fill=(140, 140, 165), font=font_sub, anchor="mm")
            draw.text((cx, cy - 20), url,
                      fill=(74, 158, 255), font=font_url, anchor="mm")
            draw.text((cx, cy + 60), "Scannez le QR code ou connectez-vous :",
                      fill=(100, 100, 135), font=font_hint, anchor="mm")
        except TypeError:
            draw.text((50, 80),  "RMG Signage", fill=(255, 255, 255), font=font_title)
            draw.text((50, 220), "Aucun media",  fill=(140, 140, 165), font=font_sub)
            draw.text((50, 380), url,            fill=(74, 158, 255),  font=font_url)
        try:
            import qrcode as _qrcode
            qr = _qrcode.QRCode(box_size=10, border=3,
                                 error_correction=_qrcode.constants.ERROR_CORRECT_M)
            qr.add_data(url)
            qr.make(fit=True)
            qr_wrapped = qr.make_image(fill_color="black", back_color="white")
            qr_pil = (qr_wrapped.get_image() if hasattr(qr_wrapped, 'get_image')
                      else qr_wrapped).convert('RGB')
            qr_pil = qr_pil.resize((qr_size, qr_size), Image.NEAREST)
            pad = 10
            draw.rectangle([qr_x - pad, qr_y - pad, qr_x + qr_size + pad, qr_y + qr_size + pad],
                           fill=(255, 255, 255))
            img.paste(qr_pil, (qr_x, qr_y))
        except ImportError:
            draw.text((cx, qr_y + qr_size // 2), url,
                      fill=(74, 158, 255), font=font_url, anchor="mm")
        os.makedirs(MEDIA_DIR, exist_ok=True)
        img.save(welcome_path)
        return welcome_path
    except Exception as e:
        print(f"Ecran de bienvenue non genere : {e}")
        return None





# === FLASK APP ===
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = MAX_UPLOAD_SIZE


@app.route("/", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        files       = request.files.getlist("files")
        files_saved = 0
        reject_reason = ""
        for f in files:
            if not f.filename:
                continue
            safe_name = secure_filename(f.filename)
            if not safe_name:
                continue
            ext = os.path.splitext(safe_name.lower())[1]
            if ext not in ALLOWED_EXTENSIONS:
                continue
            f.seek(0, 2)
            file_size = f.tell()
            f.seek(0)
            quota_ok, err_code = check_upload_quota(file_size)
            if not quota_ok:
                reject_reason = err_code
                continue
            path = os.path.join(MEDIA_DIR, safe_name)
            f.save(path)
            files_saved += 1
        if files_saved:
            update_player_playlist()
        if reject_reason and files_saved == 0:
            return redirect("/?error=" + reject_reason)
        return redirect("/")
    return render_template('index.html')


@app.route("/player")
def player():
    return render_template('player.html')


@app.route("/api/logo", methods=["POST", "DELETE"])
def manage_logo():
    logo_path = os.path.join(app.root_path, 'static', 'logo.png')
    if request.method == "POST":
        f = request.files.get('logo')
        if not f or not f.filename:
            return jsonify({"success": False, "message": "Pas de fichier"}), 400
        try:
            os.makedirs(os.path.join(app.root_path, 'static'), exist_ok=True)
            f.save(logo_path)
            return jsonify({"success": True, "message": "Logo mis a jour"})
        except (IOError, OSError) as e:
            return jsonify({"success": False, "message": str(e)}), 500
    if os.path.exists(logo_path):
        try:
            os.remove(logo_path)
            return jsonify({"success": True, "message": "Logo supprime"})
        except (IOError, OSError) as e:
            return jsonify({"success": False, "message": str(e)}), 500
    return jsonify({"success": False, "message": "Aucun logo trouve"}), 404


# === API ===

@app.route("/api/storage")
def api_storage():
    """Retourne les informations de stockage de la partition média"""
    return jsonify(get_storage_info())


@app.route("/api/license")
def api_license():
    """Retourne les informations de licence (sans la clé complète)"""
    lic = _read_license()
    tier = lic.get("tier", "none")
    max_files = DEFAULT_MAX_FILES
    for _code, tinfo in LICENSE_TIERS.items():
        if tinfo["name"] == tier:
            max_files = tinfo["max_files"]
            break
    safe = {
        "tier": tier,
        "media_quota_mb": lic.get("media_quota_mb", DEFAULT_MEDIA_QUOTA_MB),
        "max_files": max_files,
        "activated": lic.get("activated", None),
        "key_preview": lic.get("key_preview", None),
    }
    return jsonify(safe)


@app.route("/api/license/activate", methods=["POST"])
def activate_license():
    """Active une clé de licence. Payload JSON : {"key": "RMGS-XXXXX-XXXXX-XXXXX"}"""
    data = request.json
    if not data or not data.get("key"):
        return jsonify({"success": False, "message": "Clé manquante"}), 400

    key_str = data["key"].strip()
    valid, tier_name, quota_mb = validate_license_key(key_str)

    if not valid:
        return jsonify({"success": False, "message": "Clé de licence invalide"}), 400

    lic = _read_license()
    lic["tier"] = tier_name
    lic["media_quota_mb"] = quota_mb
    lic["activated"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    # Stocker un aperçu de la clé (pas la clé complète)
    lic["key_preview"] = key_str[:9] + "..." + key_str[-5:]

    if not _save_license(lic):
        return jsonify({"success": False, "message": "Impossible d'écrire la licence"}), 500

    return jsonify({
        "success": True,
        "message": f"Licence {tier_name} activée ({quota_mb} MB)",
        "tier": tier_name,
        "quota_mb": quota_mb,
    })


@app.route("/api/status")
def get_status():
    """Retourne l'état actuel du lecteur Chromium"""
    running = player_process is not None and player_process.poll() is None
    try:
        media_count = len([f for f in os.listdir(MEDIA_DIR)
                           if os.path.isfile(os.path.join(MEDIA_DIR, f)) and is_media_file(f)])
    except OSError:
        media_count = 0
    storage = get_storage_info()
    return jsonify({
        "player_running": running,
        "media_count": media_count,
        "media_dir": MEDIA_DIR,
        "serial": get_device_serial(),
        "storage": storage,
        "version": get_app_version(),
    })


@app.route("/api/files")
def list_files():
    try:
        files = sorted([f for f in os.listdir(MEDIA_DIR)
                        if os.path.isfile(os.path.join(MEDIA_DIR, f)) and is_media_file(f)])
        return jsonify(files)
    except OSError:
        return jsonify([])


@app.route("/media/<filename>")
def serve_media(filename):
    safe = secure_filename(filename)
    if not safe:
        return jsonify({"success": False, "message": "Nom de fichier invalide"}), 400
    return send_from_directory(MEDIA_DIR, safe)


@app.route("/images/<path:filename>")
def serve_images(filename):
    return send_from_directory(os.path.join(app.root_path, 'images'), filename)


@app.route("/kiosk")
def kiosk_page():
    """Page de lecture kiosk servie par Chromium en plein écran."""
    return render_template('kiosk.html')


@app.route("/api/kiosk/state")
def kiosk_state():
    """Retourne l'état courant pour la page kiosk :
    liste des médias à afficher, config et compteur de génération.
    La page kiosk poll ce endpoint pour détecter les changements de playlist."""
    try:
        all_files = [f for f in os.listdir(MEDIA_DIR)
                     if os.path.isfile(os.path.join(MEDIA_DIR, f)) and is_media_file(f)]
    except Exception:
        all_files = []

    if config.get('single_file_mode') and config.get('selected_file'):
        sel = config['selected_file']
        if sel in all_files:
            playlist_files = [sel]
        else:
            playlist_files = []
    else:
        active_pl_id = config.get('active_playlist')
        if active_pl_id:
            playlists = config.get('playlists', [])
            pl = next((p for p in playlists if p.get('id') == active_pl_id), None)
            if pl and pl.get('files'):
                all_set = set(all_files)
                pl_files = [f for f in pl['files'] if f in all_set]
                playlist_files = pl_files if pl_files else sorted(all_files)
            else:
                playlist_files = sorted(all_files)
        elif not config.get('shuffle') and config.get('file_order'):
            order = config['file_order']
            all_set = set(all_files)
            ordered = [f for f in order if f in all_set]
            remaining = sorted(f for f in all_files if f not in set(ordered))
            playlist_files = ordered + remaining
        else:
            playlist_files = sorted(all_files)

    # Vérifier si un événement «suivant» a été déclenché
    next_triggered = _kiosk_next_event.is_set()
    if next_triggered:
        _kiosk_next_event.clear()

    # Vérifier si l'affichage de l'écran IP a été demandé
    show_ip_triggered = _show_ip_event.is_set()
    if show_ip_triggered:
        _show_ip_event.clear()

    # Construire la liste d'items médias + pages entrelacées
    # Mode page seule : afficher uniquement cette page en boucle
    active_page_id = config.get("active_page")
    if active_page_id:
        active_pg = next((p for p in config.get("pages", []) if p["id"] == active_page_id), None)
        if active_pg:
            playlist_files = []
            pages_cfg = [active_pg]
        else:
            pages_cfg = config.get("pages", [])
    else:
        pages_cfg = config.get("pages", [])
    page_items = [
        {"type": "page", "id": p["id"], "name": p.get("name", ""),
         "duration": p.get("duration", 15),
         "_oi": p.get("order_index")}
        for p in pages_cfg
    ]
    # Pages avec order_index triées, puis celles sans (fin de cycle)
    page_items.sort(key=lambda p: (p["_oi"] is None, p["_oi"] if p["_oi"] is not None else 0))
    ordered_pages = [p for p in page_items if p["_oi"] is not None]
    trailing_pages = [p for p in page_items if p["_oi"] is None]
    items = []
    pi = 0
    for i, f in enumerate(playlist_files):
        while pi < len(ordered_pages) and ordered_pages[pi]["_oi"] <= i:
            op = {k: v for k, v in ordered_pages[pi].items() if k != "_oi"}
            items.append(op)
            pi += 1
        items.append({"type": "media", "file": f})
    while pi < len(ordered_pages):
        op = {k: v for k, v in ordered_pages[pi].items() if k != "_oi"}
        items.append(op)
        pi += 1
    for tp in trailing_pages:
        items.append({k: v for k, v in tp.items() if k != "_oi"})

    return jsonify({
        "generation": _playlist_generation,
        "files": playlist_files,
        "items": items,
        "config": {
            "image_duration": config.get('image_duration', 8),
            "shuffle": config.get('shuffle', True),
            "loop": config.get('loop', True),
            "rotation": config.get('rotation', 0),
            "single_file_mode": config.get('single_file_mode', False),
            "file_durations": config.get('file_durations', {}),
        },
        "next": next_triggered,
        "show_ip": show_ip_triggered,
    })

@app.route("/api/kiosk/ip-info")
def kiosk_ip_info():
    """Retourne l'IP locale et un QR code (PNG base64) pointant vers l'interface web."""
    import io, base64 as _b64
    ip = get_local_ip()
    url = f"http://{ip}:{FLASK_PORT}"
    qr_data = None
    try:
        import qrcode as _qrcode
        qr = _qrcode.QRCode(
            box_size=10, border=3,
            error_correction=_qrcode.constants.ERROR_CORRECT_M
        )
        qr.add_data(url)
        qr.make(fit=True)
        qr_wrapped = qr.make_image(fill_color="black", back_color="white")
        if hasattr(qr_wrapped, 'get_image'):
            qr_pil = qr_wrapped.get_image()
        else:
            qr_pil = qr_wrapped
        buf = io.BytesIO()
        qr_pil.save(buf, format='PNG')
        qr_data = "data:image/png;base64," + _b64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        print(f"⚠️  QR code non généré : {e}")
    return jsonify({"ip": ip, "url": url, "qr": qr_data})

@app.route("/api/delete/<filename>", methods=["DELETE"])
def delete_file(filename):
    try:
        path = os.path.join(MEDIA_DIR, filename)
        if os.path.exists(path):
            os.remove(path)
            # Nettoyer la durée personnalisée si elle existe
            if filename in config.get('file_durations', {}):
                config['file_durations'].pop(filename)
            # Retirer du file_order
            if filename in config.get('file_order', []):
                config['file_order'].remove(filename)
            # Retirer des playlists
            for pl in config.get('playlists', []):
                if filename in pl.get('files', []):
                    pl['files'].remove(filename)
            try:
                with open(CONFIG_FILE, 'w') as f:
                    json.dump(config, f, indent=2)
            except:
                pass
            return jsonify({"success": True, "message": f"{filename} supprimé"})
        return jsonify({"success": False, "message": "Fichier introuvable"}), 404
    try:
        os.remove(path)
        safe = secure_filename(filename)
        with _config_lock:
            config.get('file_durations', {}).pop(safe, None)
            fo = config.get('file_order', [])
            if safe in fo:
                fo.remove(safe)
            _save_config()
        return jsonify({"success": True, "message": f"{safe} supprime"})
    except (IOError, OSError) as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/config", methods=["GET", "POST"])
def manage_config():
    global config
    if request.method == "POST":
        data = request.json
        # Ne pas écraser file_order et file_durations via cet endpoint
        data.pop('file_order', None)
        data.pop('file_durations', None)
        config.update(data)
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(config, f)
        except:
            pass

        # Notifier la page kiosk du changement de configuration
        notify_kiosk_reload()
        if player_process is None or player_process.poll() is not None:
            restart_chromium()

        return jsonify({"success": True, "message": "Configuration mise à jour"})
    return jsonify(config)


@app.route("/api/order", methods=["POST"])
def save_order():
    global config
    data = request.json
    config['file_order'] = data.get('order', [])
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    except:
        pass
    if not config.get('shuffle'):
        notify_kiosk_reload()
    return jsonify({"success": True, "message": "Ordre sauvegardé"})


@app.route("/api/file-duration/<filename>", methods=["POST"])
def set_file_duration(filename):
    global config
    data     = request.json
    duration = data.get('duration')
    if 'file_durations' not in config:
        config['file_durations'] = {}
    if duration is None:
        config['file_durations'].pop(filename, None)
    else:
        config['file_durations'][filename] = int(duration)
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    except:
        pass
    # La page kiosk relit la config au prochain cycle — pas besoin de redémarrer Chromium
    return jsonify({"success": True, "message": f"Durée mise à jour pour {filename}"})


@app.route("/api/control/<action>", methods=["POST"])
def control_player(action):
    global player_process
    if action == "restart":
        restart_chromium()
        return jsonify({"success": True, "message": "Lecteur redémarré"})
    elif action == "stop":
        if player_process:
            player_process.terminate()
            player_process = None
        return jsonify({"success": True, "message": "Lecteur arrêté"})
    elif action == "next":
        # Signaler à la page kiosk de passer au média suivant
        _kiosk_next_event.set()
        return jsonify({"success": True, "message": "Fichier suivant"})
    elif action == "show-ip":
        # Signaler à la page kiosk d'afficher l'overlay IP
        _show_ip_event.set()
        if player_process is None or player_process.poll() is not None:
            restart_chromium()
        return jsonify({"success": True, "message": "Écran IP affiché"})
    return jsonify({"success": False, "message": "Action inconnue"}), 400


@app.route("/api/play-single/<filename>", methods=["POST"])
def play_single_file(filename):
    global config
    path = _safe_filename(filename, MEDIA_DIR)
    if not path or not os.path.exists(path):
        return jsonify({"success": False, "message": "Fichier introuvable"}), 404
    config['single_file_mode'] = True
    config['selected_file'] = filename
    config['active_playlist'] = None  # Desactiver toute playlist
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    except:
        pass
    restart_chromium()
    return jsonify({"success": True, "message": f"Affichage de {filename} uniquement"})


@app.route("/api/play-all", methods=["POST"])
def play_all_files():
    global config
    config['single_file_mode'] = False
    config['selected_file'] = None
    config['active_playlist'] = None  # Desactiver toute playlist
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    except:
        pass
    restart_chromium()
    return jsonify({"success": True, "message": "Lecture de tous les fichiers"})


# === PLAYLISTS API ===
# IMPORTANT: les routes statiques (deactivate) DOIVENT etre declarees
# AVANT les routes parametriques (<pl_id>) sinon Flask matche "deactivate"
# comme un pl_id.

@app.route("/api/playlists", methods=["GET"])
def get_playlists():
    """Liste toutes les playlists"""
    playlists = config.get('playlists', [])
    active = config.get('active_playlist')
    return jsonify({"playlists": playlists, "active_playlist": active})


@app.route("/api/playlists", methods=["POST"])
def create_playlist():
    """Cree une nouvelle playlist. JSON: {name, files?}"""
    global config
    data = request.json
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({"success": False, "message": "Nom requis"}), 400
    if 'playlists' not in config:
        config['playlists'] = []
    pl = {
        "id": _uuid.uuid4().hex[:12],
        "name": name,
        "files": data.get('files', []),
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    config['playlists'].append(pl)
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    except:
        pass
    return jsonify({"success": True, "playlist": pl})


@app.route("/api/playlists/deactivate", methods=["POST"])
def deactivate_playlist():
    """Desactive la playlist, revient a la lecture de tous les fichiers"""
    global config
    config['active_playlist'] = None
    config['single_file_mode'] = False
    config['selected_file'] = None
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    except:
        pass
    notify_kiosk_reload()
    return jsonify({"success": True, "message": "Lecture de tous les fichiers"})


@app.route("/api/playlists/<pl_id>", methods=["GET"])
def get_playlist(pl_id):
    """Retourne une playlist par son id"""
    pl = next((p for p in config.get('playlists', []) if p['id'] == pl_id), None)
    if not pl:
        return jsonify({"success": False, "message": "Playlist introuvable"}), 404
    return jsonify(pl)


@app.route("/api/playlists/<pl_id>", methods=["PUT"])
def update_playlist(pl_id):
    """Met a jour une playlist. JSON: {name?, files?}"""
    global config
    pl = next((p for p in config.get('playlists', []) if p['id'] == pl_id), None)
    if not pl:
        return jsonify({"success": False, "message": "Playlist introuvable"}), 404
    data = request.json
    if 'name' in data:
        pl['name'] = (data['name'] or '').strip() or pl['name']
    if 'files' in data:
        pl['files'] = data['files']
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    except:
        pass
    if config.get('active_playlist') == pl_id:
        notify_kiosk_reload()
    return jsonify({"success": True, "playlist": pl})


@app.route("/api/playlists/<pl_id>", methods=["DELETE"])
def delete_playlist(pl_id):
    """Supprime une playlist"""
    global config
    playlists = config.get('playlists', [])
    before = len(playlists)
    config['playlists'] = [p for p in playlists if p['id'] != pl_id]
    if len(config['playlists']) == before:
        return jsonify({"success": False, "message": "Playlist introuvable"}), 404
    if config.get('active_playlist') == pl_id:
        config['active_playlist'] = None
        notify_kiosk_reload()
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    except:
        pass
    return jsonify({"success": True, "message": "Playlist supprimee"})


@app.route("/api/playlists/<pl_id>/activate", methods=["POST"])
def activate_playlist(pl_id):
    """Active une playlist pour la lecture"""
    global config
    pl = next((p for p in config.get('playlists', []) if p['id'] == pl_id), None)
    if not pl:
        return jsonify({"success": False, "message": "Playlist introuvable"}), 404
    config['active_playlist'] = pl_id
    config['single_file_mode'] = False
    config['selected_file'] = None
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    except:
        pass
    restart_chromium()
    return jsonify({"success": True, "message": f"Playlist '{pl['name']}' activee"})


# === PAGES DE SIGNAGE ===

@app.route("/api/pages/<page_id>/activate", methods=["POST"])
def activate_page(page_id):
    """Passe en mode lecture seule sur cette page (boucle sans médias)."""
    global config
    pg = next((p for p in config.get("pages", []) if p["id"] == page_id), None)
    if not pg:
        return jsonify({"success": False, "message": "Page introuvable"}), 404
    config["active_page"] = page_id
    config["active_playlist"] = None
    config["single_file_mode"] = False
    config["selected_file"] = None
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    except:
        pass
    notify_kiosk_reload()
    return jsonify({"success": True, "message": f"Page '{pg['name']}' en lecture seule"})


@app.route("/api/pages/deactivate", methods=["POST"])
def deactivate_page():
    """Quitte le mode lecture seule, repasse en lecture de playlist/tous les médias."""
    global config
    config["active_page"] = None
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    except:
        pass
    notify_kiosk_reload()
    return jsonify({"success": True, "message": "Lecture normale reprise"})


@app.route("/api/pages", methods=["GET"])
def get_pages():
    return jsonify({"pages": config.get("pages", []), "active_page": config.get("active_page")})


@app.route("/api/pages", methods=["POST"])
def create_page():
    global config
    data = request.get_json() or {}
    page = {
        "id": str(_uuid.uuid4())[:8],
        "name": (data.get("name") or "Nouvelle page").strip(),
        "duration": max(1, int(data.get("duration", 15))),
        "order_index": data.get("order_index"),
        "bg_color": data.get("bg_color", "#1a1a2e"),
        "rotation": int(data.get("rotation", 0)) if data.get("rotation") in (0, 90, 180, 270, "0", "90", "180", "270") else 0,
        "widgets": data.get("widgets", []),
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if "pages" not in config:
        config["pages"] = []
    config["pages"].append(page)
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    except:
        pass
    notify_kiosk_reload()
    return jsonify({"success": True, "page": page})


@app.route("/api/pages/<page_id>", methods=["GET"])
def get_page(page_id):
    page = next((p for p in config.get("pages", []) if p["id"] == page_id), None)
    if not page:
        return jsonify({"error": "Page introuvable"}), 404
    return jsonify(page)


@app.route("/api/pages/<page_id>", methods=["PUT"])
def update_page(page_id):
    global config
    pages = config.get("pages", [])
    idx = next((i for i, p in enumerate(pages) if p["id"] == page_id), None)
    if idx is None:
        return jsonify({"error": "Page introuvable"}), 404
    data = request.get_json() or {}
    page = pages[idx]
    for field in ("name", "duration", "order_index", "bg_color", "rotation", "widgets"):
        if field in data:
            page[field] = data[field]
    if "duration" in data:
        page["duration"] = max(1, int(page["duration"]))
    if "rotation" in data:
        page["rotation"] = int(page["rotation"]) if page["rotation"] in (0, 90, 180, 270, "0", "90", "180", "270") else 0
    page["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    config["pages"][idx] = page
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    except:
        pass
    notify_kiosk_reload()
    return jsonify({"success": True, "page": page})


@app.route("/api/pages/<page_id>/bg-image", methods=["POST", "DELETE"])
def page_bg_image(page_id):
    """Upload ou suppression de l'image de fond d'une page."""
    global config
    pages = config.get("pages", [])
    page = next((p for p in pages if p["id"] == page_id), None)
    if not page:
        return jsonify({"success": False, "message": "Page introuvable"}), 404

    bg_dir = os.path.join(app.root_path, 'static', 'page_bg')
    os.makedirs(bg_dir, exist_ok=True)

    if request.method == "POST":
        f = request.files.get('image')
        if not f or not f.filename:
            return jsonify({"success": False, "message": "Pas de fichier"}), 400
        ext = os.path.splitext(secure_filename(f.filename))[1].lower()
        if ext not in {'.jpg', '.jpeg', '.png', '.webp', '.gif'}:
            return jsonify({"success": False, "message": "Format non supporté"}), 400
        # Supprimer l'ancienne image de fond si elle existe
        for old in os.listdir(bg_dir):
            if old.startswith(page_id + '.'):
                try:
                    os.remove(os.path.join(bg_dir, old))
                except OSError:
                    pass
        filename = page_id + ext
        f.save(os.path.join(bg_dir, filename))
        url = '/static/page_bg/' + filename
        page['bg_image'] = url
        try:
            with open(CONFIG_FILE, 'w') as cf:
                json.dump(config, cf, indent=2)
        except OSError:
            pass
        notify_kiosk_reload()
        return jsonify({"success": True, "url": url})

    # DELETE
    page.pop('bg_image', None)
    for old in os.listdir(bg_dir):
        if old.startswith(page_id + '.'):
            try:
                os.remove(os.path.join(bg_dir, old))
            except OSError:
                pass
    try:
        with open(CONFIG_FILE, 'w') as cf:
            json.dump(config, cf, indent=2)
    except OSError:
        pass
    notify_kiosk_reload()
    return jsonify({"success": True})


@app.route("/api/pages/<page_id>", methods=["DELETE"])
def delete_page(page_id):
    global config
    pages = config.get("pages", [])
    before = len(pages)
    config["pages"] = [p for p in pages if p["id"] != page_id]
    if len(config["pages"]) == before:
        return jsonify({"error": "Page introuvable"}), 404
    # Nettoyer l'image de fond associée
    bg_dir = os.path.join(app.root_path, 'static', 'page_bg')
    if os.path.isdir(bg_dir):
        for old in os.listdir(bg_dir):
            if old.startswith(page_id + '.'):
                try:
                    os.remove(os.path.join(bg_dir, old))
                except OSError:
                    pass
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    except:
        pass
    notify_kiosk_reload()
    return jsonify({"success": True})


@app.route("/signage/<page_id>")
def render_signage_page(page_id):
    """Rendu plein écran d'une page de signage (affiché dans l'iframe kiosk)."""
    page = next((p for p in config.get("pages", []) if p["id"] == page_id), None)
    if not page:
        return "Page introuvable", 404
    return render_template("signage_page.html", page=page)


@app.route("/api/rss-proxy")
def rss_proxy():
    """Proxy RSS : récupère un flux RSS/Atom distant et retourne les titres en JSON.
    Paramètre : url (URL du flux à récupérer)."""
    import urllib.request as _urlreq
    import xml.etree.ElementTree as _ET

    raw_url = request.args.get("url", "").strip()
    if not raw_url:
        return jsonify({"error": "Paramètre url manquant"}), 400
    if not raw_url.startswith(("http://", "https://")):
        return jsonify({"error": "URL invalide"}), 400

    try:
        req = _urlreq.Request(raw_url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; Feedfetcher-Google; +http://www.google.com/feedfetcher.html)",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        })
        with _urlreq.urlopen(req, timeout=8) as resp:
            xml_bytes = resp.read()
    except Exception as e:
        return jsonify({"error": str(e)}), 502

    try:
        root = _ET.fromstring(xml_bytes)
    except _ET.ParseError:
        # Certains flux ont des entités HTML (&nbsp; etc.) — on tente avec lxml-like fallback
        try:
            import re as _re2
            cleaned = _re2.sub(r'&(?!(amp|lt|gt|apos|quot|#\d+|#x[0-9a-fA-F]+);)', '&amp;', xml_bytes.decode('utf-8', errors='replace'))
            root = _ET.fromstring(cleaned.encode('utf-8'))
        except Exception as e2:
            return jsonify({"error": "XML invalide : " + str(e2)}), 502

    # Namespace Atom éventuel
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items = []

    MEDIA_NS = "http://search.yahoo.com/mrss/"

    def _extract_image(item_el):
        """Tente d'extraire une URL d'image depuis un élément <item> ou <entry>."""
        import re as _re
        # <media:content url="..." medium="image">
        for mc in item_el.iter(f"{{{MEDIA_NS}}}content"):
            url = mc.get("url", "")
            if url and mc.get("medium", "") in ("image", ""):
                return url
        # <media:thumbnail url="...">
        for mt in item_el.iter(f"{{{MEDIA_NS}}}thumbnail"):
            url = mt.get("url", "")
            if url:
                return url
        # <enclosure url="..." type="image/...">
        enc = item_el.find("enclosure")
        if enc is not None:
            url = enc.get("url", "")
            ctype = enc.get("type", "")
            if url and ctype.startswith("image/"):
                return url
        # <description> contenant une balise <img src="...">
        desc_el = item_el.find("description")
        if desc_el is not None and desc_el.text:
            m = _re.search(r'<img[^>]+src=["\']([^"\']+)["\']', desc_el.text, _re.I)
            if m:
                return m.group(1)
        return None

    # RSS 2.0 : <channel><item><title>
    for item in root.iter("item"):
        title_el = item.find("title")
        if title_el is not None and title_el.text:
            entry = {"title": title_el.text.strip()}
            try:
                img = _extract_image(item)
                if img:
                    entry["image"] = img
            except Exception:
                pass
            items.append(entry)

    # Atom : <entry><title>
    if not items:
        for entry_el in root.iter("{http://www.w3.org/2005/Atom}entry"):
            title_el = entry_el.find("{http://www.w3.org/2005/Atom}title")
            if title_el is not None and title_el.text:
                entry = {"title": title_el.text.strip()}
                try:
                    img = _extract_image(entry_el)
                    if img:
                        entry["image"] = img
                except Exception:
                    pass
                items.append(entry)

    return jsonify({"items": items})


@app.route("/api/weather")
def weather_proxy():
    """Proxy vers Open-Meteo (gratuit, sans clé API) pour éviter les CORS."""
    import urllib.request as _urlreq
    try:
        lat = float(request.args.get("lat", "48.85"))
        lon = float(request.args.get("lon", "2.35"))
        unit = request.args.get("unit", "celsius")
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            return jsonify({"error": "Coordonnées invalides"}), 400
        if unit not in ("celsius", "fahrenheit"):
            unit = "celsius"
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat:.6f}&longitude={lon:.6f}"
            f"&current_weather=true"
            f"&hourly=relativehumidity_2m,apparent_temperature"
            f"&temperature_unit={unit}&forecast_days=1&timezone=auto"
        )
        req = _urlreq.Request(url, headers={"User-Agent": "RMGSignage/1.0"})
        with _urlreq.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode())
        return jsonify(data)
    except ValueError:
        return jsonify({"error": "Paramètres invalides"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/update/status", methods=["GET"])
def update_git_status():
    """Retourne les informations git actuelles (branche locale + dernier commit de origin/main)"""
    script_dir = PROJECT_DIR
    try:
        commit = subprocess.check_output(
            [GIT_BINARY, "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_DIR, stderr=subprocess.STDOUT
        ).decode().strip()
        branch = subprocess.check_output(
            [GIT_BINARY, "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=PROJECT_DIR, stderr=subprocess.STDOUT
        ).decode().strip()
        msg = subprocess.check_output(
            [GIT_BINARY, "log", "-1", "--pretty=%s"],
            cwd=PROJECT_DIR, stderr=subprocess.STDOUT
        ).decode().strip()
        try:
            subprocess.check_output(
                [GIT_BINARY, "fetch", "origin", GIT_BRANCH],
                cwd=PROJECT_DIR, stderr=subprocess.STDOUT
            )
            remote_commit = subprocess.check_output(
                [GIT_BINARY, "rev-parse", "--short", f"origin/{GIT_BRANCH}"],
                cwd=PROJECT_DIR, stderr=subprocess.STDOUT
            ).decode().strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            remote_commit = None
        return jsonify({
            "success":        True,
            "commit":         commit,
            "branch":         branch,
            "tracked_branch": GIT_BRANCH,
            "last_message":   msg,
            "remote_commit":  remote_commit,
            "up_to_date":     commit == remote_commit if remote_commit else None,
        })
    except subprocess.CalledProcessError as e:
        return jsonify({"success": False, "message": e.output.decode().strip()})
    except (FileNotFoundError, OSError) as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/update", methods=["POST"])
def update_from_github():
    """Bascule sur main, aligne sur origin/main et redémarre si nécessaire"""
    script_dir = PROJECT_DIR
    try:
        before = subprocess.check_output(
            [GIT_BINARY, "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_DIR, stderr=subprocess.STDOUT
        ).decode().strip()
        fetch_out = subprocess.check_output(
            [GIT_BINARY, "fetch", "origin", GIT_BRANCH],
            cwd=PROJECT_DIR, stderr=subprocess.STDOUT
        ).decode().strip()
        current_branch = subprocess.check_output(
            [GIT_BINARY, "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=PROJECT_DIR, stderr=subprocess.STDOUT
        ).decode().strip()
        checkout_out = ""
        if current_branch != GIT_BRANCH:
            checkout_out = subprocess.check_output(
                [GIT_BINARY, "checkout", GIT_BRANCH],
                cwd=PROJECT_DIR, stderr=subprocess.STDOUT
            ).decode().strip()
        reset_out = subprocess.check_output(
            [GIT_BINARY, "reset", "--hard", f"origin/{GIT_BRANCH}"],
            cwd=PROJECT_DIR, stderr=subprocess.STDOUT
        ).decode().strip()
        after = subprocess.check_output(
            [GIT_BINARY, "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_DIR, stderr=subprocess.STDOUT
        ).decode().strip()
        output_lines = [l for l in [fetch_out, checkout_out, reset_out] if l]
        updated = before != after
        if updated:
            svc = SERVICE_NAME
            def delayed_restart():
                time.sleep(1.5)
                subprocess.Popen(["sudo", "reboot"])
            threading.Thread(target=delayed_restart, daemon=True).start()
        return jsonify({
            "success": True,
            "updated": updated,
            "branch":  GIT_BRANCH,
            "before":  before,
            "after":   after,
            "output":  "\n".join(output_lines),
            "message": "Mise a jour effectuee, redemarrage en cours..." if updated else "Deja a jour",
        })
    except subprocess.CalledProcessError as e:
        return jsonify({"success": False, "message": e.output.decode().strip()}), 500
    except (FileNotFoundError, OSError) as e:
        return jsonify({"success": False, "message": str(e)}), 500


def start_flask():
    # use_reloader=False évite le double fork qui casserait le thread Chromium
    # threaded=True permet les requêtes concurrentes
    app.run(host="0.0.0.0", port=FLASK_PORT, threaded=True, use_reloader=False)


def start_chromium(boot_delay=True):
    """Lance Chromium en mode kiosk sur la page de lecture.
    boot_delay=True uniquement au premier démarrage (attend que Flask soit prêt)."""
    global player_process

    if boot_delay:
        # Attendre que Flask soit en écoute (sinon Chromium afficherait une erreur 404)
        _ready_files = [
            "/run/rmg_signage/ready",
            os.path.expanduser("~/rmg_signage-ready"),
            "/tmp/rmg_signage-ready",
        ]
        for _t in range(35):
            if any(os.path.exists(p) for p in _ready_files):
                break
            time.sleep(1)
        time.sleep(0.5)

    os.makedirs(MEDIA_DIR, exist_ok=True)

    url = get_kiosk_url()

    # Profil temporaire pour éviter les avertissements de session précédente
    profile_dir = "/tmp/rmg_chromium_profile"
    os.makedirs(profile_dir, exist_ok=True)

    # Désactiver la popup de traduction via les préférences Chromium
    prefs_dir = os.path.join(profile_dir, "Default")
    os.makedirs(prefs_dir, exist_ok=True)
    prefs_file = os.path.join(prefs_dir, "Preferences")
    if not os.path.exists(prefs_file):
        try:
            with open(prefs_file, 'w') as _pf:
                json.dump({"translate": {"enabled": False},
                           "translate_site_blacklist_with_time": {}}, _pf)
        except Exception:
            pass

    cmd = [
        CHROMIUM_BINARY,
        "--kiosk",
        "--noerrdialogs",
        "--disable-infobars",
        "--no-first-run",
        "--disable-session-crashed-bubble",
        "--disable-restore-session-state",
        "--disable-features=Translate,TranslateUI",
        "--lang=fr-FR",
        "--check-for-update-interval=31536000",
        f"--user-data-dir={profile_dir}",
        url,
    ]

    try:
        with open(LOG_FILE, "ab") as logf:
            logf.write(("\n\n--- Starting Chromium kiosk: %s ---\n" % " ".join(cmd)).encode('utf-8'))
    except Exception:
        pass

    try:
        logf = open(LOG_FILE, "ab")
        proc = subprocess.Popen(cmd, stdout=logf, stderr=logf,
                                env={**os.environ, "DISPLAY": ":0"})
        time.sleep(1.5)
        if proc.poll() is None:
            player_process = proc
            try:
                proc.wait()
            finally:
                try:
                    logf.close()
                except Exception:
                    pass
            # Si player_process a été remplacé par restart_chromium(), ne pas relancer
            if player_process is not proc:
                return
            # Sortie inattendue → relancer
            player_process = None
            try:
                with open(LOG_FILE, "ab") as logf2:
                    logf2.write(b"Chromium exited unexpectedly, restarting in 2s...\n")
            except Exception:
                pass
            time.sleep(2)
            threading.Thread(target=start_chromium, kwargs={'boot_delay': False}, daemon=True).start()
            return
        else:
            try:
                logf.write((f"Chromium exited quickly with code={proc.returncode}\n").encode('utf-8'))
                logf.close()
            except Exception:
                pass
    except Exception as e:
        try:
            with open(LOG_FILE, "ab") as logf2:
                logf2.write((f"Exception launching Chromium: {e}\n").encode('utf-8'))
        except Exception:
            pass

    print("⚠️ Impossible de lancer Chromium — consultez", LOG_FILE)
    player_process = None


def restart_chromium():
    """Arrête Chromium et le relance. Notifie aussi la page kiosk via le compteur de génération."""
    global player_process
    notify_kiosk_reload()
    _player_lock.acquire()
    try:
        proc = player_process
        player_process = None
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                pass
            try:
                proc.kill()
            except Exception:
                pass
    finally:
        _player_lock.release()
    threading.Thread(target=start_chromium, kwargs={'boot_delay': False}, daemon=True).start()



if __name__ == "__main__":
    import urllib.parse
    os.makedirs(MEDIA_DIR, exist_ok=True)
    print(f"Numero de serie : {get_device_serial()}")

    if not os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(config, f, indent=2)
            print(f"Fichier de configuration cree : {CONFIG_FILE}")
        except (IOError, OSError) as e:
            print(f"Impossible de creer config.json : {e}")

    # Chromium tourne en thread daemon : quand restart_chromium() le tue, proc.wait() retourne
    # et le thread s'arrête proprement sans emporter toute l'application.
    chromium_thread = threading.Thread(target=start_chromium, daemon=True)
    chromium_thread.start()

    # Flask bloque le thread principal (non-daemon) → le processus reste en vie
    # même après un redémarrage Chromium.
    start_flask()
