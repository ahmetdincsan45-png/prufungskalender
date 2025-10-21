import os
import psycopg2
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_cors import CORS
from urllib.parse import urlparse

app = Flask(__name__)
CORS(app)

# PostgreSQL bağlantı URL'si (Render.com otomatik sağlayacak)
DATABASE_URL = os.getenv('DATABASE_URL')

# Debug bilgileri
print("🚀 Starting Prüfungskalender application...")
print(f"📊 Database URL present: {bool(DATABASE_URL)}")
if DATABASE_URL:
    print(f"🔗 Database type: PostgreSQL")
else:
    print(f"🔗 Database type: SQLite (fallback)")

def get_db_connection():
    """PostgreSQL veritabanı bağlantısı."""
    try:
        # Yerel geliştirme için SQLite fallback
        if not DATABASE_URL:
            print("🔧 Using SQLite fallback for local development")
            import sqlite3
            conn = sqlite3.connect('exams.db')
            conn.row_factory = sqlite3.Row
            return conn
        
        # Production için PostgreSQL
        print(f"🔗 Connecting to PostgreSQL: {DATABASE_URL[:20]}...")
        
        # SSL gerektiren Render.com için
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        return conn
    except ImportError as e:
        print(f"❌ Import error: {e}")
        # psycopg2 yoksa SQLite fallback
        try:
            import sqlite3
            print("🔄 Falling back to SQLite")
            conn = sqlite3.connect('exams.db')
            conn.row_factory = sqlite3.Row
            return conn
        except Exception as e2:
            print(f"❌ SQLite fallback failed: {e2}")
            return None
    except Exception as e:
        print(f"❌ Database connection error: {e}")
        # Hata durumunda SQLite fallback dene
        try:
            import sqlite3
            print("🔄 Emergency fallback to SQLite")
            conn = sqlite3.connect('exams.db')
            conn.row_factory = sqlite3.Row
            return conn
        except Exception as e2:
            print(f"❌ Emergency fallback failed: {e2}")
            return None

def init_db():
    """Veritabanı ve tabloyu oluştur."""
    try:
        print("🔧 Initializing database...")
        conn = get_db_connection()
        if not conn:
            print("❌ Could not establish database connection")
            return False
            
        cursor = conn.cursor()
        
        # PostgreSQL ve SQLite için uyumlu tablo oluşturma
        if DATABASE_URL:  # PostgreSQL
            print("📊 Creating PostgreSQL table...")
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS exams (
                    id SERIAL PRIMARY KEY,
                    subject VARCHAR(255) NOT NULL,
                    grade VARCHAR(50) NOT NULL,
                    date DATE NOT NULL,
                    start_time TIME NOT NULL,
                    end_time TIME NOT NULL,
                    created_at TIMESTAMP NOT NULL
                )
            ''')
        else:  # SQLite fallback
            print("📊 Creating SQLite table...")
            cursor.execute('''
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
        cursor.close()
        conn.close()
        print("✅ Database initialized successfully!")
        return True
    except Exception as e:
        print(f"❌ Database initialization error: {e}")
        print(f"❌ Error type: {type(e).__name__}")
        import traceback
        traceback.print_exc()
        return False

@app.route('/')
def index():
    """Ana sayfa."""
    try:
        init_db()
        conn = get_db_connection()
        if not conn:
            return render_template('index.html', next_exam=None)
            
        cursor = conn.cursor()
        today = datetime.now().strftime('%Y-%m-%d')
        
        if DATABASE_URL:  # PostgreSQL
            cursor.execute(
                'SELECT * FROM exams WHERE date >= %s ORDER BY date LIMIT 1',
                (today,)
            )
        else:  # SQLite
            cursor.execute(
                'SELECT * FROM exams WHERE date >= ? ORDER BY date LIMIT 1',
                (today,)
            )
            
        next_exam = cursor.fetchone()
        
        # Sonucu dict'e çevir
        if next_exam:
            columns = [desc[0] for desc in cursor.description]
            next_exam = dict(zip(columns, next_exam))
            
        cursor.close()
        conn.close()
        
        return render_template('index.html', next_exam=next_exam)
    except Exception as e:
        print(f"❌ Index error: {e}")
        return render_template('index.html', next_exam=None)

@app.route('/events')
def events():
    """JSON etkinlikler."""
    try:
        init_db()
        conn = get_db_connection()
        if not conn:
            return jsonify([])
            
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM exams ORDER BY date')
        exams = cursor.fetchall()
        
        # Sütun adlarını al
        if DATABASE_URL:  # PostgreSQL
            columns = [desc[0] for desc in cursor.description]
        else:  # SQLite
            columns = ['id', 'subject', 'grade', 'date', 'start_time', 'end_time', 'created_at']
        
        cursor.close()
        conn.close()
        
        events_list = []
        for exam in exams:
            if DATABASE_URL:  # PostgreSQL
                exam_dict = dict(zip(columns, exam))
            else:  # SQLite
                exam_dict = dict(exam) if hasattr(exam, 'keys') else dict(zip(columns, exam))
                
            events_list.append({
                'id': exam_dict['id'],
                'title': exam_dict['subject'],
                'start': f"{exam_dict['date']}T{exam_dict['start_time']}",
                'end': f"{exam_dict['date']}T{exam_dict['end_time']}",
                'backgroundColor': '#007bff',
                'borderColor': '#007bff'
            })
        
        return jsonify(events_list)
    except Exception as e:
        print(f"❌ Events error: {e}")
        return jsonify([])

@app.route('/add', methods=['GET', 'POST'])
def add_exam():
    """Sınav ekle."""
    if request.method == 'POST':
        try:
            init_db()
            
            subject = request.form.get('subject', '').strip()
            date = request.form.get('date', '').strip()
            
            if not subject or not date:
                return render_template('add.html', error='Bitte alle Felder ausfüllen!')
            
            # Sabit değerler
            grade = '4A'
            start_time = '08:00'
            end_time = '16:00'
            created_at = datetime.now()
            
            conn = get_db_connection()
            if not conn:
                return render_template('add.html', error='Database connection failed!')
                
            cursor = conn.cursor()
            
            if DATABASE_URL:  # PostgreSQL
                cursor.execute(
                    'INSERT INTO exams (subject, grade, date, start_time, end_time, created_at) VALUES (%s, %s, %s, %s, %s, %s)',
                    (subject, grade, date, start_time, end_time, created_at)
                )
            else:  # SQLite
                cursor.execute(
                    'INSERT INTO exams (subject, grade, date, start_time, end_time, created_at) VALUES (?, ?, ?, ?, ?, ?)',
                    (subject, grade, date, start_time, end_time, created_at.isoformat())
                )
            
            conn.commit()
            cursor.close()
            conn.close()
            
            return redirect(url_for('index'))
            
        except Exception as e:
            print(f"❌ Add exam error: {e}")
            return render_template('add.html', error=f'Fehler: {str(e)}')
    
    return render_template('add.html')

@app.route('/delete', methods=['GET', 'POST'])
def delete_exam():
    """Sınav sil."""
    try:
        init_db()
        
        if request.method == 'POST':
            exam_id = request.form.get('exam_id', '').strip()
            
            if exam_id:
                conn = get_db_connection()
                if conn:
                    cursor = conn.cursor()
                    
                    if DATABASE_URL:  # PostgreSQL
                        cursor.execute('DELETE FROM exams WHERE id = %s', (exam_id,))
                    else:  # SQLite
                        cursor.execute('DELETE FROM exams WHERE id = ?', (exam_id,))
                    
                    conn.commit()
                    cursor.close()
                    conn.close()
                
                return redirect(url_for('delete_exam'))
        
        # Tüm sınavları listele
        conn = get_db_connection()
        if not conn:
            return render_template('delete.html', exams=[], error="Database connection failed")
            
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM exams ORDER BY date')
        exams = cursor.fetchall()
        
        # Sütun adlarını al ve dict'e çevir
        columns = [desc[0] for desc in cursor.description]
        exams_list = []
        for exam in exams:
            exam_dict = dict(zip(columns, exam))
            exams_list.append(exam_dict)
        
        cursor.close()
        conn.close()
        
        return render_template('delete.html', exams=exams_list)
        
    except Exception as e:
        print(f"❌ Delete exam error: {e}")
        return render_template('delete.html', exams=[], error=str(e))

if __name__ == '__main__':
    try:
        print("🎯 Initializing database on startup...")
        init_success = init_db()
        if not init_success:
            print("⚠️ Database initialization failed, but app will continue")
        
        print("🚀 Starting Flask application...")
        app.run(debug=True, host='0.0.0.0', port=5000)
    except Exception as e:
        print(f"❌ Application startup error: {e}")
        import traceback
        traceback.print_exc()

# Render.com için de database init dene
try:
    init_db()
except Exception as e:
    print(f"⚠️ Initial database setup failed: {e}")