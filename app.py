from flask import send_from_directory
import os
import sqlite3
import threading
from pathlib import Path
from datetime import datetime, timedelta
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from functools import wraps
from flask_cors import CORS
import requests
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import hashlib
from werkzeug.security import generate_password_hash, check_password_hash

# -------------------- Flask --------------------
app = Flask(__name__)
app.secret_key = "prufungskalender_secret_key_2025_ahmet"
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
CORS(app)

# Email konfigürasyonu
EMAIL_ADDRESS = "ahmetdincsan45@gmail.com"
EMAIL_PASSWORD = "jdygziqeduesbplk"
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

# Service Worker Route
@app.route('/static/sw.js')
def service_worker():
    response = send_from_directory('static', 'sw.js', mimetype='application/javascript')
    response.headers['Cache-Control'] = 'max-age=3600'
    response.headers['Service-Worker-Allowed'] = '/'
    return response

# Offline Page Route
@app.route('/offline.html')
def offline():
    return send_from_directory('static', 'offline.html', mimetype='text/html')

# ---- Stats Auth Helpers ----
def get_admin_credentials():
    with get_db_connection() as conn:
        row = conn.execute("SELECT username, password_hash FROM admin_credentials LIMIT 1").fetchone()
    return (row['username'], row['password_hash']) if row else (None, None)

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if session.get('stats_authed'):
            return fn(*args, **kwargs)
        return redirect(url_for('stats_login'))
    return wrapper

# Login formu ve giriş işlemi
@app.route('/stats/login', methods=['GET', 'POST'])
def stats_login():
    if request.method == 'POST':
        in_user = (request.form.get('username') or '').strip()
        in_pass = (request.form.get('password') or '').strip()
        admin_user, admin_hash = get_admin_credentials()
        user_match = (admin_user or '').strip().lower() == in_user.lower()
        pass_ok = bool(admin_hash) and check_password_hash(admin_hash, in_pass)
        if user_match and pass_ok:
            session['stats_authed'] = True
            session['stats_user'] = admin_user
            return redirect(url_for('stats'))
        error_msg = "Hatalı kullanıcı adı veya şifre"
    else:
        error_msg = None
    error_html = f"<div class='err'>{error_msg}</div>" if error_msg else ""
    return (
        f"""
        <!DOCTYPE html>
        <html><head><meta charset='UTF-8'><meta name='viewport' content='width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover'>
        <meta name='apple-mobile-web-app-capable' content='yes'>
        <title>Stats Giriş</title>
        <style>
            * {{ margin:0; padding:0; box-sizing:border-box; }}
            html, body {{ height:100%; overflow:hidden; }}
            body {{ font-family: system-ui, -apple-system, sans-serif; display:flex; align-items:center; justify-content:center; background:#f5f6fa; padding:16px; }}
            .box {{ background:#fff; padding:24px; border-radius:12px; box-shadow:0 10px 30px rgba(0,0,0,.08); width:100%; max-width:360px; }}
            h2 {{ margin:0 0 16px; font-size:1.2em; color:#333; text-align:center; }}
            .row {{ margin:10px 0; }}
            input {{ width:100%; padding:12px; font-size:16px; border:1px solid #ddd; border-radius:8px; }}
            button {{ width:100%; padding:12px; border:none; border-radius:8px; background:#667eea; color:#fff; font-weight:600; cursor:pointer; }}
            button:active {{ background:#5568d3; }}
            .err {{ color:#dc3545; font-size:.9em; margin-bottom:10px; text-align:center; }}
            @media (max-height:600px) {{ .box {{ padding:16px; }} h2 {{ font-size:1.1em; margin-bottom:12px; }} }}
        </style></head><body>
        <div class='box'>
            <h2>🔒 Stats Giriş</h2>
            {error_html}
            <form method='post' autocomplete='on'>
                <div class='row'><input type='text' name='username' placeholder='Kullanıcı adı' value='{request.form.get('username','')}' autocomplete='username' required></div>
                <div class='row'><input type='password' name='password' placeholder='Şifre' autocomplete='current-password' required></div>
                <div class='row'><button type='submit'>Giriş</button></div>
            </form>
        </div>
        </body></html>
        """
    )

@app.route('/stats/logout', methods=['POST', 'GET'])
def stats_logout():
    session.clear()
    return redirect(url_for('stats_login'))

# -------------------- DB Yolu --------------------
# Ortam değişkeni öncelikli. Yoksa Render/Heroku gibi ortamlarda kalıcı disk
# varsa (/var/data) onu kullan; yoksa /tmp’ye düş.
_env_db = os.getenv("SQLITE_DB_PATH")
if _env_db:
    DB_PATH = _env_db
else:
    DB_PATH = "/var/data/prufungskalender.db" if os.path.isdir("/var/data") else "/tmp/prufungskalender.db"
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
            # Sorguların hızlanması için tarih alanına indeks
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_exams_date ON exams(date)")
            except Exception:
                pass
            conn.execute("""
                CREATE TABLE IF NOT EXISTS visits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    ip TEXT,
                    user_agent TEXT,
                    path TEXT
                )
            """)
            # Ders havuzu tablosu (stats sayfasından yönetilecek)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS subjects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS admin_credentials (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Visits tablosunu sıfırla (yeni sistem için temiz başlangıç)
            conn.execute("DELETE FROM visits")

            # Admin kaydı yoksa seed
            existing_admin = conn.execute("SELECT id FROM admin_credentials LIMIT 1").fetchone()
            if not existing_admin:
                default_user = 'Ahmet'
                default_pass = '45ee551'
                pwd_hash = generate_password_hash(default_pass, method='pbkdf2:sha256', salt_length=16)
                conn.execute("INSERT INTO admin_credentials (username, password_hash) VALUES (?, ?)", (default_user, pwd_hash))
            
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
        # Avrupa/Berlin saatine göre 18:00 eşik kuralı
        now = datetime.now(ZoneInfo('Europe/Berlin')) if ZoneInfo else datetime.now()
        today = now.strftime('%Y-%m-%d')
        after_cutoff = now.hour >= 18
        query = (
            "SELECT * FROM exams WHERE date > ? ORDER BY date, id LIMIT 1"
            if after_cutoff
            else "SELECT * FROM exams WHERE date >= ? ORDER BY date, id LIMIT 1"
        )
        with get_db_connection() as conn:
            next_exam = conn.execute(query, (today,)).fetchone()
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
                # Yalnız gelecekteki sınavları kullanıcı sayfasından silelim
                with get_db_connection() as conn:
                    row = conn.execute("SELECT date FROM exams WHERE id = ?", (exam_id,)).fetchone()
                    if row:
                        today_str = datetime.now().strftime('%Y-%m-%d')
                        if row['date'] >= today_str:
                            conn.execute("DELETE FROM exams WHERE id = ?", (exam_id,))
                            conn.commit()
                return redirect(url_for("delete_exam"))
        with get_db_connection() as conn:
            # Gelecekteki sınavlar + son 10 geçmiş sınav (birlikte göster)
            today_str = datetime.now().strftime('%Y-%m-%d')
            future = conn.execute("SELECT * FROM exams WHERE date >= ? ORDER BY date", (today_str,)).fetchall()
            past10 = conn.execute("SELECT * FROM exams WHERE date < ? ORDER BY date DESC LIMIT 10", (today_str,)).fetchall()
            rows = list(future) + list(past10)
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

@login_required
@app.route("/stats/delete-past", methods=["GET", "POST"])
def stats_delete_past():
    """Geçmiş sınavları yalnız stats yetkisi ile silebilme sayfası"""
    try:
        if request.method == 'POST':
            exam_id = (request.form.get('exam_id') or '').strip()
            if exam_id:
                with get_db_connection() as conn:
                    conn.execute("DELETE FROM exams WHERE id = ?", (exam_id,))
                    conn.commit()
            return redirect(url_for('stats_delete_past'))
        with get_db_connection() as conn:
            today_str = datetime.now().strftime('%Y-%m-%d')
            rows = conn.execute("SELECT * FROM exams WHERE date < ? ORDER BY date DESC", (today_str,)).fetchall()
        # Basit liste HTML
        items = "".join([
            f"<tr><td>{r['id']}</td><td>{r['subject']}</td><td>{r['date']}</td>"
            f"<td><form method='post' style='display:inline'>"
            f"<input type='hidden' name='exam_id' value='{r['id']}'/>"
            f"<button type='submit' style='background:#dc3545;color:#fff;border:none;padding:6px 10px;border-radius:6px;cursor:pointer'>Sil</button>"
            f"</form></td></tr>" for r in rows
        ])
        return f"""
        <!DOCTYPE html>
        <html><head><meta charset='UTF-8'>
        <meta name='viewport' content='width=device-width, initial-scale=1.0'>
        <title>Geçmiş Sınavlar</title>
        <style>
            body {{ font-family: system-ui, -apple-system, sans-serif; padding: 12px; background: #f5f5f5; }}
            h1 {{ font-size: 1.3em; margin: 0 0 12px 0; }}
            table {{ width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,.1); }}
            th, td {{ padding: 8px; border-bottom: 1px solid #eee; text-align: left; font-size: .95em; }}
            th {{ background: #f8f9fa; }}
            a.back {{ display:inline-block; margin-bottom:10px; text-decoration:none; color:#667eea; font-weight:600; }}
        </style>
        </head><body>
            <a class='back' href='/stats'>← Stats</a>
            <h1>⌛ Geçmiş Sınavlar (Silme)</h1>
            <table>
                <tr><th>ID</th><th>Fach</th><th>Datum</th><th>Aktion</th></tr>
                {items}
            </table>
        </body></html>
        """
    except Exception as e:
        return f"Error: {e}", 500

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

# ---- Subjects API (public, read-only) ----
@app.route('/api/subjects')
def api_subjects():
    try:
        # Varsayılan havuz + DB'deki eklenenler = BİRLEŞİK liste (benzersiz)
        default_pool = ['Mathematik','Deutsch','HSU','Englisch','Ethik','Religion','Musik']
        with get_db_connection() as conn:
            rows = conn.execute("SELECT name FROM subjects").fetchall()
        seen = set()
        merged = []
        for name in default_pool + [r['name'] for r in rows]:
            if not name:
                continue
            key = name.strip()
            if not key:
                continue
            lk = key.lower()
            if lk not in seen:
                seen.add(lk)
                merged.append(key)
        merged.sort(key=lambda s: s.lower())
        return jsonify({"subjects": merged})
    except Exception as e:
        return jsonify({"subjects": [], "error": str(e)}), 500

@login_required
@app.route("/stats", methods=["GET"])
def stats():
    """İstatistikler (şifresiz)"""
    # Ek güvenlik: dekoratöre ek olarak içerden de kontrol et
    if not session.get('stats_authed'):
        return redirect(url_for('stats_login'))
    def get_admin():
        with get_db_connection() as conn:
            row = conn.execute("SELECT username, password_hash FROM admin_credentials LIMIT 1").fetchone()
        return (row['username'], row['password_hash']) if row else (None, None)
    def generate_token(pwd_hash):
        return hashlib.sha256(f"{pwd_hash}:prufungskalender".encode()).hexdigest()

    # Login denemesi
    if request.method == "POST" and request.form.get('login_attempt') == '1':
        in_user = (request.form.get('username') or '').strip()
        in_pass = (request.form.get('password') or '').strip()
        admin_user, admin_hash = get_admin()
        # Kullanıcı adı eşleşmesini büyük/küçük harf duyarsız yap
        user_match = (admin_user or '').strip().lower() == in_user.lower()
        pass_ok = bool(admin_hash) and check_password_hash(admin_hash, in_pass)
        if admin_user and user_match and pass_ok:
            token = generate_token(admin_hash)
            resp = redirect(url_for('stats'))
            resp.set_cookie('stats_auth', token, max_age=86400, httponly=True)
            return resp
        # Hatalı giriş
        return """
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.1/font/bootstrap-icons.css" rel="stylesheet">
                # Eski login formu POST'ları gelirse paneli göster
                <style>
                    return redirect(url_for('stats'))
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
                /* Edge/IE varsayılan şifre gözünü gizle */
                input[type="password"]::-ms-reveal,
                input[type="password"]::-ms-clear { display: none; }
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
                                                        <span class="toggle-password" aria-hidden="true">
                                                                <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" fill="currentColor" viewBox="0 0 16 16">
                                                                    <path d="M16 8s-3-5.5-8-5.5S0 8 0 8s3 5.5 8 5.5S16 8 16 8M1.173 8a13 13 0 0 1 1.66-2.043C4.12 4.668 5.88 3.5 8 3.5s3.879 1.168 5.168 2.457A13 13 0 0 1 14.828 8q-.086.13-.195.288c-.335.48-.83 1.12-1.465 1.755C11.879 11.332 10.119 12.5 8 12.5s-3.879-1.168-5.168-2.457A13 13 0 0 1 1.172 8z"/>
                                                                    <path d="M8 5.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5M4.5 8a3.5 3.5 0 1 1 7 0 3.5 3.5 0 0 1-7 0"/>
                                                                </svg>
                                                        </span>
                                                </div>
                        <button type="submit" class="submit-btn" id="submitBtn">
                            <span class="btn-text">Giriş</span>
                            <div class="spinner"></div>
                        </button>
                    </form>
                </div>
            </div>
            <script>
                // Basit göz togglesı: şifre görünür/gizli durumunu değiştirir.
                (function(){
                    const btn = document.querySelector('.toggle-password');
                    const input = document.getElementById('password');
                    if (!btn || !input) return;
                    const EYE = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" fill="currentColor" viewBox="0 0 16 16"><path d="M16 8s-3-5.5-8-5.5S0 8 0 8s3 5.5 8 5.5S16 8 16 8M1.173 8a13 13 0 0 1 1.66-2.043C4.12 4.668 5.88 3.5 8 3.5s3.879 1.168 5.168 2.457A13 13 0 0 1 14.828 8q-.086.13-.195.288c-.335.48-.83 1.12-1.465 1.755C11.879 11.332 10.119 12.5 8 12.5s-3.879-1.168-5.168-2.457A13 13 0 0 1 1.172 8z"/><path d="M8 5.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5M4.5 8a3.5 3.5 0 1 1 7 0 3.5 3.5 0 0 1-7 0"/></svg>';
                    const EYE_SLASH = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" fill="currentColor" viewBox="0 0 16 16"><path d="M13.359 11.238C15.06 9.72 16 8 16 8s-3-5.5-8-5.5a7 7 0 0 0-2.79.588l.77.771A6 6 0 0 1 8 3.5c2.12 0 3.879 1.168 5.168 2.457A13 13 0 0 1 14.828 8q-.086.13-.195.288c-.335.48-.83 1.12-1.465 1.755q-.247.248-.517.486z"/><path d="M11.297 9.176a3.5 3.5 0 0 0-4.474-4.474l.823.823a2.5 2.5 0 0 1 2.829 2.829zm-2.943 1.299.822.822a3.5 3.5 0 0 1-4.474-4.474l.823.823a2.5 2.5 0 0 0 2.829 2.829"/><path d="M3.35 5.47q-.27.24-.518.487A13 13 0 0 0 1.172 8l.195.288c.335.48.83 1.12 1.465 1.755C4.121 11.332 5.881 12.5 8 12.5c.716 0 1.39-.133 2.02-.36l.77.772A7 7 0 0 1 8 13.5C3 13.5 0 8 0 8s.939-1.721 2.641-3.238l.708.709zm10.296 8.884-12-12 .708-.708 12 12z"/></svg>';
                    btn.style.cursor = 'pointer';
                    // İlk durumu simgele: şifre gizliyse eye-slash
                    btn.innerHTML = input.type === 'password' ? EYE_SLASH : EYE;
                    btn.addEventListener('click', function(){
                        const show = input.type === 'password';
                        input.type = show ? 'text' : 'password';
                        btn.innerHTML = show ? EYE : EYE_SLASH;
                    });
                })();
                
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
    

    # Cookie kontrolü (artık devre dışı)
    admin_user, admin_hash = get_admin()
    token = request.cookies.get('stats_auth')
    expected = generate_token(admin_hash) if admin_hash else None
    if False and token != expected:
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
                /* Edge/IE varsayılan şifre gözünü gizle */
                input[type="password"]::-ms-reveal,
                input[type="password"]::-ms-clear { display: none; }
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
                        <input type="hidden" name="login_attempt" value="1" />
                        <div class="input-group">
                            <input type="text" name="username" id="username" placeholder="Kullanıcı Adı" required autocomplete="username">
                        </div>
                                                <div class="input-group">
                                                        <input type="password" name="password" id="password" placeholder="Şifre" required autocomplete="current-password">
                                                        <span class="toggle-password" aria-hidden="true">
                                                                <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" fill="currentColor" viewBox="0 0 16 16">
                                                                    <path d="M16 8s-3-5.5-8-5.5S0 8 0 8s3 5.5 8 5.5S16 8 16 8M1.173 8a13 13 0 0 1 1.66-2.043C4.12 4.668 5.88 3.5 8 3.5s3.879 1.168 5.168 2.457A13 13 0 0 1 14.828 8q-.086.13-.195.288c-.335.48-.83 1.12-1.465 1.755C11.879 11.332 10.119 12.5 8 12.5s-3.879-1.168-5.168-2.457A13 13 0 0 1 1.172 8z"/>
                                                                    <path d="M8 5.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5M4.5 8a3.5 3.5 0 1 1 7 0 3.5 3.5 0 0 1-7 0"/>
                                                                </svg>
                                                        </span>
                                                </div>
                        <button type="submit" class="submit-btn" id="submitBtn">
                            <span class="btn-text">Giriş</span>
                            <div class="spinner"></div>
                        </button>
                    </form>
                    <button class="change-toggle" id="changeToggle" style="margin-top:18px;background:none;border:none;color:#667eea;cursor:pointer;font-weight:600">Bilgileri Değiştir ▾</button>
                    <div id="changePanel" style="display:none;margin-top:15px;animation:fadeInUp 0.4s ease-out">
                        <form method="post" action="/stats/update-credentials" id="changeForm">
                            <div class="input-group">
                                <input type="password" name="current_password" placeholder="Mevcut Şifre" required autocomplete="current-password">
                            </div>
                            <div class="input-group">
                                <input type="text" name="new_username" placeholder="Yeni Kullanıcı Adı (opsiyonel)" autocomplete="username">
                            </div>
                            <div class="input-group">
                                <input type="password" name="new_password" placeholder="Yeni Şifre (opsiyonel)" autocomplete="new-password">
                            </div>
                            <div class="input-group">
                                <input type="password" name="new_password_repeat" placeholder="Yeni Şifre Tekrar" autocomplete="new-password">
                            </div>
                            <button type="submit" class="submit-btn" style="margin-top:5px">
                                <span class="btn-text">Kaydet</span>
                                <div class="spinner"></div>
                            </button>
                            <div style="font-size:0.75em;color:#666;margin-top:6px">En az 8 karakter, harf + rakam önerilir.</div>
                        </form>
                    </div>
                </div>
            </div>
            <script>
                // Basit göz togglesı: şifre görünür/gizli durumunu değiştirir.
                (function(){
                    const btn = document.querySelector('.toggle-password');
                    const input = document.getElementById('password');
                    if (!btn || !input) return;
                    const EYE = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" fill="currentColor" viewBox="0 0 16 16"><path d="M16 8s-3-5.5-8-5.5S0 8 0 8s3 5.5 8 5.5S16 8 16 8M1.173 8a13 13 0 0 1 1.66-2.043C4.12 4.668 5.88 3.5 8 3.5s3.879 1.168 5.168 2.457A13 13 0 0 1 14.828 8q-.086.13-.195.288c-.335.48-.83 1.12-1.465 1.755C11.879 11.332 10.119 12.5 8 12.5s-3.879-1.168-5.168-2.457A13 13 0 0 1 1.172 8z"/><path d="M8 5.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5M4.5 8a3.5 3.5 0 1 1 7 0 3.5 3.5 0 0 1-7 0"/></svg>';
                    const EYE_SLASH = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" fill="currentColor" viewBox="0 0 16 16"><path d="M13.359 11.238C15.06 9.72 16 8 16 8s-3-5.5-8-5.5a7 7 0 0 0-2.79.588l.77.771A6 6 0 0 1 8 3.5c2.12 0 3.879 1.168 5.168 2.457A13 13 0 0 1 14.828 8q-.086.13-.195.288c-.335.48-.83 1.12-1.465 1.755q-.247.248-.517.486z"/><path d="M11.297 9.176a3.5 3.5 0 0 0-4.474-4.474l.823.823a2.5 2.5 0 0 1 2.829 2.829zm-2.943 1.299.822.822a3.5 3.5 0 0 1-4.474-4.474l.823.823a2.5 2.5 0 0 0 2.829 2.829"/><path d="M3.35 5.47q-.27.24-.518.487A13 13 0 0 0 1.172 8l.195.288c.335.48.83 1.12 1.465 1.755C4.121 11.332 5.881 12.5 8 12.5c.716 0 1.39-.133 2.02-.36l.77.772A7 7 0 0 1 8 13.5C3 13.5 0 8 0 8s.939-1.721 2.641-3.238l.708.709zm10.296 8.884-12-12 .708-.708 12 12z"/></svg>';
                    btn.style.cursor = 'pointer';
                    // İlk durumu simgele: şifre gizliyse eye-slash
                    btn.innerHTML = input.type === 'password' ? EYE_SLASH : EYE;
                    btn.addEventListener('click', function(){
                        const show = input.type === 'password';
                        input.type = show ? 'text' : 'password';
                        btn.innerHTML = show ? EYE : EYE_SLASH;
                    });
                })();
                
                const loginBox = document.getElementById('loginBox');
                const passwordInput = document.getElementById('password');
                
                let userInteracted = false;
                ['touchstart','mousedown','click'].forEach(ev => {
                    window.addEventListener(ev, () => { userInteracted = true; }, { once: true });
                });
                passwordInput.addEventListener('focus', function() {
                    if (!userInteracted) return;
                    setTimeout(function() {
                        loginBox.classList.add('keyboard-open');
                        passwordInput.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    }, 50);
                });
                
                passwordInput.addEventListener('blur', function() {
                    loginBox.classList.remove('keyboard-open');
                });
                
                document.getElementById('loginForm').addEventListener('submit', function() {
                    document.getElementById('submitBtn').classList.add('loading');
                });
                const changeToggle = document.getElementById('changeToggle');
                const changePanel = document.getElementById('changePanel');
                changeToggle.addEventListener('click', ()=>{
                    const open = changePanel.style.display === 'block';
                    changePanel.style.display = open ? 'none' : 'block';
                    changeToggle.textContent = open ? 'Bilgileri Değiştir ▾' : 'Bilgileri Gizle ▴';
                });
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
            
            # Saatlik dağılım kaldırıldı (kullanıcı talebi)
            
            # Son 20 ziyaret
            recent = conn.execute(
                "SELECT timestamp, ip, path FROM visits ORDER BY id DESC LIMIT 20"
            ).fetchall()
            # Ders listesi (yönetim) - varsayılan + DB birleşik gösterim
            sub_rows = conn.execute("SELECT id, name FROM subjects ORDER BY name COLLATE NOCASE").fetchall()
            default_pool = ['Mathematik','Deutsch','HSU','Englisch','Ethik','Religion','Musik']
            db_map = { (r['name'] or '').strip().lower(): r['id'] for r in sub_rows }
            merged_names = []
            seen = set()
            for nm in (default_pool + [r['name'] for r in sub_rows]):
                if not nm:
                    continue
                key = nm.strip()
                if not key:
                    continue
                lk = key.lower()
                if lk not in seen:
                    seen.add(lk)
                    merged_names.append(key)
            merged_names.sort(key=lambda s: s.lower())
            # Stats listesinde göstermek için HTML öğelerini hazırla
            items_html_parts = []
            for name in merged_names:
                key_l = (name or '').strip().lower()
                sid = db_map.get(key_l)
                if sid is not None:
                    items_html_parts.append(
                        f"<li style='display:flex;align-items:center;justify-content:space-between;padding:12px 14px;border:1px solid var(--border-color);border-radius:10px;margin:8px 0;background:linear-gradient(135deg, var(--bg-lighter), rgba(102, 126, 234, 0.02));transition:all 0.2s ease;'>"
                        f"<span style='font-weight:600;color:var(--text-secondary)'>{name}</span>"
                        f"<form method='post' action='/stats/subjects/delete' style='margin:0'>"
                        f"<input type='hidden' name='subject_id' value='{sid}'/>"
                        f"<button type='submit' style='background:linear-gradient(135deg, #dc3545, #c82333);color:#fff;border:none;padding:8px 12px;border-radius:8px;cursor:pointer;font-weight:600;transition:all 0.2s ease;box-shadow:0 2px 4px rgba(220, 53, 69, 0.2)' onmouseover=\"this.style.boxShadow='0 4px 12px rgba(220, 53, 69, 0.35)';this.style.transform='translateY(-2px)'\" onmouseout=\"this.style.boxShadow='0 2px 4px rgba(220, 53, 69, 0.2)';this.style.transform='translateY(0)'\">Sil</button>"
                        f"</form>"
                        f"</li>"
                    )
                else:
                    items_html_parts.append(
                        f"<li style='display:flex;align-items:center;justify-content:space-between;padding:12px 14px;border:1px solid var(--border-color);border-radius:10px;margin:8px 0;background:linear-gradient(135deg, var(--bg-lighter), rgba(102, 126, 234, 0.02));'>"
                        f"<span style='font-weight:600;color:var(--text-secondary)'>{name}</span>"
                        f"<span class='small' style='color:var(--text-muted);background:linear-gradient(135deg, rgba(102, 126, 234, 0.1), rgba(118, 75, 162, 0.1));border:1px solid rgba(102, 126, 234, 0.2);border-radius:8px;padding:6px 10px;font-weight:600'>Varsayılan</span>"
                        f"</li>"
                    )
            items_html = "".join(items_html_parts)
            
            html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Stats</title>
                <style>
                    * {{ box-sizing: border-box; }}
                    :root {{
                        --primary: #667eea;
                        --primary-dark: #5568d3;
                        --accent: #764ba2;
                        --success: #28a745;
                        --danger: #dc3545;
                        --warning: #ffc107;
                        --bg-light: #f8f9fa;
                        --bg-lighter: #ffffff;
                        --text-primary: #1a1a1a;
                        --text-secondary: #555555;
                        --text-muted: #999999;
                        --border-color: #eeeeee;
                        --shadow-sm: 0 2px 4px rgba(0,0,0,0.08);
                        --shadow-md: 0 4px 12px rgba(0,0,0,0.12);
                        --shadow-lg: 0 8px 24px rgba(0,0,0,0.15);
                    }}
                    body {{ 
                        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
                        padding: 0; 
                        max-width: 1200px; 
                        margin: 0 auto; 
                        background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
                        min-height: 100vh;
                        position: relative;
                        color: var(--text-primary);
                        overflow-x: hidden;
                        width: 100%;
                    }}
                    html {{
                        overflow-x: hidden;
                        max-width: 100%;
                    }}
                    /* Premium Toolbar */
                    .toolbar {{
                        position: sticky;
                        top: 0;
                        z-index: 1000;
                        background: var(--bg-lighter);
                        display: flex;
                        align-items: center;
                        justify-content: space-between;
                        padding: 16px 20px;
                        box-shadow: var(--shadow-md);
                        border-bottom: 1px solid var(--border-color);
                        backdrop-filter: blur(10px);
                        animation: slideDown 0.3s ease-out;
                    }}
                    @keyframes slideDown {{
                        from {{ transform: translateY(-100%); opacity: 0; }}
                        to {{ transform: translateY(0); opacity: 1; }}
                    }}
                    .toolbar-title {{
                        font-weight: 700;
                        color: var(--text-secondary);
                        display: flex;
                        align-items: center;
                        gap: 10px;
                        font-size: 1.25em;
                        font-family: system-ui, -apple-system, 'Segoe UI', sans-serif;
                    }}
                    .toolbar-title::before {{
                        content: '📈';
                        font-size: 1.3em;
                        display: inline-block;
                        line-height: 1;
                    }}
                    }}
                    .kebab {{
                        position: relative;
                    }}
                    .kebab-btn {{
                        background: none;
                        border: none;
                        font-size: 28px;
                        line-height: 1;
                        padding: 8px 12px;
                        cursor: pointer;
                        color: var(--text-secondary);
                        touch-action: manipulation;
                        -webkit-tap-highlight-color: transparent;
                        transition: all 0.2s ease;
                        border-radius: 8px;
                    }}
                    .kebab-btn:hover {{
                        background: var(--bg-light);
                        color: var(--primary);
                    }}
                    .kebab-btn:active {{
                        background: var(--border-color);
                        transform: scale(0.95);
                    }}
                    .menu {{
                        position: absolute;
                        right: 0;
                        top: 50px;
                        background: var(--bg-lighter);
                        border: 1px solid var(--border-color);
                        border-radius: 12px;
                        box-shadow: var(--shadow-lg);
                        min-width: 200px;
                        display: none;
                        overflow: hidden;
                        animation: popIn 0.25s ease-out;
                    }}
                    @keyframes popIn {{
                        from {{ transform: scale(0.9) translateY(-10px); opacity: 0; }}
                        to {{ transform: scale(1) translateY(0); opacity: 1; }}
                    }}
                    .menu.open {{ display: block; }}
                    .menu a {{
                        display: flex;
                        align-items: center;
                        gap: 10px;
                        padding: 14px 16px;
                        text-decoration: none;
                        color: var(--text-secondary);
                        font-weight: 600;
                        border-bottom: 1px solid var(--border-color);
                        font-size: 0.95em;
                        transition: all 0.15s ease;
                    }}
                    .menu a:last-child {{ border-bottom: none; }}
                    .menu a:hover {{ 
                        background: var(--bg-light);
                        color: var(--primary);
                        padding-left: 20px;
                    }}
                    .menu a:active {{ background: linear-gradient(135deg, rgba(102, 126, 234, 0.1), rgba(118, 75, 162, 0.1)); }}
                    .menu .danger {{ color: var(--danger); }}
                    .menu .danger:hover {{ background: rgba(220, 53, 69, 0.1); }}
                    .content {{ position: relative; z-index: 1; padding: 20px; }}
                    h1 {{ 
                        color: var(--text-primary); 
                        font-size: 1.6em; 
                        margin: 0 0 20px 0; 
                        position: relative; 
                        z-index: 1;
                        font-weight: 700;
                    }}
                    h2 {{ 
                        color: var(--text-secondary); 
                        margin-top: 28px; 
                        margin-bottom: 12px;
                        font-size: 1.2em; 
                        position: relative; 
                        z-index: 1;
                        font-weight: 600;
                    }}
                    .chart-container {{ 
                        background: var(--bg-lighter); 
                        padding: 20px; 
                        border-radius: 12px; 
                        box-shadow: var(--shadow-md); 
                        margin: 20px 0; 
                        overflow-x: auto; 
                        position: relative; 
                        z-index: 1; 
                        -webkit-overflow-scrolling: touch;
                        border: 1px solid var(--border-color);
                    }}
                    .chart-scroll {{ min-width: 600px; }}
                    table {{ 
                        width: 100%; 
                        border-collapse: collapse; 
                        margin-top: 15px; 
                        background: var(--bg-lighter); 
                        border-radius: 10px; 
                        overflow: hidden; 
                        box-shadow: var(--shadow-sm); 
                        font-size: 0.9em; 
                        position: relative; 
                        z-index: 1;
                        border: 1px solid var(--border-color);
                    }}
                    th, td {{ 
                        padding: 12px 10px; 
                        text-align: left; 
                        border-bottom: 1px solid var(--border-color); 
                    }}
                    th {{ 
                        background: linear-gradient(135deg, var(--primary), var(--accent));
                        color: white;
                        font-weight: 700; 
                        position: sticky; 
                        top: 0;
                    }}
                    .small {{ 
                        font-size: 0.8em; 
                        color: var(--text-muted); 
                    }}
                    tr:hover {{ 
                        background: linear-gradient(90deg, rgba(102, 126, 234, 0.05), transparent);
                    }}
                    .table-container {{ 
                        overflow-x: auto; 
                        -webkit-overflow-scrolling: touch; 
                        margin-bottom: 120px; 
                        position: relative; 
                        z-index: 1;
                        border-radius: 10px;
                    }}
                    .card {{ 
                        background: var(--bg-lighter); 
                        padding: 20px; 
                        border-radius: 12px; 
                        box-shadow: var(--shadow-md); 
                        margin: 16px 0; 
                        position: relative; 
                        z-index: 1;
                        border: 1px solid var(--border-color);
                        transition: all 0.3s ease;
                        animation: fadeInUp 0.4s ease-out;
                    }}
                    @keyframes fadeInUp {{
                        from {{ transform: translateY(20px); opacity: 0; }}
                        to {{ transform: translateY(0); opacity: 1; }}
                    }}
                    .card:hover {{ 
                        box-shadow: var(--shadow-lg);
                        transform: translateY(-2px);
                    }}
                    .row-flex {{ display: flex; gap: 16px; flex-wrap: wrap; }}
                    .row-flex .col {{ flex: 1 1 320px; min-width: 100%; }}
                    .input-inline {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }}
                    .input-inline input[type=text] {{ 
                        flex: 1; 
                        min-width: 150px; 
                        padding: 12px 14px; 
                        border: 2px solid var(--border-color); 
                        border-radius: 10px; 
                        font-size: 0.95em;
                        background: var(--bg-lighter);
                        color: var(--text-primary);
                        transition: all 0.2s ease;
                    }}
                    .input-inline input[type=text]:focus {{
                        outline: none;
                        border-color: var(--primary);
                        box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
                    }}
                    .input-inline button {{ 
                        padding: 12px 20px; 
                        border: none; 
                        border-radius: 10px; 
                        background: linear-gradient(135deg, var(--primary), var(--accent));
                        color: white; 
                        font-weight: 700; 
                        cursor: pointer; 
                        white-space: nowrap;
                        transition: all 0.2s ease;
                        box-shadow: var(--shadow-sm);
                    }}
                    .input-inline button:hover {{ 
                        box-shadow: var(--shadow-md);
                        transform: translateY(-2px);
                    }}
                    .input-inline button:active {{ 
                        transform: translateY(0);
                    }}
                    ul.clean {{ list-style: none; padding: 0; margin: 0; }}
                    .stat {{ 
                        background: linear-gradient(135deg, var(--bg-lighter), rgba(102, 126, 234, 0.02)); 
                        padding: 20px; 
                        border-radius: 12px; 
                        box-shadow: var(--shadow-md); 
                        margin: 14px 0; 
                        display: flex; 
                        justify-content: space-between; 
                        align-items: center; 
                        position: relative; 
                        z-index: 1;
                        border: 1px solid var(--border-color);
                        transition: all 0.3s ease;
                        animation: slideIn 0.4s ease-out;
                    }}
                    @keyframes slideIn {{
                        from {{ transform: translateX(-20px); opacity: 0; }}
                        to {{ transform: translateX(0); opacity: 1; }}
                    }}
                    .stat:hover {{
                        box-shadow: var(--shadow-lg);
                        transform: translateX(8px);
                    }}
                    .stat-label {{ 
                        font-weight: 600; 
                        color: var(--text-secondary); 
                        font-size: 0.95em;
                    }}
                    .stat-value {{ 
                        font-size: 2em; 
                        font-weight: 800; 
                        background: linear-gradient(135deg, var(--primary), var(--accent));
                        -webkit-background-clip: text;
                        -webkit-text-fill-color: transparent;
                        background-clip: text;
                    }}
                    h1, h2 {{ color: var(--text-primary); margin-top: 25px; position: relative; z-index: 1; }}
                    h3 {{ margin: 0 0 12px 0; font-size: 1.1em; color: var(--text-secondary); font-weight: 600; }}
                    @media (max-width: 768px) {{
                        .toolbar-title {{ font-size: 1.1em; }}
                        .content {{ padding: 16px; }}
                        h1 {{ font-size: 1.3em; margin: 0 0 16px 0; }}
                        h2 {{ font-size: 1.1em; margin-top: 20px; }}
                        .stat {{ padding: 16px; margin: 12px 0; gap: 12px; }}
                        .stat-label {{ font-size: 0.9em; }}
                        .stat-value {{ font-size: 1.6em; }}
                        .stat-label, .stat-value {{ white-space: nowrap; }}
                        .card {{ padding: 16px; margin: 12px 0; }}
                        .row-flex {{ gap: 12px; }}
                        .row-flex .col {{ flex: 1 1 100%; min-width: 100%; }}
                        table {{ font-size: 0.85em; }}
                        th, td {{ padding: 10px 8px; }}
                        .input-inline {{ gap: 8px; }}
                        .input-inline input[type=text] {{ padding: 12px; font-size: 16px; }}
                        .input-inline button {{ padding: 12px 16px; font-size: 0.95em; }}
                    }}
                    @media (max-width: 480px) {{
                        body {{ padding: 0; }}
                        .toolbar {{ padding: 12px 14px; gap: 10px; }}
                        .toolbar-title {{ font-size: 1em; }}
                        .kebab-btn {{ font-size: 24px; padding: 6px 8px; }}
                        .content {{ padding: 12px; }}
                        h1 {{ font-size: 1.15em; margin: 0 0 14px 0; }}
                        h2 {{ font-size: 1em; margin-top: 16px; margin-bottom: 10px; }}
                        h3 {{ font-size: 1em; }}
                        .stat {{ padding: 14px; margin: 10px 0; gap: 10px; flex-direction: column; align-items: flex-start; }}
                        .stat-label {{ font-size: 0.9em; }}
                        .stat-value {{ font-size: 1.5em; align-self: flex-end; }}
                        .card {{ padding: 14px; margin: 10px 0; }}
                        .row-flex {{ gap: 0; flex-direction: column; }}
                        .row-flex .col {{ min-width: 100%; }}
                        table {{ font-size: 0.8em; }}
                        th, td {{ padding: 8px 6px; }}
                        .small {{ font-size: 0.7em; }}
                        .input-inline {{ gap: 6px; flex-direction: column; }}
                        .input-inline input[type=text] {{ width: 100%; padding: 12px; font-size: 16px; }}
                        .input-inline button {{ width: 100%; padding: 12px; font-size: 0.95em; }}
                        .menu {{ min-width: 160px; font-size: 0.9em; }}
                        .menu a {{ padding: 12px 14px; }}
                    }}
                </style>
            </head>
            <body>
                <div class="toolbar">
                    <div class="toolbar-title">Ziyaretçi İstatistikleri</div>
                    <div class="kebab">
                        <button class="kebab-btn" id="kebabBtn" aria-label="Menü">⋮</button>
                        <div class="menu" id="kebabMenu">
                            <a href="/">⌂ Ana Sayfa</a>
                            <a href="/send-report">@ Mail</a>
                            <a href="/stats/delete-past">✕ Sil</a>
                            <a href="/stats/logout" class="danger">⎋ Çıkış</a>
                        </div>
                    </div>
                </div>
                <div class="content">
                <h1>Genel Bakış</h1>
                <div class="stat"><span class="stat-label">Toplam Ziyaret</span><span class="stat-value">{total}</span></div>
                <div class="stat"><span class="stat-label">Bugün</span><span class="stat-value">{today}</span></div>
                <div class="stat"><span class="stat-label">Son 7 Gün</span><span class="stat-value">{last_7_days}</span></div>
                <div class="stat"><span class="stat-label">Benzersiz IP</span><span class="stat-value">{unique_ips}</span></div>
                
                
                
                <h2>📚 Ders Havuzu</h2>
                <div class="card">
                    <div class="row-flex">
                        <div class="col">
                            <h3 style="margin:0 0 8px 0;font-size:1.05em;color:#555">Yeni Ders Ekle</h3>
                            <form method="post" action="/stats/subjects/add" class="input-inline">
                                <input type="text" name="subject_name" placeholder="Örn: Biologie" maxlength="64" required>
                                <button type="submit">Ekle</button>
                            </form>
                            <div class="small" style="margin-top:6px;color:#666">Eklenen dersler, anasayfadaki ekleme diyalogunda görünecek.</div>
                        </div>
                        <div class="col">
                            <h3 style="margin:0 0 8px 0;font-size:1.05em;color:#555">Mevcut Dersler</h3>
                            <ul class="clean">{items_html or "<li style='color:#666'>Henüz ders eklenmemiş.</li>"}</ul>
                        </div>
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
                </div>
                <script>
                    (function(){
                        const btn = document.getElementById('kebabBtn');
                        const menu = document.getElementById('kebabMenu');
                        function close(){ menu.classList.remove('open'); }
                        btn.addEventListener('click', function(e){
                            e.stopPropagation();
                            menu.classList.toggle('open');
                        });
                        document.addEventListener('click', close);
                        window.addEventListener('resize', close);
                    })();
                </script>
            </body>
            </html>
            """
            return html
    except Exception as e:
        return f"Error: {e}", 500

# Basit tarayıcılar 401 kodlu sayfaları boş gösterebileceği için
# yukarıda tanımlanan `stats_login` rotası aynı formu 200 OK ile sunar.

@login_required
@app.route('/stats/update-credentials', methods=['POST'])
def update_credentials():
    return redirect(url_for('stats'))

# --- Admin Reset (env token korumalı) ---
@app.route('/admin/reset', methods=['POST'])
def admin_reset():
    token_env = os.getenv('ADMIN_RESET_TOKEN')
    if not token_env:
        return "Reset kapalı (ADMIN_RESET_TOKEN yok)", 403
    token = (request.form.get('token') or '').strip()
    if token != token_env:
        return "Yetkisiz", 403
    new_username = (request.form.get('new_username') or '').strip()
    new_password = (request.form.get('new_password') or '').strip()
    if not new_username or not new_password:
        return "Eksik bilgi", 400
    import re
    if not re.fullmatch(r'[A-Za-z0-9_]{3,32}', new_username):
        return "Geçersiz kullanıcı adı", 400
    pwd_hash = generate_password_hash(new_password, method='pbkdf2:sha256', salt_length=16)
    with get_db_connection() as conn:
        row = conn.execute("SELECT id FROM admin_credentials LIMIT 1").fetchone()
        if row:
            conn.execute("UPDATE admin_credentials SET username=?, password_hash=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                         (new_username, pwd_hash, row['id']))
        else:
            conn.execute("INSERT INTO admin_credentials (username, password_hash) VALUES (?, ?)", (new_username, pwd_hash))
        conn.commit()
    return "Reset OK. /stats sayfasında giriş yapabilirsiniz.", 200

# --- Admin Bootstrap (ilk kurulumda token gerektirmez) ---
@app.route('/admin/bootstrap', methods=['POST'])
def admin_bootstrap():
    """Sadece admin_credentials boşsa çalışır; ilk kurulum için.
    new_username ve new_password zorunlu. Varsa 403 döner.
    """
    with get_db_connection() as conn:
        row = conn.execute("SELECT id FROM admin_credentials LIMIT 1").fetchone()
        if row:
            return "Zaten admin tanımlı", 403
    new_username = (request.form.get('new_username') or '').strip()
    new_password = (request.form.get('new_password') or '').strip()
    if not new_username or not new_password:
        return "Eksik bilgi", 400
    import re
    if not re.fullmatch(r'[A-Za-z0-9_]{3,32}', new_username):
        return "Geçersiz kullanıcı adı", 400
    pwd_hash = generate_password_hash(new_password, method='pbkdf2:sha256', salt_length=16)
    with get_db_connection() as conn:
        conn.execute("INSERT INTO admin_credentials (username, password_hash) VALUES (?, ?)", (new_username, pwd_hash))
        conn.commit()
    return "Bootstrap OK. /stats ile giriş yapabilirsiniz.", 200

# --- Admin Info (env token korumalı, sadece username ve güncelleme zamanı) ---
@app.route('/admin/info', methods=['GET'])
def admin_info():
    token_env = os.getenv('ADMIN_INFO_TOKEN') or os.getenv('ADMIN_RESET_TOKEN')
    if not token_env:
        return "Info kapalı (ADMIN_INFO_TOKEN yok)", 403
    token = (request.args.get('token') or request.form.get('token') or '').strip()
    if token != token_env:
        return "Yetkisiz", 403
    with get_db_connection() as conn:
        row = conn.execute("SELECT username, updated_at FROM admin_credentials LIMIT 1").fetchone()
    username = row['username'] if row else None
    updated = row['updated_at'] if row else None
    return jsonify({
        "username": username,
        "updated_at": updated,
        "password_visible": False,
        "note": "Şifre hash'li tutulur; görüntülenemez. /admin/reset ile güncelleyebilirsiniz."
    })

@login_required
@app.route('/stats/subjects/add', methods=['POST'])
def stats_subjects_add():
    name = (request.form.get('subject_name') or '').strip()
    if not name:
        return redirect(url_for('stats'))
    # Basit doğrulama: uzunluk ve tehlikeli karakterleri filtrele
    if len(name) > 64:
        name = name[:64]
    try:
        with get_db_connection() as conn:
            conn.execute("INSERT OR IGNORE INTO subjects(name) VALUES (?)", (name,))
            conn.commit()
    except Exception:
        pass
    return redirect(url_for('stats'))

@login_required
@app.route('/stats/subjects/delete', methods=['POST'])
def stats_subjects_delete():
    sid = (request.form.get('subject_id') or '').strip()
    if not sid:
        return redirect(url_for('stats'))
    try:
        with get_db_connection() as conn:
            conn.execute("DELETE FROM subjects WHERE id = ?", (sid,))
            conn.commit()
    except Exception:
        pass
    return redirect(url_for('stats'))

# -------------------- Stats JSON Endpoint --------------------
@login_required
@app.route('/stats/json')
def stats_json():
    """İstemci uygulaması için JSON formatında istatistikler (aynı auth cookie)."""
    try:
        with get_db_connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM visits").fetchone()[0]
            today = conn.execute("SELECT COUNT(*) FROM visits WHERE DATE(timestamp) = DATE('now')").fetchone()[0]
            last_7_days = conn.execute("SELECT COUNT(*) FROM visits WHERE timestamp >= datetime('now', '-7 days')").fetchone()[0]
            unique_ips = conn.execute("SELECT COUNT(DISTINCT ip) FROM visits").fetchone()[0]
            recent = conn.execute("SELECT timestamp, ip, path FROM visits ORDER BY id DESC LIMIT 20").fetchall()
        return jsonify({
            'total': total,
            'today': today,
            'last_7_days': last_7_days,
            'unique_ips': unique_ips,
            'recent': [ {'timestamp': r[0], 'ip': r[1], 'path': r[2]} for r in recent ]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

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
    return redirect(url_for('stats'))

# -------------------- Local çalıştırma --------------------
if __name__ == "__main__":
    init_db()
    print("🚀 Starting Flask (dev)")
    # Ortamdan PORT değişkeni okunarak esnek port seçimi
    try:
        _port = int(os.getenv("PORT", "5000"))
    except Exception:
        _port = 5000
    # Reloader'ı kapatmak bazı yerel ortamlarda bağlantı istikrarını artırır
    app.run(debug=True, host="0.0.0.0", port=_port, use_reloader=False)

# -------------------- Jinja2 filtre --------------------
@app.template_filter('strftime')
def _jinja2_filter_datetime(date_string, format='%d.%m.%Y'):
    from datetime import datetime
    try:
        return datetime.strptime(date_string, '%Y-%m-%d').strftime(format)
    except Exception:
        return date_string
