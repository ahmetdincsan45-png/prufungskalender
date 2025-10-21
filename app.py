import os
import sqlite3
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, url_for, make_response
from flask_cors import CORS
import csv
import io

app = Flask(__name__)
CORS(app)

DATABASE = 'exams.db'

def init_db():
    """Veritabanını ilk kez başlatır ve tabloyu oluşturur."""
    try:
        with sqlite3.connect(DATABASE) as conn:
            # Tablo var mı kontrol et
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='exams'")
            if cursor.fetchone() is None:
                # Tablo yoksa oluştur
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS exams (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        subject TEXT NOT NULL,
                        grade TEXT NOT NULL,
                        date TEXT NOT NULL,
                        start_time TEXT NOT NULL,
                        end_time TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                ''')
                conn.commit()
                print("✅ Exams tablosu oluşturuldu!")
            else:
                print("✅ Exams tablosu zaten mevcut!")
    except Exception as e:
        print(f"❌ Veritabanı hatası: {e}")
        # Yedek çözüm - doğrudan tablo oluştur
        with sqlite3.connect(DATABASE) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS exams (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subject TEXT NOT NULL,
                    grade TEXT NOT NULL,
                    date TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            ''')
            conn.commit()
            print("✅ Yedek tablo oluşturma başarılı!")

def get_db_connection():
    """Veritabanı bağlantısı döndürür."""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def index():
    """Ana sayfa - FullCalendar takvimi."""
    try:
        # Veritabanının hazır olduğundan emin ol
        init_db()
        
        # Gelecek sınavı bul
        today = datetime.now().strftime('%Y-%m-%d')
        
        conn = get_db_connection()
        # Tablo var mı kontrol et
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='exams'")
        if cursor.fetchone() is None:
            conn.close()
            return render_template('index.html', next_exam=None)
            
        next_exam = conn.execute(
            'SELECT * FROM exams WHERE date >= ? ORDER BY date, start_time LIMIT 1',
            (today,)
        ).fetchone()
        conn.close()
        
        return render_template('index.html', next_exam=next_exam)
    except Exception as e:
        print(f"Index hatası: {e}")
        # Hata durumunda basit sayfa döndür
        return render_template('index.html', next_exam=None)

@app.route('/events')
def events():
    """Takvim etkinlikleri JSON formatında döndürür."""
    try:
        conn = get_db_connection()
        # Tablo var mı kontrol et
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='exams'")
        if cursor.fetchone() is None:
            # Tablo yoksa boş array döndür
            conn.close()
            return jsonify([])
            
        exams = conn.execute(
            'SELECT * FROM exams ORDER BY date, start_time'
        ).fetchall()
        conn.close()
    except Exception as e:
        print(f"Events hatası: {e}")
        return jsonify([])
    
    events_list = []
    for exam in exams:
        events_list.append({
            'id': exam['id'],
            'title': f"{exam['subject']} ({exam['grade']})",
            'start': f"{exam['date']}T{exam['start_time']}",
            'end': f"{exam['date']}T{exam['end_time']}",
            'backgroundColor': '#007bff',
            'borderColor': '#007bff',
            'extendedProps': {
                'subject': exam['subject'],
                'grade': exam['grade'],
                'date': exam['date'],
                'start_time': exam['start_time'],
                'end_time': exam['end_time']
            }
        })
    
    return jsonify(events_list)

@app.route('/add', methods=['GET', 'POST'])
def add_exam():
    """Sınav ekleme sayfası."""
    if request.method == 'POST':
        try:
            # Veritabanının hazır olduğundan emin ol
            init_db()
            
            subject = request.form.get('subject', '').strip()
            grade = '4A'  # Sabit değer
            date = request.form.get('date', '').strip()
            start_time = '08:00'  # Sabit başlangıç saati
            end_time = request.form.get('end_time', '').strip()
            
            # Einfache Validierung
            if not all([subject, date, end_time]):
                return render_template('add.html', error='Bitte füllen Sie alle Felder aus.')
            
            # Veritabanına ekle
            conn = get_db_connection()
            conn.execute(
                'INSERT INTO exams (subject, grade, date, start_time, end_time, created_at) VALUES (?, ?, ?, ?, ?, ?)',
                (subject, grade, date, start_time, end_time, datetime.now().isoformat())
            )
            conn.commit()
            conn.close()
            
            return redirect(url_for('index'))
            
        except Exception as e:
            print(f"Add exam hatası: {e}")
            return render_template('add.html', error=f'Fehler beim Hinzufügen: {str(e)}')
    
    return render_template('add.html')

@app.route('/delete', methods=['GET', 'POST'])
def delete_exam():
    """Sınav silme sayfası."""
    try:
        # Veritabanının hazır olduğundan emin ol
        init_db()
        
        if request.method == 'POST':
            exam_id = request.form.get('exam_id', '').strip()
            
            if not exam_id:
                conn = get_db_connection()
                exams = conn.execute('SELECT * FROM exams ORDER BY date, start_time').fetchall()
                conn.close()
                return render_template('delete.html', exams=exams, error='Bitte wählen Sie eine Prüfung aus.')
            
            # Sınavı sil
            conn = get_db_connection()
            conn.execute('DELETE FROM exams WHERE id = ?', (exam_id,))
            conn.commit()
            conn.close()
            
            # Başarı mesajıyla sayfayı yenile
            conn = get_db_connection()
            exams = conn.execute('SELECT * FROM exams ORDER BY date, start_time').fetchall()
            conn.close()
            return render_template('delete.html', exams=exams, message='Prüfung wurde erfolgreich gelöscht.')
        
        # GET request - tüm sınavları listele
        conn = get_db_connection()
        exams = conn.execute('SELECT * FROM exams ORDER BY date, start_time').fetchall()
        conn.close()
        return render_template('delete.html', exams=exams)
        
    except Exception as e:
        print(f"Delete exam hatası: {e}")
        return render_template('delete.html', exams=[], error=f'Fehler: {str(e)}')

if __name__ == '__main__':
    try:
        init_db()
        print("✅ Veritabanı hazır!")
        app.run(debug=True, host='0.0.0.0', port=5000)
    except Exception as e:
        print(f"❌ Başlatma hatası: {e}")
        # Basit sürüm çalıştır
        simple_app = Flask(__name__)
        
        @simple_app.route('/')
        def simple_home():
            return '<h1>🔧 Basit Mod Aktif</h1><p>Ana uygulama hatası var, basit mod çalışıyor.</p>'
            
        simple_app.run(debug=True, host='0.0.0.0', port=5000)