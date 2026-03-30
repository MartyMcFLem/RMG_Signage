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
    shutil.which("chromium-browser") or
    shutil.which("chromium") or
    "chromium-browser"
)
GIT_BINARY = shutil.which("git") or "/usr/bin/git"
LOG_FILE = os.path.join(MEDIA_DIR, "rmg_signage.log")

GIT_BRANCH   = os.environ.get("RMG_SIGNAGE_BRANCH", "main")
FLASK_PORT   = int(os.environ.get("RMG_SIGNAGE_PORT", 5000))
SERVICE_NAME = os.environ.get("RMG_SIGNAGE_SERVICE", "rmg_signage")
PROJECT_DIR  = os.environ.get("RMG_SIGNAGE_DIR", os.path.dirname(os.path.abspath(__file__)))

ALLOWED_EXTENSIONS = {
    '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp',
    '.heic', '.heif',
    '.mp4', '.avi', '.mkv', '.mov', '.webm', '.m4v'
}

MAX_UPLOAD_SIZE = 500 * 1024 * 1024

ALLOWED_CONFIG_KEYS = {
    'image_duration', 'shuffle', 'loop', 'dark_mode',
    'rotation', 'single_file_mode', 'selected_file',
    'file_order', 'file_durations', 'weather_city',
}

# === PRESET LAYOUTS ===
# Zones: x_pct, y_pct, w_pct, h_pct sont des fractions 0.0–1.0 du viewport.
PRESET_LAYOUTS = {
    "fullscreen": [
        {"id": "z1", "type": "media",
         "x_pct": 0, "y_pct": 0, "w_pct": 1, "h_pct": 1,
         "config": {"fit": "cover"}, "widgets": []}
    ],
    "sidebar-right": [
        {"id": "z1", "type": "media",
         "x_pct": 0, "y_pct": 0, "w_pct": 0.7, "h_pct": 1,
         "config": {"fit": "cover"}, "widgets": []},
        {"id": "z2", "type": "widgets",
         "x_pct": 0.7, "y_pct": 0, "w_pct": 0.3, "h_pct": 1,
         "config": {"direction": "column"}, "widgets": [
             {"id": "w1", "type": "clock",   "order": 0,
              "config": {"show_date": True, "format_24h": True}},
             {"id": "w2", "type": "weather", "order": 1,
              "config": {"unit": "C"}},
             {"id": "w3", "type": "news_ticker", "order": 2,
              "config": {"rss_url": "", "item_count": 10}},
         ]}
    ],
    "sidebar-left": [
        {"id": "z1", "type": "widgets",
         "x_pct": 0, "y_pct": 0, "w_pct": 0.3, "h_pct": 1,
         "config": {"direction": "column"}, "widgets": [
             {"id": "w1", "type": "clock",   "order": 0,
              "config": {"show_date": True, "format_24h": True}},
             {"id": "w2", "type": "weather", "order": 1,
              "config": {"unit": "C"}},
             {"id": "w3", "type": "news_ticker", "order": 2,
              "config": {"rss_url": "", "item_count": 10}},
         ]},
        {"id": "z2", "type": "media",
         "x_pct": 0.3, "y_pct": 0, "w_pct": 0.7, "h_pct": 1,
         "config": {"fit": "cover"}, "widgets": []}
    ],
    "header-bar": [
        {"id": "z1", "type": "widgets",
         "x_pct": 0, "y_pct": 0, "w_pct": 1, "h_pct": 0.15,
         "config": {"direction": "row"}, "widgets": [
             {"id": "w1", "type": "clock",   "order": 0,
              "config": {"show_date": True, "format_24h": True}},
             {"id": "w2", "type": "weather", "order": 1,
              "config": {"unit": "C"}},
             {"id": "w3", "type": "custom_message", "order": 2,
              "config": {"text": "", "font_size": 20, "text_color": "#ffffff"}},
         ]},
        {"id": "z2", "type": "media",
         "x_pct": 0, "y_pct": 0.15, "w_pct": 1, "h_pct": 0.85,
         "config": {"fit": "cover"}, "widgets": []}
    ],
    "footer-bar": [
        {"id": "z1", "type": "media",
         "x_pct": 0, "y_pct": 0, "w_pct": 1, "h_pct": 0.85,
         "config": {"fit": "cover"}, "widgets": []},
        {"id": "z2", "type": "widgets",
         "x_pct": 0, "y_pct": 0.85, "w_pct": 1, "h_pct": 0.15,
         "config": {"direction": "row"}, "widgets": [
             {"id": "w1", "type": "clock",   "order": 0,
              "config": {"show_date": True, "format_24h": True}},
             {"id": "w2", "type": "weather", "order": 1,
              "config": {"unit": "C"}},
             {"id": "w3", "type": "news_ticker", "order": 2,
              "config": {"rss_url": "", "item_count": 8}},
         ]}
    ],
    "split-equal": [
        {"id": "z1", "type": "media",
         "x_pct": 0, "y_pct": 0, "w_pct": 0.5, "h_pct": 1,
         "config": {"fit": "cover"}, "widgets": []},
        {"id": "z2", "type": "widgets",
         "x_pct": 0.5, "y_pct": 0, "w_pct": 0.5, "h_pct": 1,
         "config": {"direction": "column"}, "widgets": [
             {"id": "w1", "type": "clock",   "order": 0,
              "config": {"show_date": True, "format_24h": True}},
             {"id": "w2", "type": "weather", "order": 1,
              "config": {"unit": "C"}},
             {"id": "w3", "type": "news_ticker", "order": 2,
              "config": {"rss_url": "", "item_count": 10}},
             {"id": "w4", "type": "custom_message", "order": 3,
              "config": {"text": "", "font_size": 24, "text_color": "#ffffff"}},
         ]}
    ],
}

_DEFAULT_CONFIG = {
    "image_duration":    8,
    "shuffle":           True,
    "loop":              True,
    "dark_mode":         False,
    "rotation":          0,
    "single_file_mode":  False,
    "selected_file":     None,
    "file_order":        [],
    "file_durations":    {},
    "weather_city":      "",
    "show_welcome":      False,
    "next_requested":    0,
    "active_template_id": None,
    "templates":         [],
}

config = dict(_DEFAULT_CONFIG)
_config_lock = threading.Lock()

if os.path.exists(CONFIG_FILE):
    try:
        with open(CONFIG_FILE, 'r') as f:
            config.update(json.load(f))
    except (IOError, json.JSONDecodeError, ValueError):
        pass

chromium_process = None
_chromium_lock   = threading.Lock()

_weather_cache = {"data": None, "fetched_at": 0}
_news_cache    = {"data": None, "fetched_at": 0, "url": ""}

_SERIAL_PATTERN = re.compile(r'^rmg-sign-[a-z0-9]{16}$')
_device_serial  = None


# === UTILITY ===

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
    return ext in ALLOWED_EXTENSIONS


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
        ip  = get_local_ip()
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


# === TEMPLATE HELPERS ===

def _make_default_template():
    zones = copy.deepcopy(PRESET_LAYOUTS["fullscreen"])
    return {
        "id":          "tpl-default",
        "name":        "Plein écran",
        "layout_type": "fullscreen",
        "created_at":  "2026-01-01T00:00:00",
        "zones":       zones,
    }


def _get_active_template():
    templates = config.get("templates", [])
    active_id = config.get("active_template_id")
    if active_id:
        t = next((t for t in templates if t["id"] == active_id), None)
        if t:
            return t
    if templates:
        return templates[0]
    return _make_default_template()


def _get_ordered_files():
    """Retourne la liste des fichiers media dans l'ordre configuré."""
    try:
        files = sorted([f for f in os.listdir(MEDIA_DIR)
                        if os.path.isfile(os.path.join(MEDIA_DIR, f)) and is_media_file(f)])
    except OSError:
        return []
    if not config.get('shuffle') and config.get('file_order'):
        order   = config['file_order']
        ordered = [f for f in order if f in set(files)]
        rest    = sorted(f for f in files if f not in set(ordered))
        return ordered + rest
    return files


# === CHROMIUM PROCESS MANAGEMENT ===

def get_chromium_cmd():
    return [
        CHROMIUM_BINARY,
        "--kiosk",
        "--noerrdialogs",
        "--disable-infobars",
        "--no-first-run",
        "--disable-translate",
        "--disable-features=TranslateUI",
        "--autoplay-policy=no-user-gesture-required",
        "--disable-session-crashed-bubble",
        "--ozone-platform=drm",
        f"http://localhost:{FLASK_PORT}/player",
    ]


def start_chromium(override_cmd=None):
    global chromium_process
    time.sleep(4)
    os.makedirs(MEDIA_DIR, exist_ok=True)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    except (IOError, OSError):
        pass
    cmd = override_cmd or get_chromium_cmd()
    try:
        with open(LOG_FILE, "ab") as logf:
            logf.write(f"\n--- Starting Chromium: {' '.join(cmd)} ---\n".encode())
        logf = open(LOG_FILE, "ab")
        proc = subprocess.Popen(cmd, stdout=logf, stderr=logf)
        time.sleep(1.0)
        if proc.poll() is None:
            chromium_process = proc
            proc.wait()
        logf.close()
    except (IOError, OSError) as e:
        print(f"Impossible de lancer Chromium : {e}")
    chromium_process = None


def restart_chromium(override_cmd=None):
    global chromium_process
    if not _chromium_lock.acquire(blocking=False):
        return
    try:
        try:
            with open('/dev/tty1', 'wb') as _tty:
                _tty.write(b'\033[?25l\033[40m\033[2J\033[H')
        except (IOError, OSError):
            pass
        if chromium_process:
            try:
                chromium_process.terminate()
                chromium_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    chromium_process.kill()
                except OSError:
                    pass
            except OSError:
                pass
        chromium_process = None
    finally:
        _chromium_lock.release()
    threading.Thread(target=start_chromium, args=(override_cmd,), daemon=True).start()


# === FLASK APP ===
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = MAX_UPLOAD_SIZE


@app.route("/", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        files       = request.files.getlist("files")
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
            f.save(os.path.join(MEDIA_DIR, safe_name))
            files_saved += 1
        if files_saved:
            with _config_lock:
                config['show_welcome'] = False
                _save_config()
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

@app.route("/api/status")
def get_status():
    running = chromium_process is not None and chromium_process.poll() is None
    try:
        media_count = len([f for f in os.listdir(MEDIA_DIR)
                           if os.path.isfile(os.path.join(MEDIA_DIR, f)) and is_media_file(f)])
    except OSError:
        media_count = 0
    return jsonify({
        "player_running": running,
        "mpv_running":    running,   # alias pour compatibilité
        "media_count":    media_count,
        "media_dir":      MEDIA_DIR,
        "serial":         get_device_serial(),
        "version":        get_app_version(),
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
        if not isinstance(data, dict):
            return jsonify({"success": False, "message": "JSON invalide"}), 400
        with _config_lock:
            data.pop('file_order',    None)
            data.pop('file_durations', None)
            filtered = {k: v for k, v in data.items() if k in ALLOWED_CONFIG_KEYS}
            config.update(filtered)
            _save_config()
        return jsonify({"success": True, "message": "Configuration mise a jour"})
    return jsonify(config)


@app.route("/api/order", methods=["POST"])
def save_order():
    global config
    data = request.json
    with _config_lock:
        config['file_order'] = data.get('order', [])
        _save_config()
    return jsonify({"success": True, "message": "Ordre sauvegarde"})


@app.route("/api/file-duration/<filename>", methods=["POST"])
def set_file_duration(filename):
    global config
    data     = request.json
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
def control_player(action):
    global chromium_process
    if action == "restart":
        restart_chromium()
        return jsonify({"success": True, "message": "Lecteur redemarre"})
    elif action == "stop":
        if chromium_process:
            chromium_process.terminate()
            chromium_process = None
        return jsonify({"success": True, "message": "Lecteur arrete"})
    elif action == "next":
        with _config_lock:
            config['next_requested'] = config.get('next_requested', 0) + 1
            _save_config()
        return jsonify({"success": True, "message": "Fichier suivant"})
    elif action == "show-ip":
        with _config_lock:
            config['show_welcome'] = True
            _save_config()
        return jsonify({"success": True, "message": "Ecran de connexion affiche"})
    return jsonify({"success": False, "message": "Action inconnue"}), 400


@app.route("/api/play-single/<filename>", methods=["POST"])
def play_single_file(filename):
    global config
    path = _safe_filename(filename, MEDIA_DIR)
    if not path or not os.path.exists(path):
        return jsonify({"success": False, "message": "Fichier introuvable"}), 404
    with _config_lock:
        config['single_file_mode'] = True
        config['selected_file']    = secure_filename(filename)
        config['show_welcome']     = False
        _save_config()
    return jsonify({"success": True,
                    "message": f"Affichage de {secure_filename(filename)} uniquement"})


@app.route("/api/play-all", methods=["POST"])
def play_all_files():
    global config
    with _config_lock:
        config['single_file_mode'] = False
        config['selected_file']    = None
        config['show_welcome']     = False
        _save_config()
    return jsonify({"success": True, "message": "Lecture de tous les fichiers"})


# === PLAYER STATE ===

@app.route("/api/player-state")
def get_player_state():
    with _config_lock:
        cfg = dict(config)

    active_template = _get_active_template()
    files           = _get_ordered_files()

    return jsonify({
        "active_template": active_template,
        "media_files":     files,
        "config": {
            "image_duration":  cfg.get("image_duration", 8),
            "file_durations":  cfg.get("file_durations", {}),
            "shuffle":         cfg.get("shuffle", True),
            "loop":            cfg.get("loop", True),
            "single_file_mode": cfg.get("single_file_mode", False),
            "selected_file":   cfg.get("selected_file"),
            "rotation":        cfg.get("rotation", 0),
            "show_welcome":    cfg.get("show_welcome", False),
            "next_requested":  cfg.get("next_requested", 0),
        },
        "weather_city": cfg.get("weather_city", ""),
        "serial":        get_device_serial(),
        "flask_port":    FLASK_PORT,
    })


# === WIDGET DATA ===

@app.route("/api/weather")
def get_weather():
    city = config.get("weather_city", "").strip()
    if not city:
        return jsonify({"success": False, "message": "Ville non configuree"}), 400
    now = time.time()
    if _weather_cache["data"] and (now - _weather_cache["fetched_at"]) < 600:
        return jsonify({"success": True, **_weather_cache["data"]})
    try:
        safe_city = urllib.parse.quote(city)
        url  = f"https://wttr.in/{safe_city}?format=j1"
        req  = urllib.request.Request(url, headers={"User-Agent": "RMGSignage/2.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = json.loads(resp.read().decode())
        cur  = raw["current_condition"][0]
        data = {
            "city":        city,
            "temp_c":      int(cur["temp_C"]),
            "feels_like":  int(cur["FeelsLikeC"]),
            "description": cur["weatherDesc"][0]["value"],
            "humidity":    int(cur["humidity"]),
            "icon":        _wttr_icon(int(cur.get("weatherCode", 113))),
        }
        _weather_cache["data"]       = data
        _weather_cache["fetched_at"] = now
        return jsonify({"success": True, **data})
    except Exception as e:
        if _weather_cache["data"]:
            return jsonify({"success": True, "stale": True, **_weather_cache["data"]})
        return jsonify({"success": False, "message": str(e)}), 503


def _wttr_icon(code):
    """Convertit un code météo wttr.in en emoji."""
    if code in (113,):                    return "☀️"
    if code in (116,):                    return "⛅"
    if code in (119, 122):               return "☁️"
    if code in (143, 248, 260):          return "🌫️"
    if code in (176, 293, 296, 299, 302,
                305, 308, 353, 356, 359): return "🌧️"
    if code in (179, 182, 185, 281, 284,
                311, 314, 317, 320, 323,
                326, 329, 332, 335, 338,
                350, 362, 365, 368, 371,
                374, 377):               return "🌨️"
    if code in (200, 386, 389, 392, 395): return "⛈️"
    return "🌡️"


@app.route("/api/news")
def get_news():
    # Récupère l'URL RSS depuis le premier widget news_ticker de la template active
    active   = _get_active_template()
    rss_url  = ""
    for zone in active.get("zones", []):
        for w in zone.get("widgets", []):
            if w.get("type") == "news_ticker":
                rss_url = w.get("config", {}).get("rss_url", "")
                if rss_url:
                    break
        if rss_url:
            break

    if not rss_url:
        return jsonify({"success": False, "message": "Aucune URL RSS configuree"}), 400

    now = time.time()
    if (_news_cache["data"] and _news_cache["url"] == rss_url
            and (now - _news_cache["fetched_at"]) < 300):
        return jsonify({"success": True, "items": _news_cache["data"]})

    try:
        try:
            import feedparser  # noqa: PLC0415
        except ImportError:
            return jsonify({"success": False,
                            "message": "feedparser non installe (pip install feedparser)"}), 503
        feed  = feedparser.parse(rss_url)
        items = [{"title": e.get("title", ""), "link": e.get("link", "")}
                 for e in feed.entries[:20]]
        _news_cache["data"]       = items
        _news_cache["fetched_at"] = now
        _news_cache["url"]        = rss_url
        return jsonify({"success": True, "items": items})
    except Exception as e:
        if _news_cache["data"]:
            return jsonify({"success": True, "stale": True, "items": _news_cache["data"]})
        return jsonify({"success": False, "message": str(e)}), 503


# === TEMPLATES API ===

@app.route("/api/templates", methods=["GET", "POST"])
def manage_templates():
    global config
    if request.method == "GET":
        templates = config.get("templates", [])
        active_id = config.get("active_template_id")
        result    = [
            {"id": t["id"], "name": t["name"], "layout_type": t.get("layout_type", "custom"),
             "is_active": t["id"] == active_id, "created_at": t.get("created_at", "")}
            for t in templates
        ]
        return jsonify(result)

    # POST — créer un nouveau template
    data = request.json
    if not isinstance(data, dict):
        return jsonify({"success": False, "message": "JSON invalide"}), 400

    name        = str(data.get("name", "Nouveau template")).strip()[:80]
    layout_type = data.get("layout_type", "custom")

    if layout_type in PRESET_LAYOUTS:
        zones = copy.deepcopy(PRESET_LAYOUTS[layout_type])
    else:
        zones = data.get("zones", [])
        layout_type = "custom"

    if not name:
        return jsonify({"success": False, "message": "Nom requis"}), 400

    tpl = {
        "id":          f"tpl-{_uuid.uuid4().hex[:8]}",
        "name":        name,
        "layout_type": layout_type,
        "created_at":  time.strftime("%Y-%m-%dT%H:%M:%S"),
        "zones":       zones,
    }

    with _config_lock:
        if "templates" not in config:
            config["templates"] = []
        config["templates"].append(tpl)
        if not config.get("active_template_id"):
            config["active_template_id"] = tpl["id"]
        _save_config()

    return jsonify({"success": True, "template": tpl}), 201


@app.route("/api/templates/<tpl_id>", methods=["GET", "PUT", "DELETE"])
def manage_template(tpl_id):
    global config
    templates = config.get("templates", [])
    idx       = next((i for i, t in enumerate(templates) if t["id"] == tpl_id), None)

    if idx is None:
        return jsonify({"success": False, "message": "Template introuvable"}), 404

    if request.method == "GET":
        return jsonify(templates[idx])

    if request.method == "PUT":
        data = request.json
        if not isinstance(data, dict):
            return jsonify({"success": False, "message": "JSON invalide"}), 400
        with _config_lock:
            tpl = config["templates"][idx]
            if "name" in data:
                tpl["name"] = str(data["name"]).strip()[:80]
            if "zones" in data:
                tpl["zones"]       = data["zones"]
                tpl["layout_type"] = "custom"
            if "layout_type" in data and data["layout_type"] in PRESET_LAYOUTS:
                tpl["zones"]       = copy.deepcopy(PRESET_LAYOUTS[data["layout_type"]])
                tpl["layout_type"] = data["layout_type"]
            _save_config()
        return jsonify({"success": True, "template": config["templates"][idx]})

    # DELETE
    if len(templates) <= 1:
        return jsonify({"success": False,
                        "message": "Impossible de supprimer le dernier template"}), 400
    with _config_lock:
        config["templates"].pop(idx)
        if config.get("active_template_id") == tpl_id:
            config["active_template_id"] = config["templates"][0]["id"]
        _save_config()
    return jsonify({"success": True, "message": "Template supprime"})


@app.route("/api/templates/<tpl_id>/activate", methods=["POST"])
def activate_template(tpl_id):
    global config
    templates = config.get("templates", [])
    if not any(t["id"] == tpl_id for t in templates):
        return jsonify({"success": False, "message": "Template introuvable"}), 404
    with _config_lock:
        config["active_template_id"] = tpl_id
        _save_config()
    return jsonify({"success": True, "message": "Template active"})


@app.route("/api/templates/<tpl_id>/zones/<zone_id>/widgets", methods=["POST"])
def add_widget(tpl_id, zone_id):
    """Ajoute un widget à une zone."""
    global config
    templates = config.get("templates", [])
    tpl = next((t for t in templates if t["id"] == tpl_id), None)
    if not tpl:
        return jsonify({"success": False, "message": "Template introuvable"}), 404
    zone = next((z for z in tpl.get("zones", []) if z["id"] == zone_id), None)
    if not zone:
        return jsonify({"success": False, "message": "Zone introuvable"}), 404

    data        = request.json or {}
    widget_type = data.get("type", "clock")
    defaults    = {
        "clock":          {"show_date": True, "format_24h": True},
        "weather":        {"unit": "C"},
        "news_ticker":    {"rss_url": "", "item_count": 10},
        "custom_message": {"text": "", "font_size": 24, "text_color": "#ffffff"},
    }
    widget = {
        "id":     f"w-{_uuid.uuid4().hex[:6]}",
        "type":   widget_type,
        "order":  len(zone.get("widgets", [])),
        "config": data.get("config", defaults.get(widget_type, {})),
    }
    with _config_lock:
        zone.setdefault("widgets", []).append(widget)
        _save_config()
    return jsonify({"success": True, "widget": widget}), 201


@app.route("/api/templates/<tpl_id>/zones/<zone_id>/widgets/<w_id>", methods=["PUT", "DELETE"])
def manage_widget(tpl_id, zone_id, w_id):
    global config
    tpl = next((t for t in config.get("templates", []) if t["id"] == tpl_id), None)
    if not tpl:
        return jsonify({"success": False, "message": "Template introuvable"}), 404
    zone = next((z for z in tpl.get("zones", []) if z["id"] == zone_id), None)
    if not zone:
        return jsonify({"success": False, "message": "Zone introuvable"}), 404
    widgets = zone.get("widgets", [])
    w_idx   = next((i for i, w in enumerate(widgets) if w["id"] == w_id), None)
    if w_idx is None:
        return jsonify({"success": False, "message": "Widget introuvable"}), 404

    if request.method == "PUT":
        data = request.json or {}
        with _config_lock:
            if "config" in data:
                widgets[w_idx]["config"].update(data["config"])
            if "order" in data:
                widgets[w_idx]["order"] = int(data["order"])
            _save_config()
        return jsonify({"success": True, "widget": widgets[w_idx]})

    with _config_lock:
        widgets.pop(w_idx)
        _save_config()
    return jsonify({"success": True, "message": "Widget supprime"})


# === GIT UPDATE ===

@app.route("/api/update/status", methods=["GET"])
def update_git_status():
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
                subprocess.Popen(["sudo", "systemctl", "restart", svc])
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
    app.run(host="0.0.0.0", port=FLASK_PORT, threaded=True, use_reloader=False)


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

    chromium_thread = threading.Thread(target=start_chromium, daemon=True)
    chromium_thread.start()

    start_flask()
