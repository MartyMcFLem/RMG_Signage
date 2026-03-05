from flask import Flask, request, redirect, jsonify, send_from_directory, render_template
from werkzeug.utils import secure_filename
import os
import threading
import subprocess
import time
import json
import shutil
import shlex

# === CONFIG ===
HOME_DIR = os.path.expanduser("~")
MEDIA_DIR = os.environ.get("RMG_SIGNAGE_MEDIA_DIR", "/home/rmg/signage/medias")
CONFIG_FILE = os.environ.get("RMG_SIGNAGE_CONFIG_FILE", os.path.join(MEDIA_DIR, "config.json"))

MPV_BINARY = shutil.which("mpv") or "mpv"
GIT_BINARY = shutil.which("git") or "/usr/bin/git"
MPV_EXTRA_ARGS = os.environ.get("MPV_EXTRA_ARGS", "")
MPV_CONF_DIR = os.environ.get("MPV_CONF_DIR", "/home/rmg/.config/mpv")
LOG_FILE = os.path.join(MEDIA_DIR, "rmg_signage-mpv.log")

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
    "file_durations": {}     # durées par fichier {"photo.jpg": 12}
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


def get_local_ip():
    """Retourne l'adresse IP locale de la machine"""
    try:
        import socket as _socket
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        s.settimeout(0)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
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
        url = f"http://{ip}:5000"
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

        # ── Overlay plein écran semi-transparent (même opacité sur toute la surface) ──
        overlay = Image.new('RGBA', (W, H), (0, 0, 0, 175))
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
            f.write("background-color=#000000\n")
            f.write("alpha=blend\n")  # Fond transparent PNG → fondu sur background-color (évite le damier)
            f.write("panscan=1.0\n")  # Zoom pour remplir l'écran (coupe les bords si rapport différent)
            f.write(f"image-display-duration={config['image_duration']}\n")
            f.write(f"video-rotate={config.get('rotation', 0)}\n")
            f.write(f"input-ipc-server={MPV_SOCKET}\n")
    except:
        pass

    # Mode fichier unique
    if config.get('single_file_mode') and config.get('selected_file'):
        selected_path = os.path.join(MEDIA_DIR, config['selected_file'])
        if os.path.exists(selected_path):
            cmd_single = [MPV_BINARY, f"--config-dir={mpv_conf_dir}"]
            if lua_script:
                cmd_single.append(f"--script={lua_script}")
            cmd_single += ["--loop-file=inf", selected_path]
            return cmd_single

    # Mode playlist
    try:
        all_files = [f for f in os.listdir(MEDIA_DIR)
                     if os.path.isfile(os.path.join(MEDIA_DIR, f)) and is_media_file(f)]
    except:
        all_files = []

    if not all_files:
        # Pas de médias : afficher l'écran de bienvenue avec l'adresse IP
        welcome = generate_welcome_screen()
        if welcome and os.path.exists(welcome):
            cmd_welcome = [MPV_BINARY, f"--config-dir={mpv_conf_dir}", "--loop-file=inf", welcome]
            return cmd_welcome
        return None

    # Appliquer l'ordre personnalisé si shuffle désactivé
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
            # Sécuriser le nom de fichier (évite path traversal)
            safe_name = secure_filename(f.filename)
            if not safe_name:
                continue
            # Vérifier l'extension
            ext = os.path.splitext(safe_name.lower())[1]
            if ext not in ALLOWED_UPLOAD_EXTENSIONS:
                continue
            path = os.path.join(MEDIA_DIR, safe_name)
            f.save(path)
            files_saved += 1
        if files_saved:
            # Déclencher la mise à jour MPV (remplace l'écran de bienvenue si actif)
            update_mpv_playlist()
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

@app.route("/api/status")
def get_status():
    """Retourne l'état actuel de mpv"""
    running = mpv_process is not None and mpv_process.poll() is None
    try:
        media_count = len([f for f in os.listdir(MEDIA_DIR)
                           if os.path.isfile(os.path.join(MEDIA_DIR, f)) and is_media_file(f)])
    except Exception:
        media_count = 0
    return jsonify({
        "mpv_running": running,
        "media_count": media_count,
        "media_dir": MEDIA_DIR,
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


@app.route("/api/update/status", methods=["GET"])
def update_git_status():
    """Retourne les informations git actuelles (branche locale + dernier commit de origin/main)"""
    script_dir = os.environ.get("RMG_SIGNAGE_DIR", "/home/rmg/PhotoFrame")
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
        # Hash du dernier commit sur origin/main (fetch silencieux)
        try:
            subprocess.check_output(
                [GIT_BINARY, "fetch", "origin", "main"],
                cwd=script_dir, stderr=subprocess.STDOUT
            )
            remote_commit = subprocess.check_output(
                [GIT_BINARY, "rev-parse", "--short", "origin/main"],
                cwd=script_dir, stderr=subprocess.STDOUT
            ).decode().strip()
        except Exception:
            remote_commit = None
        return jsonify({
            "success": True,
            "commit": commit,
            "branch": branch,
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
    script_dir = os.environ.get("RMG_SIGNAGE_DIR", "/home/rmg/PhotoFrame")
    try:
        before = subprocess.check_output(
            [GIT_BINARY, "rev-parse", "--short", "HEAD"],
            cwd=script_dir, stderr=subprocess.STDOUT
        ).decode().strip()

        # 1. Récupérer origin/main
        fetch_out = subprocess.check_output(
            [GIT_BINARY, "fetch", "origin", "main"],
            cwd=script_dir, stderr=subprocess.STDOUT
        ).decode().strip()

        # 2. Basculer sur main si ce n'est pas déjà la branche active
        current_branch = subprocess.check_output(
            [GIT_BINARY, "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=script_dir, stderr=subprocess.STDOUT
        ).decode().strip()
        checkout_out = ""
        if current_branch != "main":
            checkout_out = subprocess.check_output(
                [GIT_BINARY, "checkout", "main"],
                cwd=script_dir, stderr=subprocess.STDOUT
            ).decode().strip()

        # 3. Aligner strictement sur origin/main (ignore toute modif locale)
        reset_out = subprocess.check_output(
            [GIT_BINARY, "reset", "--hard", "origin/main"],
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
                subprocess.Popen(["sudo", "systemctl", "restart", "rmg_signage"])
            threading.Thread(target=delayed_restart, daemon=True).start()

        return jsonify({
            "success": True,
            "updated": updated,
            "branch": "main",
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
    app.run(host="0.0.0.0", port=5000, threaded=True, use_reloader=False)


def start_mpv():
    """Lance MPV avec la config actuelle"""
    global mpv_process
    time.sleep(1)
    os.makedirs(MEDIA_DIR, exist_ok=True)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    except Exception:
        pass

    cmd = get_mpv_cmd()
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


def restart_mpv():
    """Redémarre MPV (protégé par verrou pour éviter les lancements multiples)"""
    global mpv_process
    if not _mpv_lock.acquire(blocking=False):
        # Un redémarrage est déjà en cours
        return
    try:
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
    threading.Thread(target=start_mpv, daemon=True).start()


if __name__ == "__main__":
    os.makedirs(MEDIA_DIR, exist_ok=True)

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
