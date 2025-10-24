import os
import sqlite3
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# Database configuration
DATABASE = os.environ.get('DATABASE_URL', 'prufungskalender.db')

def get_db_connection():
    """Get SQLite database connection."""
    # Extract database name from URL or use direct path
    if DATABASE.startswith('sqlite:///'):
        db_path = DATABASE[10:]  # Remove 'sqlite:///'
    else:
        db_path = DATABASE
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initialize SQLite database and create tables."""
    print(f"🗄️ Creating SQLite database: {DATABASE}")
    conn = get_db_connection()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS exams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL,
            grade TEXT NOT NULL DEFAULT '4A',
            date TEXT NOT NULL,
            start_time TEXT NOT NULL DEFAULT '08:00',
            end_time TEXT NOT NULL DEFAULT '16:00',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()
    print("✅ SQLite database initialized")

@app.route('/')
def index():
    """Ana sayfa."""
    try:
        init_db()
        conn = get_db_connection()
        
        today = datetime.now().strftime('%Y-%m-%d')
        
        next_exam = conn.execute(
            'SELECT * FROM exams WHERE date >= ? ORDER BY date LIMIT 1',
            (today,)
        ).fetchone()
        
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
            subject = request.form.get('subject', '').strip()
            date = request.form.get('date', '').strip()
            
            if not subject or not date:
                return render_template('add.html', error='Bitte alle Felder ausfüllen!')
            
            conn = get_db_connection()
            
            conn.execute(
                'INSERT INTO exams (subject, date) VALUES (?, ?)',
                (subject, date)
            )
            
            conn.commit()
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
                
                conn.execute('DELETE FROM exams WHERE id = ?', (exam_id,))
                
                conn.commit()
                conn.close()
            
            return redirect(url_for('delete_exam'))
        
        # Tüm sınavları listele
        conn = get_db_connection()
        
        exams = conn.execute('SELECT * FROM exams ORDER BY date').fetchall()
        
        conn.close()
        
        return render_template('delete.html', exams=exams)
        
    except Exception as e:
        print(f"❌ Delete exam error: {e}")
        return render_template('delete.html', exams=[], error=str(e))

if __name__ == '__main__':
    print("🎯 Initializing SQLite database...")
    init_db()
    print("✅ Database initialized successfully!")
    
    print("🚀 Starting Flask application...")
    app.run(debug=True, host='0.0.0.0', port=5000)

# Render.com için database init
print("🎯 Render.com database initialization...")
init_db()
print("✅ Render.com database ready!")