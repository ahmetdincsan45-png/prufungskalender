from flask import send_from_directory
import os
import sqlite3
import threading
from pathlib import Path
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_cors import CORS
import requests
import json

# -------------------- Flask --------------------

app = Flask(__name__)
CORS(app)

# Favicon ve Apple Touch Icon rotaları
@app.route('/favicon.ico')
def favicon():
    return send_from_directory('static', 'favicon.ico', mimetype='image/x-icon')

@app.route('/apple-touch-icon.png')
def apple_touch_icon():
    return send_from_directory('static', 'apple-touch-icon.png', mimetype='image/png')

@app.route('/apple-touch-icon-precomposed.png')
def apple_touch_icon_pre():
    return send_from_directory('static', 'apple-touch-icon.png', mimetype='image/png')

# -------------------- DB Yolu (kalıcı disk) --------------------
DB_PATH = os.getenv("SQLITE_DB_PATH", "/var/data/prufungskalender.db")
DATA_DIR = Path(DB_PATH).parent
DATA_DIR.mkdir(parents=True, exist_ok=True)  # /var/data yoksa oluştur
CACHE_DIR = DATA_DIR / "ferien_cache"
FALLBACK_DIR = DATA_DIR / "ferien_fallback"
SEED_FALLBACK_DIR = Path(__file__).parent / "ferien_fallback_seed"
print("🗄️ Using SQLite path:", DB_PATH)

# -------------------- Bağlantı --------------------
def get_db_connection():
    # SQLite + gunicorn için güvenli ayarlar
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

# -------------------- İlk kurulum (1 kez) --------------------
_init_lock = threading.Lock()
_init_done = False
_seed_done = False

def seed_fallback_if_needed():
    """Repo ile gelen seed fallback JSON'larını kalıcı diske ilk çalıştırmada kopyala."""
    global _seed_done
    if _seed_done:
        return
    try:
        FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
        if SEED_FALLBACK_DIR.exists():
            for p in SEED_FALLBACK_DIR.glob("BY_*.json"):
                dst = FALLBACK_DIR / p.name
                if not dst.exists():
                    try:
                        dst.write_text(p.read_text(encoding='utf-8'), encoding='utf-8')
                        print(f"🌱 Seed fallback kopyalandı: {dst}")
                    except Exception as e:
                        print(f"Seed kopyalama hatası {p} -> {dst}: {e}")
    finally:
        _seed_done = True

def init_db():
    """Tabloları güvenli şekilde bir kere oluştur."""
    global _init_done
    if _init_done:
        return
    with _init_lock:
        if _init_done:
            return
        with get_db_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS exams (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subject     TEXT NOT NULL,
                    grade       TEXT NOT NULL DEFAULT '4A',
                    date        TEXT NOT NULL,
                    start_time  TEXT NOT NULL DEFAULT '08:00',
                    end_time    TEXT NOT NULL DEFAULT '16:00',
                    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
        # Veri dizinlerini ve seed fallback'leri hazırla
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        seed_fallback_if_needed()
        _init_done = True
        print(f"✅ SQLite initialized at {DB_PATH}")

# Flask 3.x: before_first_request yerine before_request ile garanti
@app.before_request
def ensure_inited():
    init_db()

# -------------------- Routes --------------------
@app.route("/")
def index():
    try:
        with get_db_connection() as conn:
            today = datetime.now().strftime('%Y-%m-%d')
            next_exam = conn.execute(
                "SELECT * FROM exams WHERE date >= ? ORDER BY date LIMIT 1",
                (today,)
            ).fetchone()
        return render_template("index.html", next_exam=next_exam)
    except Exception as e:
        print("❌ Index error:", e)
        return render_template("index.html", next_exam=None)

@app.route('/events')
def events():
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute('SELECT * FROM exams ORDER BY date')
            exams = cur.fetchall()
            events_list = []
            today = datetime.now().strftime('%Y-%m-%d')
            for exam in exams:
                is_past = exam['date'] < today
                color = '#dc3545' if is_past else '#007bff'
                events_list.append({
                    'id': exam['id'],
                    'title': exam['subject'],
                    'start': f"{exam['date']}T{exam['start_time']}",
                    'end': f"{exam['date']}T{exam['end_time']}",
                    'backgroundColor': color,
                    'borderColor': color
                })
        # Bayern ferien-api.de'den güncel tatil tarihlerini çek, olmazsa yedek kullan
        backup_ferien = [
            {"start": "2025-03-03", "end": "2025-03-08"},
            {"start": "2025-04-14", "end": "2025-04-26"},
            {"start": "2025-06-10", "end": "2025-06-21"},
            {"start": "2025-08-01", "end": "2025-09-16"},
            {"start": "2025-11-03", "end": "2025-11-08"},  # Herbstferien 3-7 Kasım (7 dahil)
            {"start": "2025-12-22", "end": "2026-01-06"},
        ]
        ferien_event_count = 0
        # Görünüm aralığına göre ilgili yılları belirle
        years_to_fetch = set()
        start_arg = (request.args.get('start') or '')[:10]
        end_arg = (request.args.get('end') or '')[:10]
        try:
            if start_arg and end_arg:
                start_year = datetime.strptime(start_arg, "%Y-%m-%d").year
                end_year = datetime.strptime(end_arg, "%Y-%m-%d").year
                for y in range(start_year, end_year + 1):
                    years_to_fetch.add(y)
        except Exception as _:
            pass
        # Varsayılan: en azından mevcut yıl ve bir sonraki yıl
        if not years_to_fetch:
            now_y = datetime.now().year
            years_to_fetch.update({now_y, now_y + 1})

        try:
            added_pairs = set()
            cache_dir = CACHE_DIR
            fallback_dir = FALLBACK_DIR
            cache_dir.mkdir(parents=True, exist_ok=True)
            fallback_dir.mkdir(parents=True, exist_ok=True)
            for y in sorted(years_to_fetch):
                ferien = None
                ferien_url = f'https://ferien-api.de/api/v1/holidays/BY/{y}'
                try:
                    response = requests.get(ferien_url, timeout=5)
                    if response.status_code == 200:
                        ferien = response.json()
                        # Yalnızca dolu liste döndüyse cache'e yaz (boş [] ise yazma)
                        try:
                            if isinstance(ferien, list) and len(ferien) > 0:
                                (cache_dir / f"BY_{y}.json").write_text(response.text, encoding='utf-8')
                        except Exception:
                            pass
                    else:
                        raise RuntimeError(f"HTTP {response.status_code}")
                except Exception as _:
                    # Cache'den dene
                    cache_file = cache_dir / f"BY_{y}.json"
                    if cache_file.exists():
                        try:
                            ferien = requests.utils.json.loads(cache_file.read_text(encoding='utf-8'))
                        except Exception as _:
                            ferien = None
                # Eğer API boş liste döndüyse veya hiç veri yoksa, yıllık lokal fallback'i dene
                try:
                    if not ferien or (isinstance(ferien, list) and len(ferien) == 0):
                        fb_file = fallback_dir / f"BY_{y}.json"
                        if fb_file.exists():
                            try:
                                ferien = json.loads(fb_file.read_text(encoding='utf-8'))
                                print(f"ℹ️ Fallback ferien kullanıldı: {fb_file}")
                            except Exception:
                                pass
                except Exception:
                    pass
                if not ferien or (isinstance(ferien, list) and len(ferien) == 0):
                    continue
                for holiday in ferien:
                    start = holiday.get('start')
                    end = holiday.get('end')
                    if start and end:
                        end_dt = datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)
                        end_str = end_dt.strftime("%Y-%m-%d")
                        key = (start, end_str)
                        if key in added_pairs:
                            continue
                        added_pairs.add(key)
                        events_list.append({
                            'start': start,
                            'end': end_str,
                            'rendering': 'background',
                            'backgroundColor': 'black',
                            'display': 'background'
                        })
                        ferien_event_count += 1
        except Exception as e:
            print(f"Ferien API hatası: {e}")
        # Eğer API'dan hiç tatil eklenmediyse yedekleri ekle
        if ferien_event_count == 0:
            print("Ferien API'dan hiç tatil eklenmedi, yedekler kullanılıyor.")
            for holiday in backup_ferien:
                events_list.append({
                    'start': holiday['start'],
                    'end': holiday['end'],
                    'rendering': 'background',
                    'backgroundColor': 'black',
                    'display': 'background'
                })
        return jsonify(events_list)
    except Exception as e:
        print(f"❌ Events error: {e}")
        return jsonify([])

@app.route("/add", methods=["GET", "POST"])
def add_exam():
    if request.method == "POST":
        try:
            subject = (request.form.get("subject") or "").strip()
            date    = (request.form.get("date") or "").strip()
            if not subject or not date:
                return render_template("add.html", error="Bitte alle Felder ausfüllen!")
            with get_db_connection() as conn:
                conn.execute(
                    "INSERT INTO exams (subject, date) VALUES (?, ?)",
                    (subject, date)
                )
                conn.commit()
            return redirect(url_for("index"))
        except Exception as e:
            print("❌ Add exam error:", e)
            return render_template("add.html", error=f"Fehler: {e}")
    return render_template("add.html")

@app.route("/delete", methods=["GET", "POST"])
def delete_exam():
    try:
        if request.method == "POST":
            exam_id = (request.form.get("exam_id") or "").strip()
            if exam_id:
                with get_db_connection() as conn:
                    conn.execute("DELETE FROM exams WHERE id = ?", (exam_id,))
                    conn.commit()
                return redirect(url_for("delete_exam"))
        with get_db_connection() as conn:
            exams = conn.execute("SELECT * FROM exams ORDER BY date").fetchall()
        return render_template("delete.html", exams=exams)
    except Exception as e:
        print("❌ Delete exam error:", e)
        return render_template("delete.html", exams=[], error=str(e))

# Sağlık kontrolü (log ve yol teyidi için)
@app.route("/health")
def health():
    try:
        init_db()
        with get_db_connection() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        return jsonify({"ok": True, "db": DB_PATH, "journal_mode": mode})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# -------------------- Local çalıştırma --------------------
if __name__ == "__main__":
    init_db()
    print("🚀 Starting Flask (dev)")
    app.run(debug=True, host="0.0.0.0", port=5000)

# -------------------- Jinja2 filtre --------------------
@app.template_filter('strftime')
def _jinja2_filter_datetime(date_string, format='%d.%m.%Y'):
    from datetime import datetime
    try:
        return datetime.strptime(date_string, '%Y-%m-%d').strftime(format)
    except Exception:
        return date_string
