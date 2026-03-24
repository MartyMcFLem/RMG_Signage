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

# Branche git et port Flask — injectes par systemd selon l'environnement (prod/dev)
GIT_BRANCH = os.environ.get("RMG_SIGNAGE_BRANCH", "main")
FLASK_PORT  = int(os.environ.get("RMG_SIGNAGE_PORT", 5000))

# Nom du service systemd (pour le redemarrage apres mise a jour)
SERVICE_NAME = os.environ.get("RMG_SIGNAGE_SERVICE", "rmg_signage")

# Repertoire projet (pour les operations git)
PROJECT_DIR = os.environ.get("RMG_SIGNAGE_DIR", os.path.dirname(os.path.abspath(__file__)))

# Extensions media autorisees
ALLOWED_EXTENSIONS = {
    '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp',
    '.heic', '.heif',
    '.mp4', '.avi', '.mkv', '.mov', '.webm', '.m4v'
}

# Taille maximale d'upload : 500 Mo
MAX_UPLOAD_SIZE = 500 * 1024 * 1024

# Cles de configuration autorisees (whitelist)
ALLOWED_CONFIG_KEYS = {
    'image_duration', 'shuffle', 'loop', 'dark_mode',
    'rotation', 'single_file_mode', 'selected_file',
    'file_order', 'file_durations',
}

# Configuration par defaut
_DEFAULT_CONFIG = {
    "image_duration": 8,
    "shuffle": True,
    "loop": True,
    "dark_mode": False,
    "rotation": 0,
    "single_file_mode": False,
    "selected_file": None,
    "file_order": [],
    "file_durations": {}
}

config = dict(_DEFAULT_CONFIG)
_config_lock = threading.Lock()

if os.path.exists(CONFIG_FILE):
    try:
        with open(CONFIG_FILE, 'r') as f:
            config.update(json.load(f))
    except (IOError, json.JSONDecodeError, ValueError):
        pass

mpv_process = None
_mpv_lock = threading.Lock()
MPV_SOCKET = "/tmp/mpv-socket"


_SERIAL_PATTERN = re.compile(r'^rmg-sign-[a-z0-9]{16}$')
_device_serial = None


def _safe_filename(filename, base_dir):
    """Valide qu'un nom de fichier est sur (pas de path traversal).
    Retourne le chemin absolu si valide, None sinon."""
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
    """Ecrit la config sur disque (doit etre appele avec _config_lock tenu)."""
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    except (IOError, OSError):
        pass


def _generate_serial_suffix():
    """Genere le suffixe du serial : CPU serial Pi complet (16 chars) ou UUID fallback."""
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
    """Retourne le numero de serie du device (rmg-sign-XXXXXXXXX).
    Priorite : hostname OS -> config.json -> generation depuis CPU serial ou UUID.
    En fallback, tente de corriger le hostname via sudo hostnamectl."""
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


def send_mpv_command(command):
    """Envoie une commande a MPV via le socket IPC"""
    try:
        import socket
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(MPV_SOCKET)
        sock.send((json.dumps({"command": command}) + "\n").encode('utf-8'))
        sock.close()
        return True
    except (ConnectionRefusedError, FileNotFoundError, OSError):
        return False


def update_mpv_playlist():
    """Met a jour la playlist MPV sans redemarrer (si possible)"""
    global mpv_process
    if mpv_process is None or mpv_process.poll() is not None:
        restart_mpv()
        return
    try:
        if config.get('single_file_mode') and config.get('selected_file'):
            selected_path = _safe_filename(config['selected_file'], MEDIA_DIR)
            if selected_path and os.path.exists(selected_path):
                send_mpv_command(["loadfile", selected_path, "replace"])
                send_mpv_command(["set_property", "loop-file", "inf"])
                time.sleep(0.2)
                return
        restart_mpv()
    except (ConnectionRefusedError, OSError):
        restart_mpv()


def is_media_file(filename):
    """Verifie si un fichier est un media valide"""
    if filename.startswith('.'):
        return False
    if filename in ('config.json', 'Thumbs.db', '.DS_Store'):
        return False
    ext = os.path.splitext(filename.lower())[1]
    return ext in ALLOWED_EXTENSIONS


def generate_lua_script():
    """Genere le script Lua mpv pour appliquer les durees personnalisees par fichier"""
    mpv_conf_dir = MPV_CONF_DIR or os.path.join(MEDIA_DIR, ".config")
    os.makedirs(mpv_conf_dir, exist_ok=True)
    script_path = os.path.join(mpv_conf_dir, "per_file_duration.lua")
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
    except (IOError, OSError):
        pass
    return script_path


def get_local_ip(retries=8, delay=2.0):
    """Retourne l'adresse IP locale. Reessaie plusieurs fois pour laisser
    le reseau s'initialiser au demarrage du Pi."""
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
    """Genere un ecran de bienvenue affichant l'adresse IP pour le premier demarrage.
    Fond base sur static/splash.png si disponible (floute + assombri).
    Inclut un QR code pointant vers l'interface web si le module qrcode est installe.
    Retourne le chemin vers l'image PNG generee, ou None en cas d'echec."""
    welcome_path = os.path.join(MEDIA_DIR, ".welcome_screen.png")
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
        ip = get_local_ip()
        url = f"http://{ip}:{FLASK_PORT}"
        W, H = 1920, 1080
        cx, cy = W // 2, H // 2

        script_dir = os.path.dirname(os.path.abspath(__file__))
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
        img = Image.alpha_composite(img.convert('RGBA'), overlay).convert('RGB')
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
        qr_x    = cx - qr_size // 2
        qr_y    = cy + 110

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
            qr = _qrcode.QRCode(
                box_size=10, border=3,
                error_correction=_qrcode.constants.ERROR_CORRECT_M
            )
            qr.add_data(url)
            qr.make(fit=True)
            qr_wrapped = qr.make_image(fill_color="black", back_color="white")
            if hasattr(qr_wrapped, 'get_image'):
                qr_pil = qr_wrapped.get_image().convert('RGB')
            else:
                qr_pil = qr_wrapped.convert('RGB')
            qr_pil = qr_pil.resize((qr_size, qr_size), Image.NEAREST)
            pad = 10
            draw.rectangle(
                [qr_x - pad, qr_y - pad, qr_x + qr_size + pad, qr_y + qr_size + pad],
                fill=(255, 255, 255)
            )
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


def get_mpv_cmd():
    """Genere la commande mpv avec la config actuelle"""
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
            f.write("panscan=1.0\n")
            f.write(f"image-display-duration={config['image_duration']}\n")
            f.write(f"video-rotate={config.get('rotation', 0)}\n")
            f.write(f"input-ipc-server={MPV_SOCKET}\n")
    except (IOError, OSError):
        pass

    rotation = config.get('rotation', 0)

    # Mode fichier unique
    if config.get('single_file_mode') and config.get('selected_file'):
        selected_path = _safe_filename(config['selected_file'], MEDIA_DIR)
        if selected_path and os.path.exists(selected_path):
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
    except OSError:
        all_files = []

    if not all_files:
        welcome = generate_welcome_screen()
        if welcome and os.path.exists(welcome):
            cmd_welcome = [MPV_BINARY, f"--config-dir={mpv_conf_dir}", f"--video-rotate={rotation}", "--loop-file=inf", welcome]
            return cmd_welcome
        return None

    # Appliquer l'ordre personnalise si shuffle desactive
    if not config.get('shuffle') and config.get('file_order'):
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
        for f in files:
            if not f.filename:
                continue
            safe_name = secure_filename(f.filename)
            if not safe_name:
                continue
            ext = os.path.splitext(safe_name.lower())[1]
            if ext not in ALLOWED_EXTENSIONS:
                continue
            path = os.path.join(MEDIA_DIR, safe_name)
            f.save(path)
            files_saved += 1
        if files_saved:
            update_mpv_playlist()
        return redirect("/")

    return render_template('index.html')


@app.route("/api/logo", methods=["POST", "DELETE"])
def manage_logo():
    """Upload ou suppression du logo (stocke dans static/logo.png)"""
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

    # DELETE
    if os.path.exists(logo_path):
        try:
            os.remove(logo_path)
            return jsonify({"success": True, "message": "Logo supprime"})
        except (IOError, OSError) as e:
            return jsonify({"success": False, "message": str(e)}), 500
    return jsonify({"success": False, "message": "Aucun logo trouve"}), 404


# === API ENDPOINTS ===

@app.route("/api/status")
def get_status():
    """Retourne l'etat actuel de mpv"""
    running = mpv_process is not None and mpv_process.poll() is None
    try:
        media_count = len([f for f in os.listdir(MEDIA_DIR)
                           if os.path.isfile(os.path.join(MEDIA_DIR, f)) and is_media_file(f)])
    except OSError:
        media_count = 0
    return jsonify({
        "mpv_running": running,
        "media_count": media_count,
        "media_dir": MEDIA_DIR,
        "serial": get_device_serial(),
    })


@app.route("/api/files")
def list_files():
    try:
        files = [f for f in os.listdir(MEDIA_DIR)
                 if os.path.isfile(os.path.join(MEDIA_DIR, f)) and is_media_file(f)]
        files.sort()
        return jsonify(files)
    except OSError:
        return jsonify([])


@app.route("/media/<filename>")
def serve_media(filename):
    safe = secure_filename(filename)
    if not safe:
        return jsonify({"success": False, "message": "Nom de fichier invalide"}), 400
    return send_from_directory(MEDIA_DIR, safe)


@app.route("/api/delete/<filename>", methods=["DELETE"])
def delete_file(filename):
    path = _safe_filename(filename, MEDIA_DIR)
    if not path:
        return jsonify({"success": False, "message": "Nom de fichier invalide"}), 400
    if not os.path.exists(path):
        return jsonify({"success": False, "message": "Fichier introuvable"}), 404
    try:
        os.remove(path)
        safe = secure_filename(filename)
        with _config_lock:
            if safe in config.get('file_durations', {}):
                config['file_durations'].pop(safe)
            if safe in config.get('file_order', []):
                config['file_order'].remove(safe)
            _save_config()
        return jsonify({"success": True, "message": f"{safe} supprime"})
    except (IOError, OSError) as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/config", methods=["GET", "POST"])
def manage_config():
    global config
    if request.method == "POST":
        data = request.json
        if not isinstance(data, dict):
            return jsonify({"success": False, "message": "JSON invalide"}), 400

        with _config_lock:
            old_config = config.copy()
            # Ne pas ecraser file_order et file_durations via cet endpoint
            data.pop('file_order', None)
            data.pop('file_durations', None)
            # Whitelist des cles autorisees
            filtered = {k: v for k, v in data.items() if k in ALLOWED_CONFIG_KEYS}
            config.update(filtered)
            _save_config()

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

        return jsonify({"success": True, "message": "Configuration mise a jour"})
    return jsonify(config)


@app.route("/api/order", methods=["POST"])
def save_order():
    """Sauvegarde l'ordre personnalise des fichiers"""
    global config
    data = request.json
    with _config_lock:
        config['file_order'] = data.get('order', [])
        _save_config()
    if not config.get('shuffle'):
        restart_mpv()
    return jsonify({"success": True, "message": "Ordre sauvegarde"})


@app.route("/api/file-duration/<filename>", methods=["POST"])
def set_file_duration(filename):
    """Definit la duree d'affichage personnalisee d'un fichier"""
    global config
    data = request.json
    duration = data.get('duration')
    with _config_lock:
        if 'file_durations' not in config:
            config['file_durations'] = {}
        if duration is None:
            config['file_durations'].pop(filename, None)
        else:
            config['file_durations'][filename] = int(duration)
        _save_config()
    return jsonify({"success": True, "message": f"Duree mise a jour pour {filename}"})


@app.route("/api/control/<action>", methods=["POST"])
def control_mpv(action):
    global mpv_process
    if action == "restart":
        restart_mpv()
        return jsonify({"success": True, "message": "MPV redemarre"})
    elif action == "stop":
        if mpv_process:
            mpv_process.terminate()
            mpv_process = None
        return jsonify({"success": True, "message": "MPV arrete"})
    elif action == "next":
        if send_mpv_command(["playlist-next"]):
            return jsonify({"success": True, "message": "Fichier suivant"})
        return jsonify({"success": False, "message": "Commande echouee"}), 500
    elif action == "show-ip":
        welcome = generate_welcome_screen()
        if welcome and os.path.exists(welcome):
            mpv_conf_dir = MPV_CONF_DIR or os.path.join(MEDIA_DIR, ".config")
            cmd = [MPV_BINARY, f"--config-dir={mpv_conf_dir}", "--loop-file=inf", welcome]
            restart_mpv(override_cmd=cmd)
            return jsonify({"success": True, "message": "Ecran de connexion affiche"})
        return jsonify({"success": False, "message": "Impossible de generer l'ecran"}), 500
    return jsonify({"success": False, "message": "Action inconnue"}), 400


@app.route("/api/play-single/<filename>", methods=["POST"])
def play_single_file(filename):
    global config
    path = _safe_filename(filename, MEDIA_DIR)
    if not path or not os.path.exists(path):
        return jsonify({"success": False, "message": "Fichier introuvable"}), 404
    with _config_lock:
        config['single_file_mode'] = True
        config['selected_file'] = secure_filename(filename)
        _save_config()
    update_mpv_playlist()
    return jsonify({"success": True, "message": f"Affichage de {secure_filename(filename)} uniquement"})


@app.route("/api/play-all", methods=["POST"])
def play_all_files():
    global config
    with _config_lock:
        config['single_file_mode'] = False
        config['selected_file'] = None
        _save_config()
    restart_mpv()
    return jsonify({"success": True, "message": "Lecture de tous les fichiers"})


@app.route("/api/update/status", methods=["GET"])
def update_git_status():
    """Retourne les informations git actuelles (branche locale + dernier commit de origin)"""
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
    except (FileNotFoundError, OSError) as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/update", methods=["POST"])
def update_from_github():
    """Bascule sur la branche cible, aligne sur origin et redemarre si necessaire"""
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
        pull_out = "\n".join(output_lines)

        updated = before != after
        if updated:
            svc = SERVICE_NAME
            def delayed_restart():
                time.sleep(1.5)
                subprocess.Popen(["sudo", "systemctl", "restart", svc])
            threading.Thread(target=delayed_restart, daemon=True).start()

        return jsonify({
            "success": True,
            "updated": updated,
            "branch": GIT_BRANCH,
            "before": before,
            "after": after,
            "output": pull_out,
            "message": "Mise a jour effectuee, redemarrage en cours..." if updated else "Deja a jour"
        })
    except subprocess.CalledProcessError as e:
        return jsonify({"success": False, "message": e.output.decode().strip()}), 500
    except (FileNotFoundError, OSError) as e:
        return jsonify({"success": False, "message": str(e)}), 500


def start_flask():
    app.run(host="0.0.0.0", port=FLASK_PORT, threaded=True, use_reloader=False)


def start_mpv(override_cmd=None):
    """Lance MPV avec la config actuelle (ou une commande specifique si override_cmd)"""
    global mpv_process
    # Delai : laisse le splash (mpv DRM) etre tue par le watcher de
    # splash_helper.sh et liberer le device DRM avant que ce mpv ne demarre.
    time.sleep(4)
    os.makedirs(MEDIA_DIR, exist_ok=True)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    except (IOError, OSError):
        pass

    cmd = override_cmd or get_mpv_cmd()
    if cmd is None:
        print("Aucun fichier media trouve dans", MEDIA_DIR)
        print("   En attente de fichiers...")
        while True:
            time.sleep(5)
            cmd = get_mpv_cmd()
            if cmd:
                break

    extra_list = shlex.split(MPV_EXTRA_ARGS) if MPV_EXTRA_ARGS else []
    user_has_vo = any(a.startswith("--vo=") for a in extra_list)
    vo_candidates = [[]] if user_has_vo else [["--vo=gpu"], ["--vo=drm"], ["--vo=sdl"]]

    last_exception = None
    for vo_args in vo_candidates:
        attempt_cmd = list(cmd)
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
        except (IOError, OSError):
            pass

        logf = None
        try:
            logf = open(LOG_FILE, "ab")
            proc = subprocess.Popen(new_cmd, stdout=logf, stderr=logf)
            time.sleep(1.0)
            if proc.poll() is None:
                mpv_process = proc
                try:
                    proc.wait()
                finally:
                    logf.close()
                return
            else:
                logf.write((f"mpv exited quickly with code={proc.returncode}\n").encode('utf-8'))
                logf.close()
                last_exception = RuntimeError(f"mpv exited with code {proc.returncode}")
                continue
        except (IOError, OSError) as e:
            last_exception = e
            if logf:
                try:
                    logf.write((f"Exception launching mpv: {e}\n").encode('utf-8'))
                    logf.close()
                except (IOError, OSError):
                    pass
            continue

    print("Impossible de lancer MPV -- consultez", LOG_FILE)
    if last_exception:
        try:
            with open(LOG_FILE, "ab") as logf:
                logf.write((f"Final error: {last_exception}\n").encode('utf-8'))
        except (IOError, OSError):
            pass
    mpv_process = None


def restart_mpv(override_cmd=None):
    """Redemarre MPV (protege par verrou pour eviter les lancements multiples)"""
    global mpv_process
    if not _mpv_lock.acquire(blocking=False):
        return
    try:
        try:
            with open('/dev/tty1', 'wb') as _tty:
                _tty.write(b'\033[?25l\033[40m\033[2J\033[H')
        except (IOError, OSError):
            pass
        if mpv_process:
            try:
                mpv_process.terminate()
                mpv_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    mpv_process.kill()
                except OSError:
                    pass
            except OSError:
                pass
        mpv_process = None
    finally:
        _mpv_lock.release()
    threading.Thread(target=start_mpv, args=(override_cmd,), daemon=True).start()


if __name__ == "__main__":
    os.makedirs(MEDIA_DIR, exist_ok=True)
    print(f"Numero de serie : {get_device_serial()}")

    if not os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(config, f, indent=2)
            print(f"Fichier de configuration cree : {CONFIG_FILE}")
        except (IOError, OSError) as e:
            print(f"Impossible de creer config.json : {e}")

    mpv_thread = threading.Thread(target=start_mpv, daemon=True)
    mpv_thread.start()

    start_flask()
