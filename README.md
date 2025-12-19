# Sınav Takvimi - Flask + SQLite + FullCalendar

Bu proje, sınıf sınavlarını yönetmek için Flask, SQLite ve FullCalendar kullanılarak geliştirilmiş bir web uygulamasıdır.

## ⚠️ Güvenlik Uyarısı
Bu sistemde kişisel veriler (tam isim, öğrenci numarası vb.) girmeyin. Sadece ders ve sınıf bilgilerini kullanın.

## Özellikler

- **Ana Sayfa (/)**: Türkçe FullCalendar takvimi
  - Sınav Ekle butonu
  - Bugün butonu
  - CSV Dışa Aktar butonu  
  - Sınıf Filtresi (4A/4B/Hepsi)
  
- **Etkinlikler (/events)**: JSON formatında sınav verileri (sınıf filtreleme destekli)

- **Sınav Ekleme (/add)**: GET/POST metodlarıyla yeni sınav ekleme
  - Ders adı
  - Sınıf (4A/4B)
  - Tarih
  - Başlangıç ve bitiş saatleri

- **CSV Dışa Aktarma (/export.csv)**: UTF-8 formatında CSV indirme
  - Kolonlar: date, start_time, end_time, grade, subject

## Veritabanı

- **Veritabanı**: SQLite (`exams.db`)
- **Tablo**: `exams`
  - `id`: Otomatik artan birincil anahtar
  - `subject`: Ders adı (TEXT)
  - `grade`: Sınıf (TEXT)
  - `date`: Tarih (TEXT)
  - `start_time`: Başlangıç saati (TEXT)
  - `end_time`: Bitiş saati (TEXT)
  - `created_at`: Oluşturulma zamanı (TEXT)

## Kurulum

### 1. Gereksinimler

Python 3.11.9 veya üzeri

### 2. Sanal Ortam Oluşturma (Önerilen)

```bash
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux/Mac
```

### 3. Bağımlılıkları Yükleme

```bash
pip install -r requirements.txt
```

### 4. Çalıştırma

```bash
python app.py
```

Uygulama http://localhost:5000 adresinde çalışmaya başlayacaktır.

## Kullanım

1. **İlk Çalıştırma**: Uygulama otomatik olarak `schema.sql` dosyasını kullanarak veritabanını oluşturacaktır.

2. **Sınav Ekleme**: 
   - Ana sayfadan "Sınav Ekle" butonuna tıklayın
   - Gerekli bilgileri doldurun
   - "Sınav Ekle" butonuna tıklayın

3. **Takvimdeki Sınavları Görüntüleme**:
   - Ana sayfadaki takvimde sınavlar renkli kutular halinde görünür
   - 4A sınıfı mavi, 4B sınıfı yeşil renktedir
   - Sınava tıklayarak detaylarını görebilirsiniz

4. **Filtreleme**:
   - Sınıf filtresini kullanarak sadece belirli bir sınıfın sınavlarını görüntüleyebilirsiniz

5. **CSV Dışa Aktarma**:
   - "CSV Dışa Aktar" butonuna tıklayarak tüm sınavları CSV formatında indirebilirsiniz

## Teknolojiler

- **Backend**: Flask (Python)
- **Veritabanı**: SQLite
- **Frontend**: Bootstrap 5, FullCalendar 6
- **Lokalizasyon**: Türkçe (tr)
- **Takvim**: Pazartesi ile başlar (firstDay=1)

## Dosya Yapısı

```
takvım/
├── app.py              # Ana Flask uygulaması
├── schema.sql          # Veritabanı şeması
├── requirements.txt    # Python bağımlılıkları
├── render.yaml        # Render blueprint (otomatik deploy için)
├── README.md          # Bu dosya
└── templates/
    ├── index.html     # Ana sayfa şablonu
    └── add.html       # Sınav ekleme şablonu
  ├── render.yaml        # Render blueprint (otomatik deploy için)
```

## Güvenlik Notları
## Deploy (Render)

- Render hesabı açın (kişisel hesap yeterlidir).
- “New +” → “Blueprint” → repo’yu seçin. Alternatif: “Web Service” ve GitHub repo bağlama.
- Disk eklenmiş servis otomatik oluşur (render.yaml):
  - Build: `pip install -r requirements.txt`
  - Start: `gunicorn app:app`
  - Disk: `/var/data` (kalıcı SQLite)
  - Env: `SQLITE_DB_PATH=/var/data/prufungskalender.db`
- Otomatik deploy açık: GitHub’a push → Render yeniden yayınlar.

Not: Heroku üye olamıyorsanız Render/ Railway uygun alternatiflerdir.

- Bu uygulama eğitim/demo amaçlıdır
- Kişisel verileri saklamayın
- Üretim ortamında güvenlik önlemleri alın
- Veritabanı yedeklemelerini düzenli yapın

## Lisans

Bu proje eğitim amaçlıdır ve MIT lisansı altında sunulmaktadır.