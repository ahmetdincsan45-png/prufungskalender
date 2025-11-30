# Desktop Client (Prüfungskalender)

Bu klasör, mevcut Render üzerindeki Flask takvim API'sine bağlanan hafif bir masaüstü istemci örneğidir. Amaç: Web sitesine zarar vermeden `.exe` oluşturabilmek.

## Mantık
- Veritabanı ve asıl uygulama Render sunucusunda kalır.
- `client.py` sadece HTTP üzerinden `/events` ve `/add` endpoint'lerini kullanır.
- `.exe` üretmek için PyInstaller kullanabilirsiniz.

## Dosyalar
- `config.json`: Sunucunun taban URL'si (`API_BASE`). Bunu kendi canlı URL'inizle değiştirin.
- `client.py`: Listeleme ve sınav ekleme komutları.

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
- `/add` şu an form POST beklediği için JSON değil `data=` ile gönderiyoruz.
- Auth gerektiren admin/statistik endpoint'lerini eklemek istersen cookie yönetimi gerekir.
- Otomatik güncelleme istersen daha sonra bir küçük versiyon kontrol endpoint'i ekleyebilirsin.

## Güvenlik
- `config.json` içindeki URL düz metin. Gizli bilgi (şifre hash vs.) koyma.
- Şifreli işlemler sadece web üzerinden login ile kalsın; istemciye gömme.

## Genişletme Fikirleri
- `update` komutu: Lokal versiyon numarasını kontrol edip yeni sürüm indir.
- `stats` komutu: `/stats` HTML'ini çekip temel metrikleri sadeleştirerek konsola yaz.
- Gerçek zamanlı yenileme: `list` sonrası belirli aralıkla tekrar çağıran bir mod.

İhtiyacın olursa sonraki adımları birlikte ekleyebiliriz.
