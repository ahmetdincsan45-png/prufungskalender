from flask import send_from_directory
import os
import sqlite3
import threading
from pathlib import Path
from datetime import datetime, timedelta
try:
    from zoneinfo import ZoneInfo
except Exception:  # Python <3.9 veya ortamda yoksa
    ZoneInfo = None
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
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

@app.route("/stats/delete-past", methods=["GET", "POST"])
def stats_delete_past():
    """Geçmiş sınavları yalnız stats yetkisi ile silebilme sayfası"""
    # Stats ile aynı cookie doğrulaması
    def get_admin():
        with get_db_connection() as conn:
            row = conn.execute("SELECT username, password_hash FROM admin_credentials LIMIT 1").fetchone()
        return (row['username'], row['password_hash']) if row else (None, None)
    def generate_token(pwd_hash):
        return hashlib.sha256(f"{pwd_hash}:prufungskalender".encode()).hexdigest()
    admin_user, admin_hash = get_admin()
    expected = generate_token(admin_hash) if admin_hash else None
    token = request.cookies.get('stats_auth')
    if token != expected:
        return redirect(url_for('stats'))
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

@app.route("/stats", methods=["GET", "POST"])
def stats():
    """İstatistikler - username + parola ile korumalı"""
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
        if admin_user and in_user == admin_user and check_password_hash(admin_hash, in_pass):
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
                        <div class="error">❌ Yanlış kullanıcı adı veya şifre!</div>
                        <form method="post" id="loginForm">
                            <input type="hidden" name="login_attempt" value="1" />
                            <div class="input-group">
                                <input type="text" name="username" id="username" placeholder="Kullanıcı Adı" required autocomplete="username">
                            </div>
                            <div class="input-group">
                                <input type="password" name="password" id="password" placeholder="Şifre" required autocomplete="current-password">
                                <button type="button" class="toggle-password" onclick="togglePassword()">👁️</button>
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
    admin_user, admin_hash = get_admin()
    token = request.cookies.get('stats_auth')
    expected = generate_token(admin_hash) if admin_hash else None
    if token != expected:
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
                        <input type="hidden" name="login_attempt" value="1" />
                        <div class="input-group">
                            <input type="text" name="username" id="username" placeholder="Kullanıcı Adı" required autocomplete="username">
                        </div>
                        <div class="input-group">
                            <input type="password" name="password" id="password" placeholder="Şifre" required autocomplete="current-password">
                            <button type="button" class="toggle-password" onclick="togglePassword()">👁️</button>
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
            
            html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Stats</title>
                <style>
                    * {{ box-sizing: border-box; }}
                    body {{ font-family: system-ui, -apple-system, sans-serif; padding: 0; 
                            max-width: 1000px; margin: 0 auto; background: #f5f5f5; }}
                    /* Top Toolbar */
                    .toolbar {{
                        position: sticky;
                        top: 0;
                        z-index: 1000;
                        background: #ffffff;
                        display: flex;
                        align-items: center;
                        justify-content: space-between;
                        padding: 10px 15px;
                        box-shadow: 0 1px 6px rgba(0,0,0,0.08);
                        border-bottom: 1px solid #eee;
                    }}
                    .toolbar-title {{
                        font-weight: 600;
                        color: #333;
                        display: flex;
                        align-items: center;
                        gap: 8px;
                    }}
                    .kebab {{
                        position: relative;
                    }}
                    .kebab-btn {{
                        background: none;
                        border: none;
                        font-size: 22px;
                        line-height: 1;
                        padding: 6px 10px;
                        cursor: pointer;
                        color: #333;
                    }}
                    .menu {{
                        position: absolute;
                        right: 0;
                        top: 36px;
                        background: #fff;
                        border: 1px solid #eee;
                        border-radius: 10px;
                        box-shadow: 0 6px 20px rgba(0,0,0,0.12);
                        min-width: 190px;
                        display: none;
                        overflow: hidden;
                    }}
                    .menu.open {{ display: block; }}
                    .menu a {{
                        display: flex;
                        align-items: center;
                        gap: 8px;
                        padding: 10px 12px;
                        text-decoration: none;
                        color: #333;
                        font-weight: 600;
                        border-bottom: 1px solid #f5f5f5;
                    }}
                    .menu a:last-child {{ border-bottom: none; }}
                    .menu a:hover {{ background: #f8f9fa; }}
                    .menu .danger {{ color: #c82333; }}
                    .content {{ padding: 15px; }}
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
                    .card {{ background:#fff; padding:12px 15px; border-radius:8px; box-shadow:0 1px 3px rgba(0,0,0,.1); margin:12px 0; }}
                    .row-flex {{ display:flex; gap:12px; flex-wrap:wrap; }}
                    .row-flex .col {{ flex:1 1 320px; }}
                    .input-inline {{ display:flex; gap:8px; align-items:center; }}
                    .input-inline input[type=text] {{ flex:1; padding:10px 12px; border:1px solid #ddd; border-radius:8px; font-size:.95em; }}
                    .input-inline button {{ padding:10px 14px; border:none; border-radius:8px; background:#0d6efd; color:#fff; font-weight:600; cursor:pointer; }}
                    .input-inline button:hover {{ background:#0b5ed7; }}
                    ul.clean {{ list-style:none; padding:0; margin:0; }}
                    @media (max-width: 600px) {{
                        .content {{ padding: 10px; }}
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
                <div class="toolbar">
                    <div class="toolbar-title">📊 Ziyaretçi İstatistikleri</div>
                    <div class="kebab">
                        <button class="kebab-btn" id="kebabBtn" aria-label="Menü">⋮</button>
                        <div class="menu" id="kebabMenu">
                            <a href="/send-report">📧 Rapor Gönder</a>
                            <a href="/stats/delete-past">🗑️ Geçmişi Sil</a>
                            <a href="/logout" class="danger">🚪 Çıkış</a>
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
                            <ul class="clean">{"".join([
                                (
                                    (lambda _name: (
                                        (lambda _id: (
                                            f"<li style='display:flex;align-items:center;justify-content:space-between;padding:8px 10px;border:1px solid #eee;border-radius:8px;margin:6px 0;background:#fff'>"
                                            f"<span style='font-weight:600;color:#333'>{_name}</span>"
                                            + (
                                                (f"<form method='post' action='/stats/subjects/delete' style='margin:0'>"
                                                 f"<input type='hidden' name='subject_id' value='{_id}'/>"
                                                 f"<button type='submit' style='background:#dc3545;color:#fff;border:none;padding:6px 10px;border-radius:6px;cursor:pointer'>Sil</button>"
                                                 f"</form>") if _id is not None else
                                                f"<span class='small' style='color:#666;background:#f1f3f5;border:1px solid #e5e7eb;border-radius:6px;padding:4px 8px'>Varsayılan</span>"
                                            )
                                            + f"</li>"
                                        ))(db_map.get(_name.strip().lower()))
                                    )))(name)
                                ) for name in merged_names
                            ])}</ul>
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

@app.route('/stats/update-credentials', methods=['POST'])
def update_credentials():
    with get_db_connection() as conn:
        row = conn.execute("SELECT id, username, password_hash FROM admin_credentials LIMIT 1").fetchone()
    if not row:
        return redirect(url_for('stats'))
    current_password = (request.form.get('current_password') or '').strip()
    new_username = (request.form.get('new_username') or '').strip()
    new_password = (request.form.get('new_password') or '').strip()
    new_repeat = (request.form.get('new_password_repeat') or '').strip()
    if not check_password_hash(row['password_hash'], current_password):
        return """<html><body style='font-family:system-ui;padding:40px'><h3 style='color:#dc3545'>Mevcut şifre hatalı</h3><a href='/stats' style='color:#667eea'>Geri dön</a></body></html>""", 400
    updates = {}
    if new_username:
        import re
        if not re.fullmatch(r'[A-Za-z0-9_]{3,32}', new_username):
            return """<html><body style='font-family:system-ui;padding:40px'><h3 style='color:#dc3545'>Geçersiz kullanıcı adı</h3><p>3-32 karakter; harf, rakam, altçizgi.</p><a href='/stats' style='color:#667eea'>Geri dön</a></body></html>""", 400
        updates['username'] = new_username
    if new_password:
        if len(new_password) < 8:
            return """<html><body style='font-family:system-ui;padding:40px'><h3 style='color:#dc3545'>Şifre çok kısa</h3><p>En az 8 karakter.</p><a href='/stats' style='color:#667eea'>Geri dön</a></body></html>""", 400
        if new_password != new_repeat:
            return """<html><body style='font-family:system-ui;padding:40px'><h3 style='color:#dc3545'>Şifreler uyuşmuyor</h3><a href='/stats' style='color:#667eea'>Geri dön</a></body></html>""", 400
        updates['password_hash'] = generate_password_hash(new_password, method='pbkdf2:sha256', salt_length=16)
    if not updates:
        return """<html><body style='font-family:system-ui;padding:40px'><h3 style='color:#dc3545'>Değişiklik yok</h3><a href='/stats' style='color:#667eea'>Geri dön</a></body></html>""", 400
    set_clause = ', '.join(f"{k}=?" for k in updates.keys()) + ', updated_at=CURRENT_TIMESTAMP'
    vals = list(updates.values()) + [row['id']]
    with get_db_connection() as conn:
        conn.execute(f"UPDATE admin_credentials SET {set_clause} WHERE id=?", vals)
        conn.commit()
        new_row = conn.execute("SELECT password_hash FROM admin_credentials WHERE id=?", (row['id'],)).fetchone()
    new_hash = new_row['password_hash'] if new_row else row['password_hash']
    token = hashlib.sha256(f"{new_hash}:prufungskalender".encode()).hexdigest()
    resp = redirect(url_for('stats'))
    resp.set_cookie('stats_auth', token, max_age=86400, httponly=True)
    return resp

# ---- Subjects management (Stats auth required) ----
def _stats_expected_token():
    with get_db_connection() as conn:
        row = conn.execute("SELECT password_hash FROM admin_credentials LIMIT 1").fetchone()
    if not row:
        return None
    return hashlib.sha256(f"{row['password_hash']}:prufungskalender".encode()).hexdigest()

@app.route('/stats/subjects/add', methods=['POST'])
def stats_subjects_add():
    token = request.cookies.get('stats_auth')
    if token != _stats_expected_token():
        return redirect(url_for('stats'))
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

@app.route('/stats/subjects/delete', methods=['POST'])
def stats_subjects_delete():
    token = request.cookies.get('stats_auth')
    if token != _stats_expected_token():
        return redirect(url_for('stats'))
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
@app.route('/stats/json')
def stats_json():
    """İstemci uygulaması için JSON formatında istatistikler (aynı auth cookie)."""
    def get_admin_hash():
        with get_db_connection() as conn:
            r = conn.execute("SELECT password_hash FROM admin_credentials LIMIT 1").fetchone()
        return r['password_hash'] if r else None
    pwd_hash = get_admin_hash()
    if not pwd_hash:
        return jsonify({'error': 'auth missing'}), 401
    expected = hashlib.sha256(f"{pwd_hash}:prufungskalender".encode()).hexdigest()
    token = request.cookies.get('stats_auth')
    if token != expected:
        return jsonify({'error': 'unauthorized'}), 401
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
    # Cookie kontrolü - stats ile aynı mantık
    try:
        with get_db_connection() as conn:
            row = conn.execute("SELECT password_hash FROM admin_credentials LIMIT 1").fetchone()
        pwd_hash = row['password_hash'] if row else None
        expected_token = hashlib.sha256(f"{pwd_hash}:prufungskalender".encode()).hexdigest() if pwd_hash else None
        auth_token = request.cookies.get('stats_auth')
        if (not expected_token) or (auth_token != expected_token):
            return redirect(url_for('stats'))
    except Exception:
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
