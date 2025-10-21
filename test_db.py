import sqlite3
import os

# Veritabanı dosyası var mı kontrol et
if os.path.exists('exams.db'):
    print("✅ exams.db dosyası mevcut")
    
    conn = sqlite3.connect('exams.db')
    
    # Tabloları listele
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
    print("📋 Tablolar:", [table[0] for table in tables])
    
    # exams tablosunda veri var mı kontrol et
    if tables:
        try:
            exams = conn.execute("SELECT COUNT(*) FROM exams").fetchone()
            print(f"📊 Toplam sınav sayısı: {exams[0]}")
            
            # Son 3 sınavı göster
            recent_exams = conn.execute("SELECT * FROM exams ORDER BY created_at DESC LIMIT 3").fetchall()
            print("🔍 Son eklenen sınavlar:")
            for exam in recent_exams:
                print(f"  - {exam[1]} ({exam[2]}) - {exam[3]}")
                
        except Exception as e:
            print(f"❌ Veri okuma hatası: {e}")
    
    conn.close()
else:
    print("❌ exams.db dosyası bulunamadı")