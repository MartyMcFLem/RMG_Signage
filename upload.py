from flask import Flask, request, redirect, jsonify, send_from_directory, render_template
from werkzeug.utils import secure_filename
import os
import re
import threading
import subprocess
import time
import json
import shutil
import shlex
import uuid as _uuid

# === CONFIG ===
HOME_DIR = os.path.expanduser("~")
MEDIA_DIR = os.environ.get("RMG_SIGNAGE_MEDIA_DIR", "/home/rmg/signage/medias")
CONFIG_FILE = os.environ.get("RMG_SIGNAGE_CONFIG_FILE", os.path.join(MEDIA_DIR, "config.json"))

MPV_BINARY = shutil.which("mpv") or "mpv"
GIT_BINARY = shutil.which("git") or "/usr/bin/git"
MPV_EXTRA_ARGS = os.environ.get("MPV_EXTRA_ARGS", "")
MPV_CONF_DIR = os.environ.get("MPV_CONF_DIR", os.path.join(HOME_DIR, ".config", "mpv"))
LOG_FILE = os.path.join(MEDIA_DIR, "rmg_signage-mpv.log")

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
    "active_playlist": None  # id de la playlist active (None = tous les fichiers)
}

if os.path.exists(CONFIG_FILE):
    try:
        with open(CONFIG_FILE, 'r') as f:
            config.update(json.load(f))
    except:
        pass

mpv_process = None
_mpv_lock = threading.Lock()
MPV_SOCKET = "/tmp/mpv-socket"

# Taille maximale d'upload : 500 Mo
MAX_UPLOAD_SIZE = 500 * 1024 * 1024

# Extensions media autorisées à l'upload
ALLOWED_UPLOAD_EXTENSIONS = {
    '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp',
    '.heic', '.heif',
    '.mp4', '.avi', '.mkv', '.mov', '.webm', '.m4v'
}


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

# Serial mis en cache au démarrage
_device_serial = None


def _generate_serial_suffix():
    """Génère le suffixe du serial : CPU serial Pi complet (16 chars) ou UUID fallback."""
    # Méthode 1 : CPU serial du Raspberry Pi (positions [10:26] de la ligne Serial)
    try:
        with open('/proc/cpuinfo', 'r') as f:
            for line in f:
                if line.startswith('Serial'):
                    suffix = line[10:26].strip()
                    if len(suffix) == 16 and re.match(r'^[0-9a-f]{16}$', suffix):
                        return suffix
    except Exception:
        pass
    # Méthode 2 : UUID aléatoire persisté (16 chars hex)
    serial_file = '/etc/rmg_serial'
    try:
        if os.path.exists(serial_file):
            stored = open(serial_file).read().strip()
            if len(stored) == 16:
                return stored
    except Exception:
        pass
    suffix = _uuid.uuid4().hex[:16]
    try:
        with open(serial_file, 'w') as f:
            f.write(suffix)
    except Exception:
        pass
    return suffix


def get_device_serial():
    """Retourne le numéro de série du device (rmg-sign-XXXXXXXXX).
    Priorité : hostname OS → config.json → génération depuis CPU serial ou UUID.
    En fallback, tente de corriger le hostname via sudo hostnamectl."""
    global _device_serial, config
    if _device_serial:
        return _device_serial

    import socket as _socket
    hostname = _socket.gethostname()
    if _SERIAL_PATTERN.match(hostname):
        _device_serial = hostname
        return _device_serial

    # Fallback : serial stocké dans config
    stored = config.get('device_serial', '')
    if stored and _SERIAL_PATTERN.match(stored):
        _device_serial = stored
        return _device_serial

    # Génération
    serial = f"rmg-sign-{_generate_serial_suffix()}"
    config['device_serial'] = serial
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    except Exception:
        pass

    # Tenter de corriger le hostname (nécessite sudoers rmg_hostname)
    try:
        subprocess.run(
            ['sudo', 'hostnamectl', 'set-hostname', serial],
            capture_output=True, timeout=5
        )
    except Exception:
        pass

    _device_serial = serial
    return _device_serial


def send_mpv_command(command):
    """Envoie une commande à MPV via le socket IPC"""
    try:
        import socket
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(MPV_SOCKET)
        sock.send((json.dumps({"command": command}) + "\n").encode('utf-8'))
        sock.close()
        return True
    except:
        return False


def update_mpv_playlist():
    """Met à jour la playlist MPV sans redémarrer (si possible)"""
    global mpv_process
    if mpv_process is None or mpv_process.poll() is not None:
        restart_mpv()
        return
    try:
        if config.get('single_file_mode') and config.get('selected_file'):
            selected_path = os.path.join(MEDIA_DIR, config['selected_file'])
            if os.path.exists(selected_path):
                send_mpv_command(["loadfile", selected_path, "replace"])
                send_mpv_command(["set_property", "loop-file", "inf"])
                time.sleep(0.2)
                return
        restart_mpv()
    except:
        restart_mpv()


def is_media_file(filename):
    """Vérifie si un fichier est un média valide"""
    if filename.startswith('.'):
        return False
    if filename in ['config.json', 'Thumbs.db', '.DS_Store']:
        return False
    ext = os.path.splitext(filename.lower())[1]
    if ext == '.json':
        return False
    return ext in {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp',
                   '.heic', '.heif',
                   '.mp4', '.avi', '.mkv', '.mov', '.webm', '.m4v'}


def generate_lua_script():
    """Génère le script Lua mpv pour appliquer les durées personnalisées par fichier"""
    mpv_conf_dir = MPV_CONF_DIR or os.path.join(MEDIA_DIR, ".config")
    os.makedirs(mpv_conf_dir, exist_ok=True)
    script_path = os.path.join(mpv_conf_dir, "per_file_duration.lua")
    # Utiliser des slashes Unix dans le script Lua (tourne sur Raspberry Pi)
    config_path_lua = CONFIG_FILE.replace('\\', '/')
    lua = "\n".join([
        "-- Script MPV : duree personnalisee par fichier",
        "local utils = require 'mp.utils'",
        "mp.register_event(\"start-file\", function()",
        "    local path = mp.get_property(\"path\")",
        "    if not path then return end",
        "    local _, filename = utils.split_path(path)",
        "    local f = io.open(\"" + config_path_lua + "\", \"r\")",
        "    if not f then return end",
        "    local content = f:read(\"*all\")",
        "    f:close()",
        "    local cfg = utils.parse_json(content)",
        "    if not cfg or not cfg.file_durations then return end",
        "    local dur = cfg.file_durations[filename]",
        "    if dur and type(dur) == \"number\" then",
        "        mp.set_property_number(\"image-display-duration\", dur)",
        "    end",
        "end)",
        ""
    ])
    try:
        with open(script_path, 'w') as f:
            f.write(lua)
    except:
        pass
    return script_path


def get_local_ip(retries=8, delay=2.0):
    """Retourne l'adresse IP locale. Réessaie plusieurs fois pour laisser
    le réseau s'initialiser au démarrage du Pi."""
    import socket as _socket

    def _try_udp():
        """Méthode UDP (pas de paquet réellement envoyé)"""
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip

    def _try_hostname():
        """Via le hostname local"""
        return _socket.gethostbyname(_socket.gethostname())

    def _try_ifconfig():
        """Lecture directe de l'interface réseau via ip/ifconfig"""
        for cmd in (
            ["ip", "-4", "addr", "show", "scope", "global"],
            ["hostname", "-I"],
        ):
            try:
                out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode()
                if cmd[0] == "hostname":
                    ips = [x for x in out.split() if x and not x.startswith("127.")]
                    if ips:
                        return ips[0]
                else:
                    import re
                    ips = re.findall(r"inet (\d+\.\d+\.\d+\.\d+)", out)
                    ips = [ip for ip in ips if not ip.startswith("127.")]
                    if ips:
                        return ips[0]
            except Exception:
                pass
        return None

    for attempt in range(retries):
        for method in (_try_udp, _try_hostname, _try_ifconfig):
            try:
                ip = method()
                if ip and not ip.startswith("127.") and ip != "0.0.0.0":
                    return ip
            except Exception:
                pass
        if attempt < retries - 1:
            time.sleep(delay)
    return "?.?.?.?"


def generate_welcome_screen():
    """Génère un écran de bienvenue affichant l'adresse IP pour le premier démarrage.
    Fond basé sur static/splash.png si disponible (flouté + assombri).
    Inclut un QR code pointant vers l'interface web si le module qrcode est installé.
    Retourne le chemin vers l'image PNG générée, ou None en cas d'échec."""
    welcome_path = os.path.join(MEDIA_DIR, ".welcome_screen.png")
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
        ip = get_local_ip()
        url = f"http://{ip}:{FLASK_PORT}"
        W, H = 1920, 1080
        cx, cy = W // 2, H // 2

        # ── Background : splash.png flouté/assombri, ou fond sombre par défaut ────
        script_dir = os.path.dirname(os.path.abspath(__file__))
        splash_path = os.path.join(script_dir, 'static', 'splash.png')
        if os.path.exists(splash_path):
            try:
                bg = Image.open(splash_path).convert('RGB').resize((W, H), Image.LANCZOS)
                bg = bg.filter(ImageFilter.GaussianBlur(radius=10))
                bg = ImageEnhance.Brightness(bg).enhance(0.35)
                img = bg
            except Exception:
                img = Image.new("RGB", (W, H), color=(10, 10, 26))
        else:
            img = Image.new("RGB", (W, H), color=(10, 10, 26))

        # ── Overlay plein écran semi-transparent 30% ──────────────────────────
        overlay = Image.new('RGBA', (W, H), (0, 0, 0, 77))
        img = Image.alpha_composite(img.convert('RGBA'), overlay).convert('RGB')
        draw = ImageDraw.Draw(img)

        def load_font(name, size):
            for path in [
                f"/usr/share/fonts/truetype/dejavu/{name}",
                f"/usr/share/fonts/truetype/liberation/Liberation{name}",
            ]:
                try:
                    return ImageFont.truetype(path, size)
                except Exception:
                    pass
            return ImageFont.load_default()

        font_title = load_font("DejaVuSans-Bold.ttf", 80)
        font_sub   = load_font("DejaVuSans.ttf", 44)
        font_url   = load_font("DejaVuSans-Bold.ttf", 60)
        font_hint  = load_font("DejaVuSans.ttf", 30)

        qr_size = 240
        qr_x    = cx - qr_size // 2
        qr_y    = cy + 110

        # ── Texte ───────────────────────────────────────────────────────────────
        try:
            draw.text((cx, cy - 260), "RMG Signage",
                      fill=(255, 255, 255), font=font_title, anchor="mm")
            draw.line([(cx - 340, cy - 188), (cx + 340, cy - 188)],
                      fill=(70, 70, 110), width=2)
            draw.text((cx, cy - 125), "Aucun média à afficher",
                      fill=(140, 140, 165), font=font_sub, anchor="mm")
            draw.text((cx, cy - 20), url,
                      fill=(74, 158, 255), font=font_url, anchor="mm")
            draw.text((cx, cy + 60), "Scannez le QR code ou connectez-vous :",
                      fill=(100, 100, 135), font=font_hint, anchor="mm")
        except TypeError:
            draw.text((50, 80),  "RMG Signage", fill=(255, 255, 255), font=font_title)
            draw.text((50, 220), "Aucun media",  fill=(140, 140, 165), font=font_sub)
            draw.text((50, 380), url,            fill=(74, 158, 255),  font=font_url)

        # ── QR code ─────────────────────────────────────────────────────────────
        try:
            import qrcode as _qrcode
            qr = _qrcode.QRCode(
                box_size=10, border=3,
                error_correction=_qrcode.constants.ERROR_CORRECT_M
            )
            qr.add_data(url)
            qr.make(fit=True)
            # make_image() retourne un objet PilImage (wrapper qrcode) ;
            # on récupère le PIL Image sous-jacent via get_image() si disponible,
            # sinon on force la conversion directement.
            qr_wrapped = qr.make_image(fill_color="black", back_color="white")
            if hasattr(qr_wrapped, 'get_image'):
                qr_pil = qr_wrapped.get_image().convert('RGB')
            else:
                qr_pil = qr_wrapped.convert('RGB')
            qr_pil = qr_pil.resize((qr_size, qr_size), Image.NEAREST)
            # Fond blanc légèrement plus grand pour lisibilité du scanner
            pad = 10
            draw.rectangle(
                [qr_x - pad, qr_y - pad, qr_x + qr_size + pad, qr_y + qr_size + pad],
                fill=(255, 255, 255)
            )
            img.paste(qr_pil, (qr_x, qr_y))
        except ImportError:
            # qrcode non installé — on répète l'URL à la place
            draw.text((cx, qr_y + qr_size // 2), url,
                      fill=(74, 158, 255), font=font_url, anchor="mm")

        os.makedirs(MEDIA_DIR, exist_ok=True)
        img.save(welcome_path)
        return welcome_path
    except Exception as e:
        print(f"⚠️  Écran de bienvenue non généré : {e}")
        return None


def get_mpv_cmd():
    """Génère la commande mpv avec la config actuelle"""
    mpv_conf_dir = MPV_CONF_DIR or os.path.join(MEDIA_DIR, ".config")
    os.makedirs(mpv_conf_dir, exist_ok=True)

    lua_script = generate_lua_script()
    lua_script = lua_script if os.path.exists(lua_script) else None

    mpv_conf = os.path.join(mpv_conf_dir, "mpv.conf")
    try:
        with open(mpv_conf, 'w') as f:
            f.write("fs=yes\n")
            f.write("border=no\n")
            f.write("osd-bar=no\n")
            f.write("background=0.0/0.0/0.0\n")
            f.write("panscan=0.0\n")  # 0 = fit (image entiere visible, barres noires si ratio different)
            f.write(f"image-display-duration={config['image_duration']}\n")
            f.write(f"video-rotate={config.get('rotation', 0)}\n")
            f.write(f"input-ipc-server={MPV_SOCKET}\n")
    except:
        pass

    rotation = config.get('rotation', 0)

    # Mode fichier unique
    if config.get('single_file_mode') and config.get('selected_file'):
        selected_path = os.path.join(MEDIA_DIR, config['selected_file'])
        if os.path.exists(selected_path):
            cmd_single = [MPV_BINARY, f"--config-dir={mpv_conf_dir}"]
            if lua_script:
                cmd_single.append(f"--script={lua_script}")
            cmd_single.append(f"--video-rotate={rotation}")
            cmd_single += ["--loop-file=inf", selected_path]
            return cmd_single

    # Mode playlist
    try:
        all_files = [f for f in os.listdir(MEDIA_DIR)
                     if os.path.isfile(os.path.join(MEDIA_DIR, f)) and is_media_file(f)]
    except:
        all_files = []

    if not all_files:
        welcome_path = os.path.join(MEDIA_DIR, ".welcome_screen.png")
        if os.path.exists(welcome_path) and (time.time() - os.path.getmtime(welcome_path)) < 300:
            welcome = welcome_path
        else:
            welcome = generate_welcome_screen()
        if welcome and os.path.exists(welcome):
            return [MPV_BINARY, f"--config-dir={mpv_conf_dir}", f"--video-rotate={rotation}", "--loop-file=inf", welcome]
        script_dir = os.path.dirname(os.path.abspath(__file__))
        splash_path = os.path.join(script_dir, 'static', 'splash.png')
        if os.path.exists(splash_path):
            return [MPV_BINARY, f"--config-dir={mpv_conf_dir}", f"--video-rotate={rotation}", "--loop-file=inf", splash_path]
        return None

    # Si une playlist est active, filtrer les fichiers
    active_pl_id = config.get('active_playlist')
    if active_pl_id:
        playlists = config.get('playlists', [])
        pl = next((p for p in playlists if p.get('id') == active_pl_id), None)
        if pl and pl.get('files'):
            all_set = set(all_files)
            pl_files = [f for f in pl['files'] if f in all_set]
            if pl_files:
                final_files = pl_files
            else:
                final_files = sorted(all_files)
        else:
            final_files = sorted(all_files)
    elif not config.get('shuffle') and config.get('file_order'):
        order = config['file_order']
        all_set = set(all_files)
        ordered = [f for f in order if f in all_set]
        remaining = sorted(f for f in all_files if f not in set(ordered))
        final_files = ordered + remaining
    else:
        final_files = sorted(all_files)

    cmd = [MPV_BINARY, f"--config-dir={mpv_conf_dir}"]
    if lua_script:
        cmd.append(f"--script={lua_script}")
    cmd.append(f"--video-rotate={rotation}")
    if config['loop']:
        cmd.append("--loop-playlist=inf")
    if config['shuffle']:
        cmd.append("--shuffle")
    cmd.extend(os.path.join(MEDIA_DIR, f) for f in final_files)
    return cmd


# === FLASK APP ===
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = MAX_UPLOAD_SIZE


@app.route("/", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        files = request.files.getlist("files")
        files_saved = 0
        reject_reason = ""
        for f in files:
            if not f.filename:
                continue
            safe_name = secure_filename(f.filename)
            if not safe_name:
                continue
            ext = os.path.splitext(safe_name.lower())[1]
            if ext not in ALLOWED_UPLOAD_EXTENSIONS:
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
            update_mpv_playlist()
        if reject_reason and files_saved == 0:
            return redirect("/?error=" + reject_reason)
        return redirect("/")

    return render_template('index.html')


@app.route("/api/logo", methods=["POST", "DELETE"])
def manage_logo():
    """Upload ou suppression du logo (stocké dans static/logo.png)"""
    logo_path = os.path.join(app.root_path, 'static', 'logo.png')
    if request.method == "POST":
        f = request.files.get('logo')
        if not f or not f.filename:
            return jsonify({"success": False, "message": "Pas de fichier"}), 400
        try:
            os.makedirs(os.path.join(app.root_path, 'static'), exist_ok=True)
            f.save(logo_path)
            return jsonify({"success": True, "message": "Logo mis à jour"})
        except Exception as e:
            return jsonify({"success": False, "message": str(e)}), 500

    # DELETE
    if os.path.exists(logo_path):
        try:
            os.remove(logo_path)
            return jsonify({"success": True, "message": "Logo supprimé"})
        except Exception as e:
            return jsonify({"success": False, "message": str(e)}), 500
    return jsonify({"success": False, "message": "Aucun logo trouvé"}), 404


# === API ENDPOINTS ===

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
    """Retourne l'état actuel de mpv"""
    running = mpv_process is not None and mpv_process.poll() is None
    try:
        media_count = len([f for f in os.listdir(MEDIA_DIR)
                           if os.path.isfile(os.path.join(MEDIA_DIR, f)) and is_media_file(f)])
    except Exception:
        media_count = 0
    storage = get_storage_info()
    return jsonify({
        "mpv_running": running,
        "media_count": media_count,
        "media_dir": MEDIA_DIR,
        "serial": get_device_serial(),
        "storage": storage,
    })


@app.route("/api/files")
def list_files():
    try:
        files = [f for f in os.listdir(MEDIA_DIR)
                 if os.path.isfile(os.path.join(MEDIA_DIR, f)) and is_media_file(f)]
        files.sort()
        return jsonify(files)
    except:
        return jsonify([])


@app.route("/media/<filename>")
def serve_media(filename):
    return send_from_directory(MEDIA_DIR, filename)


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
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/config", methods=["GET", "POST"])
def manage_config():
    global config
    if request.method == "POST":
        old_config = config.copy()
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

        if mpv_process and mpv_process.poll() is None:
            if old_config.get('shuffle') != config.get('shuffle'):
                send_mpv_command(["set_property", "shuffle", config['shuffle']])
            if not config.get('single_file_mode') and old_config.get('loop') != config.get('loop'):
                loop_value = "inf" if config['loop'] else "no"
                send_mpv_command(["set_property", "loop-playlist", loop_value])
            if old_config.get('image_duration') != config.get('image_duration'):
                restart_mpv()
            elif old_config.get('shuffle') != config.get('shuffle'):
                restart_mpv()
            elif old_config.get('rotation') != config.get('rotation'):
                send_mpv_command(["set_property", "video-rotate", config.get('rotation', 0)])
                restart_mpv()
        else:
            restart_mpv()

        return jsonify({"success": True, "message": "Configuration mise à jour"})
    return jsonify(config)


@app.route("/api/order", methods=["POST"])
def save_order():
    """Sauvegarde l'ordre personnalisé des fichiers"""
    global config
    data = request.json
    config['file_order'] = data.get('order', [])
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    except:
        pass
    if not config.get('shuffle'):
        restart_mpv()
    return jsonify({"success": True, "message": "Ordre sauvegardé"})


@app.route("/api/file-duration/<filename>", methods=["POST"])
def set_file_duration(filename):
    """Définit la durée d'affichage personnalisée d'un fichier"""
    global config
    data = request.json
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
    # Le script Lua relit la config à chaque fichier — pas besoin de redémarrer mpv
    return jsonify({"success": True, "message": f"Durée mise à jour pour {filename}"})


@app.route("/api/control/<action>", methods=["POST"])
def control_mpv(action):
    global mpv_process
    if action == "restart":
        restart_mpv()
        return jsonify({"success": True, "message": "MPV redémarré"})
    elif action == "stop":
        if mpv_process:
            mpv_process.terminate()
            mpv_process = None
        return jsonify({"success": True, "message": "MPV arrêté"})
    elif action == "next":
        if send_mpv_command(["playlist-next"]):
            return jsonify({"success": True, "message": "Fichier suivant"})
        return jsonify({"success": False, "message": "Commande échouée"}), 500
    elif action == "show-ip":
        welcome = generate_welcome_screen()
        if welcome and os.path.exists(welcome):
            mpv_conf_dir = MPV_CONF_DIR or os.path.join(MEDIA_DIR, ".config")
            cmd = [MPV_BINARY, f"--config-dir={mpv_conf_dir}", "--loop-file=inf", welcome]
            restart_mpv(override_cmd=cmd)
            return jsonify({"success": True, "message": "Écran de connexion affiché"})
        return jsonify({"success": False, "message": "Impossible de générer l'écran"}), 500
    return jsonify({"success": False, "message": "Action inconnue"}), 400


@app.route("/api/play-single/<filename>", methods=["POST"])
def play_single_file(filename):
    global config
    path = os.path.join(MEDIA_DIR, filename)
    if not os.path.exists(path):
        return jsonify({"success": False, "message": "Fichier introuvable"}), 404
    config['single_file_mode'] = True
    config['selected_file'] = filename
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    except:
        pass
    update_mpv_playlist()
    return jsonify({"success": True, "message": f"Affichage de {filename} uniquement"})


@app.route("/api/play-all", methods=["POST"])
def play_all_files():
    global config
    config['single_file_mode'] = False
    config['selected_file'] = None
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    except:
        pass
    restart_mpv()
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
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    except:
        pass
    restart_mpv()
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
        restart_mpv()
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
        restart_mpv()
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
    restart_mpv()
    return jsonify({"success": True, "message": f"Playlist '{pl['name']}' activee"})


@app.route("/api/update/status", methods=["GET"])
def update_git_status():
    """Retourne les informations git actuelles (branche locale + dernier commit de origin/main)"""
    script_dir = PROJECT_DIR
    try:
        # État local
        commit = subprocess.check_output(
            [GIT_BINARY, "rev-parse", "--short", "HEAD"],
            cwd=script_dir, stderr=subprocess.STDOUT
        ).decode().strip()
        branch = subprocess.check_output(
            [GIT_BINARY, "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=script_dir, stderr=subprocess.STDOUT
        ).decode().strip()
        msg = subprocess.check_output(
            [GIT_BINARY, "log", "-1", "--pretty=%s"],
            cwd=script_dir, stderr=subprocess.STDOUT
        ).decode().strip()
        # Hash du dernier commit sur origin/<branche> (fetch silencieux)
        try:
            subprocess.check_output(
                [GIT_BINARY, "fetch", "origin", GIT_BRANCH],
                cwd=script_dir, stderr=subprocess.STDOUT
            )
            remote_commit = subprocess.check_output(
                [GIT_BINARY, "rev-parse", "--short", f"origin/{GIT_BRANCH}"],
                cwd=script_dir, stderr=subprocess.STDOUT
            ).decode().strip()
        except Exception:
            remote_commit = None
        return jsonify({
            "success": True,
            "commit": commit,
            "branch": branch,
            "tracked_branch": GIT_BRANCH,
            "last_message": msg,
            "remote_commit": remote_commit,
            "up_to_date": commit == remote_commit if remote_commit else None
        })
    except subprocess.CalledProcessError as e:
        return jsonify({"success": False, "message": e.output.decode().strip()})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/update", methods=["POST"])
def update_from_github():
    """Bascule sur main, aligne sur origin/main et redémarre si nécessaire"""
    script_dir = PROJECT_DIR
    try:
        before = subprocess.check_output(
            [GIT_BINARY, "rev-parse", "--short", "HEAD"],
            cwd=script_dir, stderr=subprocess.STDOUT
        ).decode().strip()

        # 1. Récupérer origin/<branche>
        fetch_out = subprocess.check_output(
            [GIT_BINARY, "fetch", "origin", GIT_BRANCH],
            cwd=script_dir, stderr=subprocess.STDOUT
        ).decode().strip()

        # 2. Basculer sur la branche cible si nécessaire
        current_branch = subprocess.check_output(
            [GIT_BINARY, "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=script_dir, stderr=subprocess.STDOUT
        ).decode().strip()
        checkout_out = ""
        if current_branch != GIT_BRANCH:
            checkout_out = subprocess.check_output(
                [GIT_BINARY, "checkout", GIT_BRANCH],
                cwd=script_dir, stderr=subprocess.STDOUT
            ).decode().strip()

        # 3. Aligner strictement sur origin/<branche> (ignore toute modif locale)
        reset_out = subprocess.check_output(
            [GIT_BINARY, "reset", "--hard", f"origin/{GIT_BRANCH}"],
            cwd=script_dir, stderr=subprocess.STDOUT
        ).decode().strip()

        after = subprocess.check_output(
            [GIT_BINARY, "rev-parse", "--short", "HEAD"],
            cwd=script_dir, stderr=subprocess.STDOUT
        ).decode().strip()

        output_lines = [l for l in [fetch_out, checkout_out, reset_out] if l]
        pull_out = "\n".join(output_lines)

        updated = before != after
        if updated:
            def delayed_restart():
                time.sleep(1.5)
                subprocess.Popen(["sudo", "reboot"])
            threading.Thread(target=delayed_restart, daemon=True).start()

        return jsonify({
            "success": True,
            "updated": updated,
            "branch": GIT_BRANCH,
            "before": before,
            "after": after,
            "output": pull_out,
            "message": "Mise à jour effectuée, redémarrage en cours…" if updated else "Déjà à jour"
        })
    except subprocess.CalledProcessError as e:
        return jsonify({"success": False, "message": e.output.decode().strip()}), 500
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


def start_flask():
    # use_reloader=False évite le double fork qui casserait le thread MPV
    # threaded=True permet les requêtes concurrentes
    app.run(host="0.0.0.0", port=FLASK_PORT, threaded=True, use_reloader=False)


def start_mpv(override_cmd=None, boot_delay=True):
    """Lance MPV avec la config actuelle (ou une commande spécifique si override_cmd).
    boot_delay=True uniquement au premier démarrage (attend la libération DRM du splash).
    boot_delay=False pour les redémarrages runtime (restart_mpv, show-ip, etc.)."""
    global mpv_process

    if boot_delay and not override_cmd:
        # Pré-générer l'écran de bienvenue EN PARALLÈLE du délai DRM (si aucun média).
        # get_local_ip() peut prendre plusieurs secondes → on ne veut pas cumuler ce
        # délai avec le time.sleep(4) qui suit.
        os.makedirs(MEDIA_DIR, exist_ok=True)
        _welcome_ready = threading.Event()
        try:
            _has_media = any(
                is_media_file(f)
                for f in os.listdir(MEDIA_DIR)
                if os.path.isfile(os.path.join(MEDIA_DIR, f))
            )
        except Exception:
            _has_media = False
        if not _has_media:
            def _pregen():
                generate_welcome_screen()
                _welcome_ready.set()
            threading.Thread(target=_pregen, daemon=True).start()
        else:
            _welcome_ready.set()
        # Attendre que start_rmg_signage.sh ait quitté Plymouth et créé le ready file.
        # Le ready file est écrit APRÈS plymouth quit, donc MPV ne démarre
        # qu'une fois le DRM réellement libéré.
        _ready_files = [
            "/run/rmg_signage/ready",
            os.path.expanduser("~/rmg_signage-ready"),
            "/tmp/rmg_signage-ready",
        ]
        for _t in range(35):
            if any(os.path.exists(p) for p in _ready_files):
                break
            time.sleep(1)
        else:
            # Timeout : Plymouth probablement absent, on continue quand même
            pass
        # Petit délai supplémentaire pour que Plymouth libère effectivement le DRM
        time.sleep(0.5)
        _welcome_ready.wait(timeout=30)  # attend max 30s que le welcome soit prêt
    elif boot_delay:
        time.sleep(0.5)

    os.makedirs(MEDIA_DIR, exist_ok=True)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    except Exception:
        pass

    cmd = override_cmd or get_mpv_cmd()
    if cmd is None:
        print("⚠️ Aucun fichier média trouvé dans", MEDIA_DIR)
        print("   En attente de fichiers...")
        while True:
            time.sleep(5)
            cmd = get_mpv_cmd()
            if cmd:
                break

    extra_list = shlex.split(MPV_EXTRA_ARGS) if MPV_EXTRA_ARGS else []
    user_has_vo = any(a.startswith("--vo=") for a in extra_list)
    # --vo=gpu : backend moderne, détecte automatiquement X11 ou Wayland
    # --vo=drm : framebuffer direct (sans serveur graphique)
    # --vo=sdl : fallback universel
    vo_candidates = [[]] if user_has_vo else [["--vo=gpu"], ["--vo=drm"], ["--vo=sdl"]]

    last_exception = None
    for vo_args in vo_candidates:
        attempt_cmd = list(cmd)
        # Ensure --config-dir is passed immediately after the mpv binary
        config_arg = None
        rest_args = attempt_cmd[1:]
        for a in attempt_cmd[1:]:
            if isinstance(a, str) and a.startswith("--config-dir="):
                config_arg = a
                rest_args = [x for x in attempt_cmd[1:] if x != config_arg]
                break
        if config_arg:
            new_cmd = attempt_cmd[:1] + [config_arg] + vo_args + extra_list + rest_args
        else:
            new_cmd = attempt_cmd[:1] + vo_args + extra_list + attempt_cmd[1:]

        try:
            with open(LOG_FILE, "ab") as logf:
                logf.write(("\n\n--- Starting mpv: %s ---\n" % " ".join(new_cmd)).encode('utf-8'))
        except Exception:
            pass

        try:
            logf = open(LOG_FILE, "ab")
            proc = subprocess.Popen(new_cmd, stdout=logf, stderr=logf)
            time.sleep(1.0)
            if proc.poll() is None:
                mpv_process = proc
                try:
                    proc.wait()
                finally:
                    try:
                        logf.close()
                    except Exception:
                        pass
                # Si mpv_process a été remplacé par restart_mpv(), ne pas relancer ici
                if mpv_process is not proc:
                    return
                # Sortie inattendue de MPV (pas un arrêt volontaire) → relancer
                mpv_process = None
                try:
                    with open(LOG_FILE, "ab") as logf:
                        logf.write(b"mpv exited unexpectedly, restarting in 2s...\n")
                except Exception:
                    pass
                time.sleep(2)
                threading.Thread(target=start_mpv, kwargs={'boot_delay': False}, daemon=True).start()
                return
            else:
                try:
                    logf.write((f"mpv exited quickly with code={proc.returncode}\n").encode('utf-8'))
                    logf.close()
                except Exception:
                    pass
                last_exception = RuntimeError(f"mpv exited with code {proc.returncode}")
                continue
        except Exception as e:
            last_exception = e
            try:
                with open(LOG_FILE, "ab") as logf:
                    logf.write((f"Exception launching mpv: {e}\n").encode('utf-8'))
            except Exception:
                pass
            continue

    print("⚠️ Impossible de lancer MPV — consultez", LOG_FILE)
    if last_exception:
        try:
            with open(LOG_FILE, "ab") as logf:
                logf.write((f"Final error: {last_exception}\n").encode('utf-8'))
        except Exception:
            pass
    mpv_process = None


def restart_mpv(override_cmd=None):
    """Redémarre MPV (protégé par verrou pour éviter les lancements multiples)"""
    global mpv_process
    if not _mpv_lock.acquire(blocking=False):
        # Un redémarrage est déjà en cours
        return
    try:
        # Blackout tty1 AVANT de tuer mpv : quand mpv libère le DRM, la VT
        # sous-jacente affiche déjà un fond noir avec curseur masqué.
        try:
            with open('/dev/tty1', 'wb') as _tty:
                _tty.write(b'\033[?25l\033[40m\033[2J\033[H')
        except Exception:
            pass
        if mpv_process:
            try:
                mpv_process.terminate()
                mpv_process.wait(timeout=2)
            except Exception:
                try:
                    mpv_process.kill()
                except Exception:
                    pass
        mpv_process = None
    finally:
        _mpv_lock.release()
    threading.Thread(target=start_mpv, args=(override_cmd,), kwargs={'boot_delay': False}, daemon=True).start()


if __name__ == "__main__":
    os.makedirs(MEDIA_DIR, exist_ok=True)
    # Initialisation du serial dès le démarrage (corrige le hostname si nécessaire)
    print(f"🔑 Numéro de série : {get_device_serial()}")

    if not os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(config, f, indent=2)
            print(f"✅ Fichier de configuration créé : {CONFIG_FILE}")
        except Exception as e:
            print(f"⚠️ Impossible de créer config.json : {e}")

    # MPV tourne en thread daemon : quand restart_mpv() le tue, proc.wait() retourne
    # et le thread s'arrête proprement sans emporter toute l'application.
    mpv_thread = threading.Thread(target=start_mpv, daemon=True)
    mpv_thread.start()

    # Flask bloque le thread principal (non-daemon) → le processus reste en vie
    # même après un redémarrage mpv.
    start_flask()
