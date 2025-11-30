# Desktop Client (Prüfungskalender)

Bu klasör, mevcut Render üzerindeki Flask takvim API'sine bağlanan hafif bir masaüstü istemci örneğidir. Amaç: Web sitesine zarar vermeden `.exe` oluşturabilmek.

## Mantık
- Veritabanı ve asıl uygulama Render sunucusunda kalır.
- `client.py` sadece HTTP üzerinden `/events` ve `/add` endpoint'lerini kullanır.
- `.exe` üretmek için PyInstaller kullanabilirsiniz.

## Dosyalar
- `config.json`: Sunucunun taban URL'si (`API_BASE`). Bunu kendi canlı URL'inizle değiştirin.
- `client.py`: Listeleme, ekleme, login, stats ve canlı (polling) komutları.

## Kurulum (Windows Örnek)
```powershell
python -m venv venv
venv\Scripts\activate
pip install --upgrade pip
pip install requests pyinstaller
```

## Kullanım (Kaynak Kod Halinde)
```powershell
python desktop_client\client.py list
python desktop_client\client.py add MATEMATIK 2025-12-18
python desktop_client\client.py add "MATEMATIK,FIZIK" 2025-12-18
python desktop_client\client.py login Ahmet 45ee551
python desktop_client\client.py stats
python desktop_client\client.py stats_live 15   # 15 sn'de bir yenile
python desktop_client\client.py events_live 30  # 30 sn'de bir sınav listesi
```

## .exe Üretme
```powershell
pyinstaller --onefile desktop_client\client.py
# Oluşan dosya: dist\client.exe
# Test:
dist\client.exe list
dist\client.exe add MATEMATIK 2025-12-18
```

## Notlar
- `/add` form POST beklediği için JSON değil `data=`.
- Login sonrası `stats_auth` cookie'si kaydedilir (`.session_cookie.json`).
- `stats` ve `stats_live` komutları `/stats/json` endpoint'ini kullanır (HTML parse gerekmez).
- Canlı modlar Ctrl+C ile durdurulur.
- Otomatik güncelleme / versiyon kontrolü ileride eklenebilir.

## Güvenlik
- `config.json` içindeki URL düz metin. Gizli bilgi (şifre hash vs.) koyma.
- Şifreli işlemler sadece web üzerinden login ile kalsın; istemciye gömme.

## Genişletme Fikirleri
- `update` komutu: Yeni sürüm kontrolü + otomatik indirme.
- WebSocket/SSE: Polling yerine anlık push için.
- Log dosyası: Canlı mod çıktısını `client_live.log` içine yazma.
- Komut kısayolları: `sl` (stats_live), `el` (events_live) gibi alias'lar.

İhtiyacın olursa sonraki adımları birlikte ekleyebiliriz.
