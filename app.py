import os
import sqlite3
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_cors import CORS

# PostgreSQL bağlantısı öncelik, SQLite fallback
try:
    import psycopg2
    import psycopg2.extras
    POSTGRESQL_AVAILABLE = True
    print("✅ PostgreSQL module available: True")
except ImportError:
    POSTGRESQL_AVAILABLE = False
    print("❌ PostgreSQL module available: False")

DATABASE_DIR = '/opt/render/project/src'
SQLITE_DATABASE = os.path.join(DATABASE_DIR, 'exams.db')

app = Flask(__name__)
CORS(app)

# Debug bilgileri
print("🚀 Starting Prüfungskalender application...")
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith('postgresql://') and POSTGRESQL_AVAILABLE:
    print(f"🔗 Database type: PostgreSQL (Supabase)")
    print(f"📡 Database URL: {DATABASE_URL[:50]}...")
    print("✅ Data will persist PERMANENTLY on Supabase")
else:
    print(f"🔗 Database type: SQLite FALLBACK")
    print(f"📁 SQLite location: {SQLITE_DATABASE}")
    print("⚠️ FALLBACK: Using SQLite (data may be temporary)")

def get_db_connection():
    """Get database connection - PostgreSQL preferred, SQLite fallback."""
    DATABASE_URL = os.environ.get('DATABASE_URL')
    
    # Try PostgreSQL first (Supabase)
    if DATABASE_URL and DATABASE_URL.startswith('postgresql://') and POSTGRESQL_AVAILABLE:
        try:
            print(f"🔗 Connecting to PostgreSQL (Supabase)...")
            conn = psycopg2.connect(DATABASE_URL)
            return conn, 'postgresql'
        except Exception as e:
            print(f"❌ PostgreSQL connection failed: {e}")
            print("🔄 Falling back to SQLite...")
    
    # Fallback to SQLite
    try:
        print(f"📱 Using SQLite database: {SQLITE_DATABASE}")
        
        # Ensure directory exists
        os.makedirs(DATABASE_DIR, exist_ok=True)
        
        conn = sqlite3.connect(SQLITE_DATABASE)
        conn.row_factory = sqlite3.Row
        return conn, 'sqlite'
    except Exception as e:
        print(f"❌ SQLite connection failed: {e}")
        return None, None

def init_db():
    """Initialize database and create tables."""
    try:
        print("🔧 Initializing database...")
        
        conn, db_type = get_db_connection()
        if not conn:
            print("❌ Could not establish database connection")
            return False
        
        if db_type == 'postgresql':
            print("� Creating PostgreSQL table (PERMANENT)...")
            cursor = conn.cursor()
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
            conn.commit()
            cursor.close()
            print("✅ PostgreSQL database initialized successfully! (PERMANENT)")
        else:
            print("📊 Creating SQLite table (TEMPORARY)...")
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
            print("⚠️ SQLite database initialized (TEMPORARY - will be deleted)")
        
        conn.close()
        return True
    except Exception as e:
        print(f"❌ Database initialization error: {e}")
        import traceback
        traceback.print_exc()
        return False

@app.route('/')
def index():
    """Ana sayfa."""
    try:
        init_db()
        conn, db_type = get_db_connection()
        if not conn:
            return render_template('index.html', next_exam=None)
            
        cursor = conn.cursor()
        today = datetime.now().strftime('%Y-%m-%d')
        
        if db_type == 'postgresql':
            cursor.execute(
                'SELECT * FROM exams WHERE date >= %s ORDER BY date LIMIT 1',
                (today,)
            )
        else:
            cursor.execute(
                'SELECT * FROM exams WHERE date >= ? ORDER BY date LIMIT 1',
                (today,)
            )
            
        next_exam = cursor.fetchone()
        
        # Sonucu dict'e çevir
        if next_exam:
            if db_type == 'postgresql':
                next_exam = dict(next_exam)
            else:
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
        
        exams = conn.execute('SELECT * FROM exams ORDER BY date').fetchall()
        events_list = []
        for exam in exams:
            events_list.append({
                'id': exam['id'],
                'title': exam['subject'],
                'start': f"{exam['date']}T{exam['start_time']}",
                'end': f"{exam['date']}T{exam['end_time']}",
                'backgroundColor': '#007bff',
                'borderColor': '#007bff'
            })
        
        conn.close()
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