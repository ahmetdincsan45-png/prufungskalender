import os
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_cors import CORS
import psycopg2
import psycopg2.extras

app = Flask(__name__)
CORS(app)

def get_db_connection():
    """Get PostgreSQL database connection (Supabase only)."""
    DATABASE_URL = os.environ.get('DATABASE_URL')
    
    if not DATABASE_URL or not DATABASE_URL.startswith('postgresql://'):
        raise Exception("❌ DATABASE_URL not found! Supabase PostgreSQL required.")
    
    try:
        print("🔗 Connecting to Supabase PostgreSQL...")
        conn = psycopg2.connect(DATABASE_URL)
        conn.cursor_factory = psycopg2.extras.RealDictCursor
        return conn
    except Exception as e:
        raise Exception(f"❌ Supabase PostgreSQL connection failed: {e}")

def init_db():
    """Initialize Supabase PostgreSQL database and create tables."""
    try:
        conn = get_db_connection()
        
        print("🗄️ Creating Supabase PostgreSQL table (PERMANENT)")
        cursor = conn.cursor()
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
        
        conn.commit()
        cursor.close()
        conn.close()
        print("✅ Supabase PostgreSQL database initialized (PERMANENT)")
        return True
        
    except Exception as e:
        print(f"❌ Database initialization failed: {e}")
        raise e

@app.route('/')
def index():
    """Ana sayfa."""
    try:
        init_db()
        conn = get_db_connection()
        
        cursor = conn.cursor()
        today = datetime.now().strftime('%Y-%m-%d')
        
        cursor.execute(
            'SELECT * FROM exams WHERE date >= %s ORDER BY date LIMIT 1',
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
        conn = get_db_connection()
        
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
            
            conn = get_db_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                'INSERT INTO exams (subject, date) VALUES (%s, %s)',
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
                conn = get_db_connection()
                cursor = conn.cursor()
                
                cursor.execute('DELETE FROM exams WHERE id = %s', (exam_id,))
                
                conn.commit()
                cursor.close()
                conn.close()
            
            return redirect(url_for('delete_exam'))
        
        # Tüm sınavları listele
        conn = get_db_connection()
        
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
        print("🎯 Initializing Supabase PostgreSQL database...")
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
    print("✅ Render.com Supabase database initialization successful!")
except Exception as e:
    print(f"❌ Render.com database initialization failed: {e}")