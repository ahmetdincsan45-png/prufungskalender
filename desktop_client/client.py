import requests, json, sys
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.json"
if not CONFIG_PATH.exists():
    print("config.json bulunamadı.")
    sys.exit(1)

cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
BASE = cfg.get("API_BASE", "").rstrip('/')
if not BASE:
    print("API_BASE config.json içinde tanımlı olmalı.")
    sys.exit(1)


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
    else:
        usage()
