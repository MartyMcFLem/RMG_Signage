from flask import Flask, request, redirect, jsonify, send_from_directory, render_template
import os
import threading
import subprocess
import time
import json
import shutil
import shlex

# === CONFIG ===
HOME_DIR = os.path.expanduser("~")
MEDIA_DIR = os.environ.get("PHOTOFRAME_MEDIA_DIR", "/home/pi/cadre")
CONFIG_FILE = os.environ.get("PHOTOFRAME_CONFIG_FILE", os.path.join(MEDIA_DIR, "config.json"))

MPV_BINARY = shutil.which("mpv") or "mpv"
MPV_EXTRA_ARGS = os.environ.get("MPV_EXTRA_ARGS", "")
LOG_FILE = os.path.join(MEDIA_DIR, "photoframe-mpv.log")

# Configuration par défaut
config = {
    "image_duration": 8,
    "shuffle": True,
    "loop": True,
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
MPV_SOCKET = "/tmp/mpv-socket"


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
    mpv_conf_dir = os.path.join(MEDIA_DIR, ".config")
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


def get_mpv_cmd():
    """Génère la commande mpv avec la config actuelle"""
    mpv_conf_dir = os.path.join(MEDIA_DIR, ".config")
    os.makedirs(mpv_conf_dir, exist_ok=True)

    lua_script = generate_lua_script()

    mpv_conf = os.path.join(mpv_conf_dir, "mpv.conf")
    try:
        with open(mpv_conf, 'w') as f:
            f.write("fs=yes\n")
            f.write("border=no\n")
            f.write("osd-bar=no\n")
            f.write("background-color=#000000\n")
            f.write("vf=scale=min(4096,iw):min(4096,ih):force_original_aspect_ratio=decrease:flags=lanczos\n")
            f.write(f"image-display-duration={config['image_duration']}\n")
            f.write(f"input-ipc-server={MPV_SOCKET}\n")
    except:
        pass

    # Mode fichier unique
    if config.get('single_file_mode') and config.get('selected_file'):
        selected_path = os.path.join(MEDIA_DIR, config['selected_file'])
        if os.path.exists(selected_path):
            return [MPV_BINARY, f"--config-dir={mpv_conf_dir}",
                    f"--script={lua_script}", "--loop-file=inf", selected_path]

    # Mode playlist
    try:
        all_files = [f for f in os.listdir(MEDIA_DIR)
                     if os.path.isfile(os.path.join(MEDIA_DIR, f)) and is_media_file(f)]
    except:
        all_files = []

    if not all_files:
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

    cmd = [MPV_BINARY, f"--config-dir={mpv_conf_dir}", f"--script={lua_script}"]
    if config['loop']:
        cmd.append("--loop-playlist=inf")
    if config['shuffle']:
        cmd.append("--shuffle")
    cmd.extend(os.path.join(MEDIA_DIR, f) for f in final_files)
    return cmd


# === FLASK APP ===
app = Flask(__name__)


@app.route("/", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        files = request.files.getlist("files")
        for f in files:
            if f.filename:
                path = os.path.join(MEDIA_DIR, f.filename)
                f.save(path)
        return redirect("/")

    return render_template('index.html')


# === API ENDPOINTS ===

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


def start_flask():
    app.run(host="0.0.0.0", port=5000)


def start_mpv():
    """Lance MPV avec la config actuelle"""
    global mpv_process
    time.sleep(3)
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
    vo_candidates = [[]] if user_has_vo else [["--vo=drm"], ["--vo=opengl"], ["--vo=sdl"]]

    last_exception = None
    for vo_args in vo_candidates:
        attempt_cmd = list(cmd)
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
    """Redémarre MPV"""
    global mpv_process
    if mpv_process:
        try:
            mpv_process.terminate()
            mpv_process.wait(timeout=2)
        except:
            mpv_process.kill()
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

    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()

    start_mpv()
