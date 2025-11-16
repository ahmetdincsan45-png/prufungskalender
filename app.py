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

@app.route("/stats")
def stats():
    """Gizli istatistik sayfası - şifreyle korumalı"""
    # Basit şifre kontrolü (query parameter ile)
    password = request.args.get('p', '')
    if password != '45ee551':  # İstersen bu şifreyi değiştirebilirsin
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
                    height: 100vh; 
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    overflow: hidden;
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
            <div class="login-box">
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
                document.getElementById('loginForm').addEventListener('submit', function() {
                    document.getElementById('submitBtn').classList.add('loading');
                });
            </script>
        </body>
        </html>
        """, 401
    
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
                    .table-container {{ overflow-x: auto; -webkit-overflow-scrolling: touch; }}
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
