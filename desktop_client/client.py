import requests, json, sys, time, os
from pathlib import Path

def load_config():
    # Öncelik: ENV -> çalışma dizini config.json -> script dizini config.json
    env_base = os.getenv('API_BASE', '').strip()
    if env_base:
        return env_base.rstrip('/')
    cwd_cfg = Path.cwd() / 'config.json'
    if cwd_cfg.exists():
        try:
            data = json.loads(cwd_cfg.read_text(encoding='utf-8'))
            base = data.get('API_BASE','').strip()
            if base:
                return base.rstrip('/')
        except Exception:
            pass
    script_cfg = Path(__file__).parent / 'config.json'
    if script_cfg.exists():
        try:
            data = json.loads(script_cfg.read_text(encoding='utf-8'))
            base = data.get('API_BASE','').strip()
            if base:
                return base.rstrip('/')
        except Exception:
            pass
    return ''

BASE = load_config()
if not BASE:
    print("Uyarı: API_BASE bulunamadı. Çoğu komut çalışmayacak. ENV veya config.json ekleyin.")


session = requests.Session()

COOKIE_FILE = Path(__file__).parent / ".session_cookie.json"

def save_cookies():
    data = { 'cookies': session.cookies.get_dict() }
    COOKIE_FILE.write_text(json.dumps(data), encoding='utf-8')

def load_cookies():
    if COOKIE_FILE.exists():
        try:
            data = json.loads(COOKIE_FILE.read_text(encoding='utf-8'))
            for k,v in data.get('cookies', {}).items():
                session.cookies.set(k,v)
        except Exception:
            pass

load_cookies()

def list_events():
    """Sunucudaki tüm sınavları listeler (/events endpoint'i FullCalendar formatında)."""
    try:
        r = requests.get(f"{BASE}/events", timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print("Hata (listeleme):", e)
        return []


def add_exam(subjects: str, date: str):
    def login(username: str, password: str):
        """Stats için giriş yap ve auth cookie sakla."""
        try:
            payload = {
                'login_attempt': '1',
                'username': username,
                'password': password
            }
            r = session.post(f"{BASE}/stats", data=payload, timeout=10)
            if r.status_code == 200 and 'stats_auth' in session.cookies.get_dict():
                save_cookies()
                print("✔ Giriş başarılı. Cookie kaydedildi.")
            else:
                print("❌ Giriş başarısız (status)", r.status_code)
        except Exception as e:
            print("Hata (login):", e)

    def fetch_stats():
        """JSON stats endpoint'inden verileri çek."""
        try:
            r = session.get(f"{BASE}/stats/json", timeout=10)
            if r.status_code == 200:
                return r.json()
            else:
                print("❌ Yetkisiz veya hata:", r.status_code)
                return None
        except Exception as e:
            print("Hata (stats):", e)
            return None

    def print_stats(data):
        if not data:
            print("Veri yok")
            return
        print(f"Toplam: {data.get('total')} | Bugün: {data.get('today')} | 7 Gün: {data.get('last_7_days')} | IP: {data.get('unique_ips')}")

    def stats_live(interval: int):
        print(f"Gerçek zamanlı stats (her {interval} sn) için Ctrl+C ile çıkış.")
        while True:
            data = fetch_stats()
            ts = time.strftime('%H:%M:%S')
            print(f"[{ts}] ", end='')
            print_stats(data)
            time.sleep(interval)

    def events_live(interval: int):
        print(f"Gerçek zamanlı events (her {interval} sn) için Ctrl+C ile çıkış.")
        while True:
            evs = list_events()
            ts = time.strftime('%H:%M:%S')
            print(f"[{ts}] Toplam sınav: {len(evs)}")
            time.sleep(interval)
    """Sunucuya yeni sınav(lar) ekler. subjects: virgülle ayrılmış ders listesi, date: YYYY-MM-DD."""
    data = {
        "subjects": subjects,
        "date": date
    }
    try:
        # Flask form POST bekliyor, JSON değil. Bu yüzden data= kullanıyoruz.
        r = requests.post(f"{BASE}/add", data=data, timeout=10, allow_redirects=False)
        if r.status_code in (302, 200):
            print("✔ Sınav(lar) eklendi.")
        else:
            print("❌ Beklenmeyen durum:", r.status_code, r.text[:200])
    except Exception as e:
        print("Hata (ekleme):", e)


def usage():
    print("Kullanım:")
    print("  client.py list")
    print("  client.py add MATEMATIK 2025-12-18")
    print("  client.py add 'MATEMATIK,FIZIK' 2025-12-18")
    print("  client.py login USER PASS")
    print("  client.py stats")
    print("  client.py stats_live 15")
    print("  client.py events_live 30")
    print("ENV override: API_BASE=... python client.py list")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        usage()
        sys.exit(0)
    cmd = sys.argv[1]
    if cmd == "list":
        events = list_events()
        for ev in events:
            # FullCalendar event formatında: id, title, start
            print(f"{ev.get('id')} | {ev.get('title')} | {ev.get('start')}")
    elif cmd == "add" and len(sys.argv) >= 4:
        subjects = sys.argv[2]
        date = sys.argv[3]
        add_exam(subjects, date)
    elif cmd == "login" and len(sys.argv) >= 4:
        login(sys.argv[2], sys.argv[3])
    elif cmd == "stats":
        data = fetch_stats()
        print_stats(data)
    elif cmd == "stats_live":
        interval = int(sys.argv[2]) if len(sys.argv) >= 3 else 15
        stats_live(interval)
    elif cmd == "events_live":
        interval = int(sys.argv[2]) if len(sys.argv) >= 3 else 30
        events_live(interval)
    else:
        usage()
