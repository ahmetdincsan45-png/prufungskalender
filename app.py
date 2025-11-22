from flask import send_from_directory
import os
import sqlite3
import threading
from pathlib import Path
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from flask_cors import CORS
import requests
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import hashlib

# -------------------- Flask --------------------

app = Flask(__name__)
app.secret_key = "prufungskalender_secret_key_2025_ahmet"  # Session için secret key
app.config['SESSION_COOKIE_SECURE'] = False  # HTTP için (HTTPS olsa True olmalı)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
CORS(app)

# Email konfigürasyonu
EMAIL_ADDRESS = "ahmetdincsan45@gmail.com"
EMAIL_PASSWORD = "jdygziqeduesbplk"  # Gmail uygulama şifresi (boşluksuz)
RECIPIENT_EMAIL = "ahmetdincsan45@gmail.com"

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
FEIERTAGE_CACHE_DIR = DATA_DIR / "feiertage_cache"
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS visits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    ip TEXT,
                    user_agent TEXT,
                    path TEXT
                )
            """)
            
            # Visits tablosunu sıfırla (yeni sistem için temiz başlangıç)
            conn.execute("DELETE FROM visits")
            
            conn.commit()
        # Veri dizinlerini ve seed fallback'leri hazırla
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
            FEIERTAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        seed_fallback_if_needed()
        _init_done = True
        print(f"✅ SQLite initialized at {DB_PATH}")

# Flask 3.x: before_first_request yerine before_request ile garanti
@app.before_request
def ensure_inited():
    init_db()
    # Her istekte ziyaret kaydet (bots hariç basit filtreleme)
    if request.endpoint and not request.path.startswith('/static'):
        try:
            # Kendi IP'ni filtrele
            ip = request.headers.get('X-Forwarded-For', request.remote_addr)
            if ip:
                ip = ip.split(',')[0].strip()
            if ip == '217.233.108.177':
                return  # Kendi ziyaretlerini sayma
            
            user_agent = request.headers.get('User-Agent', '')
            # Bot kontrolü (basit)
            bot_keywords = ['bot', 'crawler', 'spider', 'scraper']
            is_bot = any(keyword in user_agent.lower() for keyword in bot_keywords)
            if not is_bot:
                with get_db_connection() as conn:
                    # Aynı IP son 7 gün içinde kayıt edilmiş mi kontrol et
                    existing = conn.execute(
                        "SELECT id FROM visits WHERE ip = ? AND timestamp >= datetime('now', '-7 days') LIMIT 1",
                        (ip,)
                    ).fetchone()
                    
                    # Yoksa kaydet
                    if not existing:
                        conn.execute(
                            "INSERT INTO visits (ip, user_agent, path) VALUES (?, ?, ?)",
                            (ip, user_agent[:500], request.path)
                        )
                        conn.commit()
        except Exception:
            pass  # Sessizce devam et

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
            {"start": "2025-11-19", "end": "2025-11-19"},  # Buß- und Bettag (schulfrei)
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
            # Hafta sonlarını boyamamak için: verilen aralığı yalnızca hafta içi bloklar halinde arka plan olarak ekle
            def add_weekday_background_ranges(start_str: str, end_exclusive_str: str) -> int:
                appended = 0
                try:
                    d = datetime.strptime(start_str, "%Y-%m-%d")
                    end_ex = datetime.strptime(end_exclusive_str, "%Y-%m-%d")
                except Exception:
                    return 0
                run_start = None
                while d < end_ex:
                    if d.weekday() < 5:  # 0=Mon .. 4=Fri
                        if run_start is None:
                            run_start = d
                    else:
                        if run_start is not None:
                            s = run_start.strftime("%Y-%m-%d")
                            e = d.strftime("%Y-%m-%d")
                            key = (s, e)
                            if key not in added_pairs:
                                added_pairs.add(key)
                                events_list.append({
                                    'start': s,
                                    'end': e,
                                    'rendering': 'background',
                                    'backgroundColor': '#f0f0f0',
                                    'display': 'background'
                                })
                                appended += 1
                            run_start = None
                    d += timedelta(days=1)
                if run_start is not None:
                    s = run_start.strftime("%Y-%m-%d")
                    e = end_ex.strftime("%Y-%m-%d")
                    key = (s, e)
                    if key not in added_pairs:
                        added_pairs.add(key)
                        events_list.append({
                            'start': s,
                            'end': e,
                            'rendering': 'background',
                            'backgroundColor': '#f0f0f0',
                            'display': 'background'
                        })
                        appended += 1
                return appended
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
                        ferien_event_count += add_weekday_background_ranges(start, end_str)
        except Exception as e:
            print(f"Ferien API hatası: {e}")
        # Bayern resmî tatilleri (Feiertage) de arka plan olarak ekle (okullar kapalı)
        try:
            feiertage_added = 0
            for y in sorted(years_to_fetch):
                feiertage = None
                feiertage_url = f'https://date.nager.at/api/v3/PublicHolidays/{y}/DE'
                cache_file = FEIERTAGE_CACHE_DIR / f"DE_{y}.json"
                try:
                    resp = requests.get(feiertage_url, timeout=5)
                    if resp.status_code == 200:
                        feiertage = resp.json()
                        # doluysa cachele
                        try:
                            if isinstance(feiertage, list) and len(feiertage) > 0:
                                cache_file.write_text(resp.text, encoding='utf-8')
                        except Exception:
                            pass
                    else:
                        raise RuntimeError(f"HTTP {resp.status_code}")
                except Exception:
                    if cache_file.exists():
                        try:
                            feiertage = json.loads(cache_file.read_text(encoding='utf-8'))
                        except Exception:
                            feiertage = None
                if not feiertage or (isinstance(feiertage, list) and len(feiertage) == 0):
                    continue
                for ft in feiertage:
                    try:
                        # Yalnızca Bavyera için geçerli olan veya ülke çapında (global) olan tatilleri al
                        is_global = bool(ft.get('global'))
                        counties = ft.get('counties') or []
                        applies_to_by = is_global or ('DE-BY' in counties)
                        if not applies_to_by:
                            continue
                        date_str = ft.get('date')  # YYYY-MM-DD
                        if not date_str:
                            continue
                        # Tek günlük background event: end = date + 1 gün (exclusive end)
                        start_dt = datetime.strptime(date_str, "%Y-%m-%d")
                        # Hafta sonu ise atla (yalnızca hafta içi önemli)
                        if start_dt.weekday() >= 5:
                            continue
                        end_dt = start_dt + timedelta(days=1)
                        end_str = end_dt.strftime("%Y-%m-%d")
                        key = (date_str, end_str)
                        # Aynı aralık daha önce eklendiyse atla
                        if key in added_pairs:
                            continue
                        added_pairs.add(key)
                        events_list.append({
                            'start': date_str,
                            'end': end_str,
                            'rendering': 'background',
                            'backgroundColor': '#f0f0f0',
                            'display': 'background'
                        })
                        feiertage_added += 1
                    except Exception:
                        continue
        except Exception as e:
            print(f"Feiertage çekme hatası: {e}")
        # Eğer API'dan hiç tatil eklenmediyse yedekleri ekle (hafta sonlarını atla)
        if ferien_event_count == 0:
            print("Ferien API'dan hiç tatil eklenmedi, yedekler kullanılıyor.")
            for holiday in backup_ferien:
                try:
                    # backup 'end' değerini inclusive kabul edip +1 günle exclusive'e çevir
                    start_incl = holiday['start']
                    end_incl = holiday['end']
                    end_ex = (datetime.strptime(end_incl, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
                    _ = add_weekday_background_ranges(start_incl, end_ex)
                except Exception:
                    continue
        return jsonify(events_list)
    except Exception as e:
        print(f"❌ Events error: {e}")
        return jsonify([])

@app.route("/add", methods=["GET", "POST"])
def add_exam():
    if request.method == "POST":
        try:
            # Yeni form: bir veya birden çok ders subjects içinde virgülle gelir
            raw_subjects = (request.form.get("subjects") or "").strip()
            # Eski formdan gelen tekil alanı da destekle (geri uyumluluk)
            legacy_subject = (request.form.get("subject") or "").strip()
            date    = (request.form.get("date") or "").strip()
            if not raw_subjects and legacy_subject:
                raw_subjects = legacy_subject
            # Parse subjects
            subjects = [s.strip() for s in raw_subjects.split(',') if s.strip()]
            # Yinelenenleri temizle, makul üst sınır uygula
            seen = set()
            unique_subjects = []
            for s in subjects:
                key = s.lower()
                if key not in seen:
                    seen.add(key)
                    unique_subjects.append(s)
                if len(unique_subjects) >= 30:
                    break
            if not unique_subjects or not date:
                return render_template("add.html", error="Bitte alle Felder ausfüllen!")
            # Geçmiş tarih kontrolü - sessizce reddet
            today_str = datetime.now().strftime('%Y-%m-%d')
            if date < today_str:
                return redirect(url_for("index"))
            with get_db_connection() as conn:
                for s in unique_subjects:
                    conn.execute(
                        "INSERT INTO exams (subject, date) VALUES (?, ?)",
                        (s, date)
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
            rows = conn.execute("SELECT * FROM exams ORDER BY date").fetchall()
        # Her satıra biçimlenmiş tarih ekle
        exams = []
        today_str = datetime.now().strftime('%Y-%m-%d')
        for r in rows:
            date_str = r["date"]
            if isinstance(date_str, str) and len(date_str) == 10:
                formatted = f"{date_str[8:10]}.{date_str[5:7]}.{date_str[0:4]}"
                day_part = str(int(date_str[8:10]))  # Öncü sıfırı kaldır ("09" -> "9")
            else:
                formatted = date_str
                day_part = ''
            is_past = (date_str < today_str)
            exams.append({
                'id': r['id'],
                'subject': r['subject'],
                'date': date_str,
                'date_formatted': formatted,
                'grade': r.get('grade', '4A') if hasattr(r, 'get') else '4A',
                'is_past': is_past,
                'day': day_part
            })
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

@app.route("/stats", methods=["GET", "POST"])
def stats():
    """Gizli istatistik sayfası - şifreyle korumalı"""
    # POST ile şifre kontrolü (güvenli)
    if request.method == "POST":
        password = request.form.get('p', '')
        if password == '45ee551':
            # Cookie ile auth token oluştur
            auth_token = hashlib.sha256(f"{password}:prufungskalender".encode()).hexdigest()
            response = redirect(url_for('stats'))
            response.set_cookie('stats_auth', auth_token, max_age=86400)  # 24 saat
            return response
        else:
            # Yanlış şifre
            return """
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Giriş</title>
                <style>
                    * { margin: 0; padding: 0; box-sizing: border-box; }
                    body { 
                        font-family: system-ui, -apple-system, sans-serif; 
                        display: flex; 
                        justify-content: center; 
                        align-items: center; 
                        min-height: 100vh; 
                        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                        overflow: auto;
                        padding: 20px;
                    }
                    .container {
                        width: 100%;
                        display: flex;
                        justify-content: center;
                        align-items: center;
                        min-height: 100vh;
                    }
                    @keyframes fadeInUp {
                        from { opacity: 0; transform: translateY(30px); }
                        to { opacity: 1; transform: translateY(0); }
                    }
                    @keyframes shake {
                        0%, 100% { transform: translateX(0); }
                        25% { transform: translateX(-10px); }
                        75% { transform: translateX(10px); }
                    }
                    .login-box { 
                        background: rgba(255, 255, 255, 0.95); 
                        padding: 50px 40px; 
                        border-radius: 20px; 
                        box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                        animation: fadeInUp 0.6s ease-out, shake 0.5s ease-out;
                        backdrop-filter: blur(10px);
                        min-width: 350px;
                        max-width: 400px;
                        width: 100%;
                        transition: transform 0.3s ease;
                    }
                    .login-box.keyboard-open {
                        transform: translateY(-80px);
                    }
                    @media (max-width: 600px) {
                        .login-box {
                            min-width: unset;
                            padding: 40px 30px;
                        }
                    }
                    h2 { 
                        text-align: center; 
                        color: #333; 
                        margin-bottom: 10px;
                        font-size: 1.8em;
                    }
                    .error {
                        color: #dc3545;
                        text-align: center;
                        margin-bottom: 20px;
                        font-size: 0.9em;
                        font-weight: 500;
                    }
                    .input-group {
                        position: relative;
                        margin-bottom: 25px;
                    }
                    input { 
                        padding: 14px 45px 14px 14px; 
                        font-size: 16px; 
                        border: 2px solid #dc3545; 
                        border-radius: 10px; 
                        width: 100%;
                        transition: all 0.3s ease;
                        background: white;
                    }
                    input:focus {
                        outline: none;
                        border-color: #dc3545;
                        box-shadow: 0 0 0 3px rgba(220, 53, 69, 0.1);
                    }
                    .toggle-password {
                        position: absolute;
                        right: 12px;
                        top: 50%;
                        transform: translateY(-50%);
                        background: none;
                        border: none;
                        cursor: pointer;
                        font-size: 20px;
                        padding: 5px;
                        color: #666;
                        transition: color 0.3s;
                    }
                    .toggle-password:hover { color: #667eea; }
                    button.submit-btn { 
                        padding: 14px 24px; 
                        font-size: 16px; 
                        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                        color: white; 
                        border: none; 
                        border-radius: 10px; 
                        cursor: pointer; 
                        width: 100%;
                        font-weight: 600;
                        transition: transform 0.2s, box-shadow 0.3s;
                        position: relative;
                    }
                    button.submit-btn:hover { 
                        transform: translateY(-2px);
                        box-shadow: 0 10px 25px rgba(102, 126, 234, 0.4);
                    }
                    button.submit-btn:active {
                        transform: translateY(0);
                    }
                    @keyframes spin {
                        to { transform: rotate(360deg); }
                    }
                    .spinner {
                        display: none;
                        width: 20px;
                        height: 20px;
                        border: 3px solid rgba(255,255,255,0.3);
                        border-top-color: white;
                        border-radius: 50%;
                        animation: spin 0.8s linear infinite;
                        position: absolute;
                        left: 50%;
                        top: 50%;
                        transform: translate(-50%, -50%);
                    }
                    .loading .spinner { display: block; }
                    .loading .btn-text { opacity: 0; }
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="login-box" id="loginBox">
                        <h2>🔒 Stats</h2>
                        <div class="error">❌ Yanlış şifre!</div>
                        <form method="post" id="loginForm">
                            <div class="input-group">
                                <input type="password" name="p" id="password" placeholder="Şifre" autofocus required>
                                <button type="button" class="toggle-password" onclick="togglePassword()">👁️</button>
                            </div>
                            <button type="submit" class="submit-btn" id="submitBtn">
                                <span class="btn-text">Giriş</span>
                                <div class="spinner"></div>
                            </button>
                        </form>
                    </div>
                </div>
                <script>
                    function togglePassword() {
                        const input = document.getElementById('password');
                        const btn = document.querySelector('.toggle-password');
                        if (input.type === 'password') {
                            input.type = 'text';
                            btn.textContent = '🙈';
                        } else {
                            input.type = 'password';
                            btn.textContent = '👁️';
                        }
                    }
                    
                    const loginBox = document.getElementById('loginBox');
                    const passwordInput = document.getElementById('password');
                    
                    passwordInput.addEventListener('focus', function() {
                        setTimeout(function() {
                            loginBox.classList.add('keyboard-open');
                            passwordInput.scrollIntoView({ behavior: 'smooth', block: 'center' });
                        }, 300);
                    });
                    
                    passwordInput.addEventListener('blur', function() {
                        loginBox.classList.remove('keyboard-open');
                    });
                    
                    document.getElementById('loginForm').addEventListener('submit', function() {
                        document.getElementById('submitBtn').classList.add('loading');
                    });
                </script>
            </body>
            </html>
            """, 401
    
    # Legacy session gate removed
    if False:
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Giriş</title>
            <style>
                * { margin: 0; padding: 0; box-sizing: border-box; }
                body { 
                    font-family: system-ui, -apple-system, sans-serif; 
                    display: flex; 
                    justify-content: center; 
                    align-items: center; 
                    min-height: 100vh; 
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    overflow: auto;
                    padding: 20px;
                    padding-bottom: 120px; /* floating butonlar için boşluk */
                }
                .container {
                    width: 100%;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    min-height: 100vh;
                }
                @keyframes fadeInUp {
                    from { opacity: 0; transform: translateY(30px); }
                    to { opacity: 1; transform: translateY(0); }
                }
                .login-box { 
                    background: rgba(255, 255, 255, 0.95); 
                    padding: 50px 40px; 
                    border-radius: 20px; 
                    box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                    animation: fadeInUp 0.6s ease-out;
                    backdrop-filter: blur(10px);
                    min-width: 350px;
                    max-width: 400px;
                    width: 100%;
                    transition: transform 0.3s ease;
                }
                .login-box.keyboard-open {
                    transform: translateY(-80px);
                }
                @media (max-width: 600px) {
                    .login-box {
                        min-width: unset;
                        padding: 40px 30px;
                    }
                }
                h2 { 
                    text-align: center; 
                    color: #333; 
                    margin-bottom: 30px;
                    font-size: 1.8em;
                }
                .input-group {
                    position: relative;
                    margin-bottom: 25px;
                }
                input { 
                    padding: 14px 45px 14px 14px; 
                    font-size: 16px; 
                    border: 2px solid #e0e0e0; 
                    border-radius: 10px; 
                    width: 100%;
                    transition: all 0.3s ease;
                    background: white;
                }
                input:focus {
                    outline: none;
                    border-color: #667eea;
                    box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
                }
                .toggle-password {
                    position: absolute;
                    right: 12px;
                    top: 50%;
                    transform: translateY(-50%);
                    background: none;
                    border: none;
                    cursor: pointer;
                    font-size: 20px;
                    padding: 5px;
                    color: #666;
                    transition: color 0.3s;
                }
                .toggle-password:hover { color: #667eea; }
                button.submit-btn { 
                    padding: 14px 24px; 
                    font-size: 16px; 
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white; 
                    border: none; 
                    border-radius: 10px; 
                    cursor: pointer; 
                    width: 100%;
                    font-weight: 600;
                    transition: transform 0.2s, box-shadow 0.3s;
                    position: relative;
                }
                button.submit-btn:hover { 
                    transform: translateY(-2px);
                    box-shadow: 0 10px 25px rgba(102, 126, 234, 0.4);
                }
                button.submit-btn:active {
                    transform: translateY(0);
                }
                @keyframes spin {
                    to { transform: rotate(360deg); }
                }
                .spinner {
                    display: none;
                    width: 20px;
                    height: 20px;
                    border: 3px solid rgba(255,255,255,0.3);
                    border-top-color: white;
                    border-radius: 50%;
                    animation: spin 0.8s linear infinite;
                    position: absolute;
                    left: 50%;
                    top: 50%;
                    transform: translate(-50%, -50%);
                }
                .loading .spinner { display: block; }
                .loading .btn-text { opacity: 0; }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="login-box" id="loginBox">
                    <h2>🔒 Stats</h2>
                    <form method="get" id="loginForm">
                        <div class="input-group">
                            <input type="password" name="p" id="password" placeholder="Şifre" autofocus required>
                            <button type="button" class="toggle-password" onclick="togglePassword()">👁️</button>
                        </div>
                        <button type="submit" class="submit-btn" id="submitBtn">
                            <span class="btn-text">Giriş</span>
                            <div class="spinner"></div>
                        </button>
                    </form>
                </div>
            </div>
            <script>
                function togglePassword() {
                    const input = document.getElementById('password');
                    const btn = document.querySelector('.toggle-password');
                    if (input.type === 'password') {
                        input.type = 'text';
                        btn.textContent = '🙈';
                    } else {
                        input.type = 'password';
                        btn.textContent = '👁️';
                    }
                }
                
                // Mobil klavye açıldığında login kutusunu yukarı kaydır
                const loginBox = document.getElementById('loginBox');
                const passwordInput = document.getElementById('password');
                
                passwordInput.addEventListener('focus', function() {
                    // iOS ve Android için klavye tespit et
                    setTimeout(function() {
                        loginBox.classList.add('keyboard-open');
                        // Scroll to input için ekstra
                        passwordInput.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    }, 300);
                });
                
                passwordInput.addEventListener('blur', function() {
                    loginBox.classList.remove('keyboard-open');
                });
                
                document.getElementById('loginForm').addEventListener('submit', function() {
                    document.getElementById('submitBtn').classList.add('loading');
                });
                // İlk klavye açılışında bile kutuyu yukarı kaydırma iyileştirmesi
                (function initKeyboardAssist(){
                    const isMobile = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
                    if (!isMobile) return; // Masaüstünde gerek yok
                    let elevated = false;
                    function elevate(){
                        if (!elevated){
                            loginBox.classList.add('keyboard-open');
                            elevated = true;
                        }
                        setTimeout(()=>passwordInput.scrollIntoView({behavior:'smooth', block:'center'}),50);
                    }
                    passwordInput.addEventListener('focus', elevate, { once: false });
                    passwordInput.addEventListener('touchstart', elevate, { once: false });
                    if (window.visualViewport){
                        const initialVH = window.visualViewport.height;
                        window.visualViewport.addEventListener('resize', ()=>{
                            // Klavye açıldığında yükseklik küçülür
                            if (window.visualViewport.height < initialVH - 100){
                                elevate();
                            }
                        });
                    }
                })();
            </script>
        </body>
        </html>
        """, 401
    
    # Cookie kontrolü
    auth_token = request.cookies.get('stats_auth')
    expected_token = hashlib.sha256("45ee551:prufungskalender".encode()).hexdigest()
    
    if auth_token != expected_token:
        # Login formu göster
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Giriş</title>
            <style>
                * { margin: 0; padding: 0; box-sizing: border-box; }
                body { 
                    font-family: system-ui, -apple-system, sans-serif; 
                    display: flex; 
                    justify-content: center; 
                    align-items: center; 
                    min-height: 100vh; 
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    overflow: auto;
                    padding: 20px;
                    padding-bottom: 120px; /* floating butonlar için boşluk */
                }
                .container {
                    width: 100%;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    min-height: 100vh;
                }
                @keyframes fadeInUp {
                    from { opacity: 0; transform: translateY(30px); }
                    to { opacity: 1; transform: translateY(0); }
                }
                .login-box { 
                    background: rgba(255, 255, 255, 0.95); 
                    padding: 50px 40px; 
                    border-radius: 20px; 
                    box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                    animation: fadeInUp 0.6s ease-out;
                    backdrop-filter: blur(10px);
                    min-width: 350px;
                    max-width: 400px;
                    width: 100%;
                    transition: transform 0.3s ease;
                }
                .login-box.keyboard-open {
                    transform: translateY(-80px);
                }
                @media (max-width: 600px) {
                    .login-box {
                        min-width: unset;
                        padding: 40px 30px;
                    }
                }
                h2 { 
                    text-align: center; 
                    color: #333; 
                    margin-bottom: 30px;
                    font-size: 1.8em;
                }
                .input-group {
                    position: relative;
                    margin-bottom: 25px;
                }
                input { 
                    padding: 14px 45px 14px 14px; 
                    font-size: 16px; 
                    border: 2px solid #e0e0e0; 
                    border-radius: 10px; 
                    width: 100%;
                    transition: all 0.3s ease;
                    background: white;
                }
                input:focus {
                    outline: none;
                    border-color: #667eea;
                    box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
                }
                .toggle-password {
                    position: absolute;
                    right: 12px;
                    top: 50%;
                    transform: translateY(-50%);
                    background: none;
                    border: none;
                    cursor: pointer;
                    font-size: 20px;
                    padding: 5px;
                    color: #666;
                    transition: color 0.3s;
                }
                .toggle-password:hover { color: #667eea; }
                button.submit-btn { 
                    padding: 14px 24px; 
                    font-size: 16px; 
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white; 
                    border: none; 
                    border-radius: 10px; 
                    cursor: pointer; 
                    width: 100%;
                    font-weight: 600;
                    transition: transform 0.2s, box-shadow 0.3s;
                    position: relative;
                }
                button.submit-btn:hover { 
                    transform: translateY(-2px);
                    box-shadow: 0 10px 25px rgba(102, 126, 234, 0.4);
                }
                button.submit-btn:active {
                    transform: translateY(0);
                }
                @keyframes spin {
                    to { transform: rotate(360deg); }
                }
                .spinner {
                    display: none;
                    width: 20px;
                    height: 20px;
                    border: 3px solid rgba(255,255,255,0.3);
                    border-top-color: white;
                    border-radius: 50%;
                    animation: spin 0.8s linear infinite;
                    position: absolute;
                    left: 50%;
                    top: 50%;
                    transform: translate(-50%, -50%);
                }
                .loading .spinner { display: block; }
                .loading .btn-text { opacity: 0; }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="login-box" id="loginBox">
                    <h2>🔒 Stats</h2>
                    <form method="post" id="loginForm">
                        <div class="input-group">
                            <input type="password" name="p" id="password" placeholder="Şifre" autofocus required>
                            <button type="button" class="toggle-password" onclick="togglePassword()">👁️</button>
                        </div>
                        <button type="submit" class="submit-btn" id="submitBtn">
                            <span class="btn-text">Giriş</span>
                            <div class="spinner"></div>
                        </button>
                    </form>
                </div>
            </div>
            <script>
                function togglePassword() {
                    const input = document.getElementById('password');
                    const btn = document.querySelector('.toggle-password');
                    if (input.type === 'password') {
                        input.type = 'text';
                        btn.textContent = '🙈';
                    } else {
                        input.type = 'password';
                        btn.textContent = '👁️';
                    }
                }
                
                const loginBox = document.getElementById('loginBox');
                const passwordInput = document.getElementById('password');
                
                passwordInput.addEventListener('focus', function() {
                    setTimeout(function() {
                        loginBox.classList.add('keyboard-open');
                        passwordInput.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    }, 300);
                });
                
                passwordInput.addEventListener('blur', function() {
                    loginBox.classList.remove('keyboard-open');
                });
                
                document.getElementById('loginForm').addEventListener('submit', function() {
                    document.getElementById('submitBtn').classList.add('loading');
                });
                // İlk klavye açılışında bile kutuyu yukarı kaydırma iyileştirmesi
                (function initKeyboardAssist(){
                    const isMobile = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
                    if (!isMobile) return;
                    let elevated = false;
                    function elevate(){
                        if (!elevated){
                            loginBox.classList.add('keyboard-open');
                            elevated = true;
                        }
                        setTimeout(()=>passwordInput.scrollIntoView({behavior:'smooth', block:'center'}),50);
                    }
                    passwordInput.addEventListener('focus', elevate);
                    passwordInput.addEventListener('touchstart', elevate);
                    if (window.visualViewport){
                        const initialVH = window.visualViewport.height;
                        window.visualViewport.addEventListener('resize', ()=>{
                            if (window.visualViewport.height < initialVH - 100){
                                elevate();
                            }
                        });
                    }
                })();
            </script>
        </body>
        </html>
        """, 401
    
    # Authenticated - Stats sayfasını göster
    try:
        with get_db_connection() as conn:
            # Toplam ziyaret sayısı
            total = conn.execute("SELECT COUNT(*) FROM visits").fetchone()[0]
            # Bugünkü ziyaretler
            today = conn.execute(
                "SELECT COUNT(*) FROM visits WHERE DATE(timestamp) = DATE('now')"
            ).fetchone()[0]
            # Son 7 gün
            last_7_days = conn.execute(
                "SELECT COUNT(*) FROM visits WHERE timestamp >= datetime('now', '-7 days')"
            ).fetchone()[0]
            # Benzersiz IP sayısı
            unique_ips = conn.execute("SELECT COUNT(DISTINCT ip) FROM visits").fetchone()[0]
            
            # Saatlik dağılım (son 24 saat)
            hourly = conn.execute("""
                SELECT strftime('%H', timestamp) as hour, COUNT(*) as count
                FROM visits
                WHERE timestamp >= datetime('now', '-24 hours')
                GROUP BY hour
                ORDER BY hour
            """).fetchall()
            
            # Saatlik dağılım grafiği için HTML
            hourly_chart = "<div style='display: flex; align-items: flex-end; gap: 4px; height: 150px; margin: 20px 0;'>"
            hourly_data = {str(h[0]).zfill(2): h[1] for h in hourly}
            max_count = max(hourly_data.values()) if hourly_data else 1
            
            for hour in range(24):
                h_str = str(hour).zfill(2)
                count = hourly_data.get(h_str, 0)
                height_pct = (count / max_count * 100) if max_count > 0 else 0
                hourly_chart += f"""
                <div style='flex: 1; display: flex; flex-direction: column; align-items: center;'>
                    <div style='font-size: 0.7em; color: #666; margin-bottom: 4px;'>{count}</div>
                    <div style='width: 100%; background: #007bff; border-radius: 4px 4px 0 0; 
                                height: {height_pct}%; min-height: 2px;'></div>
                    <div style='font-size: 0.7em; color: #666; margin-top: 4px;'>{h_str}</div>
                </div>
                """
            hourly_chart += "</div>"
            
            # Son 20 ziyaret
            recent = conn.execute(
                "SELECT timestamp, ip, path FROM visits ORDER BY id DESC LIMIT 20"
            ).fetchall()
            
            html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Stats</title>
                <style>
                    * {{ box-sizing: border-box; }}
                    body {{ font-family: system-ui, -apple-system, sans-serif; padding: 15px; 
                            max-width: 1000px; margin: 0 auto; background: #f5f5f5; }}
                    h1 {{ color: #333; font-size: 1.5em; margin: 0 0 15px 0; }}
                    h2 {{ color: #555; margin-top: 25px; font-size: 1.2em; }}
                    .stat {{ background: white; padding: 12px 15px; margin: 8px 0; border-radius: 8px; 
                             box-shadow: 0 1px 3px rgba(0,0,0,0.1); display: flex; 
                             justify-content: space-between; align-items: center; }}
                    .stat-label {{ font-size: 0.95em; color: #666; }}
                    .stat-value {{ font-size: 1.5em; color: #007bff; font-weight: bold; }}
                    .chart-container {{ background: white; padding: 15px; border-radius: 8px; 
                                        box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin: 15px 0; 
                                        overflow-x: auto; }}
                    .chart-scroll {{ min-width: 600px; }}
                    table {{ width: 100%; border-collapse: collapse; margin-top: 15px; background: white; 
                             border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); 
                             font-size: 0.9em; }}
                    th, td {{ padding: 10px 8px; text-align: left; border-bottom: 1px solid #f0f0f0; }}
                    th {{ background: #f8f9fa; font-weight: 600; color: #555; position: sticky; top: 0; }}
                    .small {{ font-size: 0.8em; color: #666; }}
                    tr:hover {{ background: #f8f9fa; }}
                    .table-container {{ overflow-x: auto; -webkit-overflow-scrolling: touch; margin-bottom: 120px; }}
                    .fab-container {{
                        position: fixed;
                        bottom: 20px;
                        right: 20px;
                        display: flex;
                        gap: 12px;
                        z-index: 1000;
                    }}
                    .send-report-btn {{
                        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                        color: white;
                        border: none;
                        padding: 15px 25px;
                        border-radius: 50px;
                        font-size: 16px;
                        font-weight: 600;
                        cursor: pointer;
                        box-shadow: 0 4px 15px rgba(102, 126, 234, 0.4);
                        transition: transform 0.2s, box-shadow 0.3s;
                        text-decoration: none;
                        display: inline-block;
                    }}
                    .logout-btn {{
                        background: linear-gradient(135deg, #dc3545 0%, #c82333 100%);
                    }}
                    .send-report-btn:hover {{
                        transform: translateY(-3px);
                        box-shadow: 0 6px 20px rgba(102, 126, 234, 0.6);
                    }}
                    .send-report-btn:active {{
                        transform: translateY(0);
                    }}
                    @media (max-width: 600px) {{
                        body {{ padding: 10px; }}
                        h1 {{ font-size: 1.3em; }}
                        h2 {{ font-size: 1.1em; margin-top: 20px; }}
                        .stat {{ padding: 10px 12px; }}
                        .stat-value {{ font-size: 1.3em; }}
                        table {{ font-size: 0.8em; }}
                        th, td {{ padding: 8px 6px; }}
                    }}
                </style>
            </head>
            <body>
                <div class="fab-container">
                    <a href="/logout" class="send-report-btn logout-btn">🚪 Çıkış</a>
                    <a href="/send-report" class="send-report-btn">📧 Rapor Gönder</a>
                </div>
                <h1>📊 Ziyaretçi İstatistikleri</h1>
                <div class="stat"><span class="stat-label">Toplam Ziyaret</span><span class="stat-value">{total}</span></div>
                <div class="stat"><span class="stat-label">Bugün</span><span class="stat-value">{today}</span></div>
                <div class="stat"><span class="stat-label">Son 7 Gün</span><span class="stat-value">{last_7_days}</span></div>
                <div class="stat"><span class="stat-label">Benzersiz IP</span><span class="stat-value">{unique_ips}</span></div>
                
                <h2>📈 Saatlik Dağılım (Son 24 Saat)</h2>
                <div class="chart-container">
                    <div class="chart-scroll">
                        {hourly_chart}
                        <div style='text-align: center; color: #666; font-size: 0.9em; margin-top: 10px;'>Saat (00-23)</div>
                    </div>
                </div>
                
                <h2>🕐 Son 20 Ziyaret</h2>
                <div class="table-container">
                    <table>
                        <tr><th>Zaman</th><th>IP</th><th>Sayfa</th></tr>
            """
            for r in recent:
                html += f"<tr><td class='small'>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td></tr>"
            html += """
                    </table>
                </div>
            </body>
            </html>
            """
            return html
    except Exception as e:
        return f"Error: {e}", 500

# -------------------- Email Raporu --------------------
def send_weekly_report():
    """Haftalık istatistik raporunu mail olarak gönder"""
    try:
        with get_db_connection() as conn:
            # İstatistikleri topla
            total = conn.execute("SELECT COUNT(*) FROM visits").fetchone()[0]
            today = conn.execute(
                "SELECT COUNT(*) FROM visits WHERE DATE(timestamp) = DATE('now')"
            ).fetchone()[0]
            last_7_days = conn.execute(
                "SELECT COUNT(*) FROM visits WHERE timestamp >= datetime('now', '-7 days')"
            ).fetchone()[0]
            unique_ips = conn.execute("SELECT COUNT(DISTINCT ip) FROM visits").fetchone()[0]
            
            # Son 7 günlük günlük detay
            daily_stats = conn.execute("""
                SELECT DATE(timestamp) as day, COUNT(*) as count
                FROM visits
                WHERE timestamp >= datetime('now', '-7 days')
                GROUP BY day
                ORDER BY day DESC
            """).fetchall()
            
            daily_html = ""
            for row in daily_stats:
                daily_html += f"<tr><td>{row[0]}</td><td><strong>{row[1]}</strong> ziyaret</td></tr>"
        
        # HTML email içeriği
        html_content = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; background: #f5f5f5; padding: 20px; }}
                .container {{ max-width: 600px; margin: 0 auto; background: white; 
                             border-radius: 10px; padding: 30px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                h1 {{ color: #667eea; border-bottom: 3px solid #667eea; padding-bottom: 10px; }}
                .stat-box {{ background: #f8f9fa; padding: 15px; margin: 10px 0; 
                            border-radius: 8px; border-left: 4px solid #667eea; }}
                .stat-box strong {{ color: #667eea; font-size: 1.5em; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
                th, td {{ padding: 10px; text-align: left; border-bottom: 1px solid #eee; }}
                th {{ background: #f8f9fa; color: #555; }}
                .footer {{ margin-top: 30px; text-align: center; color: #999; font-size: 0.9em; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>📊 Haftalık Ziyaretçi Raporu</h1>
                <p>Merhaba! İşte son 7 günün istatistikleri:</p>
                
                <div class="stat-box">
                    <div>Toplam Ziyaret</div>
                    <strong>{total}</strong>
                </div>
                
                <div class="stat-box">
                    <div>Son 7 Gün</div>
                    <strong>{last_7_days}</strong>
                </div>
                
                <div class="stat-box">
                    <div>Bugün</div>
                    <strong>{today}</strong>
                </div>
                
                <div class="stat-box">
                    <div>Benzersiz Ziyaretçi</div>
                    <strong>{unique_ips}</strong>
                </div>
                
                <h2 style="color: #555; margin-top: 30px;">📅 Günlük Detay</h2>
                <table>
                    <tr><th>Tarih</th><th>Ziyaret</th></tr>
                    {daily_html}
                </table>
                
                <div class="footer">
                    <p>Bu rapor otomatik olarak oluşturulmuştur.</p>
                    <p>Prüfungskalender © {datetime.now().year}</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        # Email oluştur
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f'📊 Haftalık Rapor - {datetime.now().strftime("%d.%m.%Y")}'
        msg['From'] = EMAIL_ADDRESS
        msg['To'] = RECIPIENT_EMAIL
        
        html_part = MIMEText(html_content, 'html', 'utf-8')
        msg.attach(html_part)
        
        # Gmail SMTP ile gönder
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            smtp.send_message(msg)
        
        return True
    except Exception as e:
        print(f"❌ Email gönderme hatası: {e}")
        return False

@app.route("/send-report")
def send_report():
    """Manuel rapor gönderme endpoint'i (stats sayfasından erişilebilir)"""
    # Cookie kontrolü
    auth_token = request.cookies.get('stats_auth')
    expected_token = hashlib.sha256("45ee551:prufungskalender".encode()).hexdigest()
    
    if auth_token != expected_token:
        return redirect(url_for('stats'))
    
    success = send_weekly_report()
    if success:
        return """
        <html>
        <head>
            <meta charset="UTF-8">
            <meta http-equiv="refresh" content="3;url=/stats">
            <style>
                body { font-family: system-ui; display: flex; justify-content: center; 
                       align-items: center; height: 100vh; background: #f5f5f5; }
                .box { background: white; padding: 40px; border-radius: 12px; 
                       box-shadow: 0 2px 10px rgba(0,0,0,0.1); text-align: center; }
                .success { color: #28a745; font-size: 3em; }
            </style>
        </head>
        <body>
            <div class="box">
                <div class="success">✅</div>
                <h2>Rapor Gönderildi!</h2>
                <p>Email adresinizi kontrol edin.</p>
                <p style="color: #999;">3 saniye içinde geri dönülüyor...</p>
            </div>
        </body>
        </html>
        """
    else:
        return "Email gönderme başarısız", 500

@app.route("/logout")
def logout():
    """Stats sayfasından çıkış yap"""
    response = redirect(url_for('stats'))
    response.set_cookie('stats_auth', '', max_age=0)  # Cookie'yi sil
    return response

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
