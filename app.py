import os
import sqlite3
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_cors import CORS

# PostgreSQL import with fallback
try:
    import psycopg2
    import psycopg2.extras
    POSTGRESQL_AVAILABLE = True
    print("✅ PostgreSQL module available")
except ImportError:
    POSTGRESQL_AVAILABLE = False
    print("❌ PostgreSQL not available - will use SQLite fallback")

app = Flask(__name__)
CORS(app)

def get_db_connection():
    """Get database connection - PostgreSQL preferred, SQLite fallback."""
    DATABASE_URL = os.environ.get('DATABASE_URL')
    
    # Try PostgreSQL first (Supabase)
    if DATABASE_URL and DATABASE_URL.startswith('postgresql://') and POSTGRESQL_AVAILABLE:
        try:
            print("🔗 Connecting to PostgreSQL (Supabase)...")
            conn = psycopg2.connect(DATABASE_URL)
            conn.cursor_factory = psycopg2.extras.RealDictCursor
            return conn, 'postgresql'
        except Exception as e:
            print(f"❌ PostgreSQL failed: {e}")
            print("🔄 Falling back to SQLite...")
    
    # Fallback to SQLite
    try:
        print("📱 Using SQLite database")
        conn = sqlite3.connect('/tmp/exams.db')
        conn.row_factory = sqlite3.Row
        return conn, 'sqlite'
    except Exception as e:
        print(f"❌ SQLite failed: {e}")
        return None, None

def init_db():
    """Initialize database and create tables."""
    try:
        conn, db_type = get_db_connection()
        if not conn:
            return False
        
        cursor = conn.cursor()
        
        if db_type == 'postgresql':
            print("🗄️ Creating PostgreSQL table (PERMANENT)")
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS exams (
                    id SERIAL PRIMARY KEY,
                    subject VARCHAR(255) NOT NULL,
                    grade VARCHAR(50) NOT NULL DEFAULT '4A',
                    date DATE NOT NULL,
                    start_time TIME NOT NULL DEFAULT '08:00',
                    end_time TIME NOT NULL DEFAULT '16:00',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
        else:
            print("📊 Creating SQLite table (TEMPORARY)")
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS exams (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subject TEXT NOT NULL,
                    grade TEXT NOT NULL DEFAULT '4A',
                    date TEXT NOT NULL,
                    start_time TEXT NOT NULL DEFAULT '08:00',
                    end_time TEXT NOT NULL DEFAULT '16:00',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
        
        conn.commit()
        cursor.close()
        conn.close()
        print(f"✅ Database initialized ({db_type})")
        return True
        
    except Exception as e:
        print(f"❌ Database initialization failed: {e}")
        return False

@app.route('/')
def index():
    """Ana sayfa."""
    try:
        try:
            init_db()
            conn, db_type = get_db_connection()
            
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
        conn, db_type = get_db_connection()
        
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM exams ORDER BY date')
        exams = cursor.fetchall()
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
        
        cursor.close()
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
            subject = request.form.get('subject', '').strip()
            date = request.form.get('date', '').strip()
            
            if not subject or not date:
                return render_template('add.html', error='Bitte alle Felder ausfüllen!')
            
            conn, db_type = get_db_connection()
            cursor = conn.cursor()
            
            if db_type == 'postgresql':
                cursor.execute(
                    'INSERT INTO exams (subject, date) VALUES (%s, %s)',
                    (subject, date)
                )
            else:
                cursor.execute(
                    'INSERT INTO exams (subject, date) VALUES (?, ?)',
                    (subject, date)
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
        if request.method == 'POST':
            exam_id = request.form.get('exam_id', '').strip()
            
            if exam_id:
                conn, db_type = get_db_connection()
                cursor = conn.cursor()
                
                if db_type == 'postgresql':
                    cursor.execute('DELETE FROM exams WHERE id = %s', (exam_id,))
                else:
                    cursor.execute('DELETE FROM exams WHERE id = ?', (exam_id,))
                
                conn.commit()
                cursor.close()
                conn.close()
            
            return redirect(url_for('delete_exam'))
        
        # Tüm sınavları listele
        conn, db_type = get_db_connection()
        
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM exams ORDER BY date')
        exams = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        return render_template('delete.html', exams=exams)
        
    except Exception as e:
        print(f"❌ Delete exam error: {e}")
        return render_template('delete.html', exams=[], error=str(e))

if __name__ == '__main__':
    try:
        print("🎯 Initializing database on startup...")
        init_db()
        print("✅ Database initialized successfully!")
        
        print("🚀 Starting Flask application...")
        app.run(debug=True, host='0.0.0.0', port=5000)
    except Exception as e:
        print(f"❌ Application startup error: {e}")
        exit(1)

# Render.com için database init
try:
    init_db()
    print("✅ Render.com database initialization successful!")
except Exception as e:
    print(f"❌ Render.com database initialization failed: {e}")