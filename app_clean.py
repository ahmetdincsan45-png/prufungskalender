import os
import sqlite3
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

DATABASE = 'exams.db'

def init_db():
    """Veritabanı ve tabloyu oluştur."""
    try:
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
            print("✅ Database ready!")
            return True
    except Exception as e:
        print(f"❌ Database error: {e}")
        return False

def get_db_connection():
    """Veritabanı bağlantısı."""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def index():
    """Ana sayfa."""
    try:
        init_db()
        conn = get_db_connection()
        
        # Gelecek sınavı bul
        today = datetime.now().strftime('%Y-%m-%d')
        next_exam = conn.execute(
            'SELECT * FROM exams WHERE date >= ? ORDER BY date LIMIT 1',
            (today,)
        ).fetchone()
        conn.close()
        
        return render_template('index.html', next_exam=next_exam)
    except:
        return render_template('index.html', next_exam=None)

@app.route('/events')
def events():
    """JSON etkinlikler."""
    try:
        init_db()
        conn = get_db_connection()
        exams = conn.execute('SELECT * FROM exams ORDER BY date').fetchall()
        conn.close()
        
        events_list = []
        for exam in exams:
            events_list.append({
                'id': exam['id'],
                'title': f"{exam['subject']} ({exam['grade']})",
                'start': f"{exam['date']}T{exam['start_time']}",
                'end': f"{exam['date']}T{exam['end_time']}",
                'backgroundColor': '#007bff',
                'borderColor': '#007bff'
            })
        
        return jsonify(events_list)
    except:
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
            created_at = datetime.now().isoformat()
            
            conn = get_db_connection()
            conn.execute(
                'INSERT INTO exams (subject, grade, date, start_time, end_time, created_at) VALUES (?, ?, ?, ?, ?, ?)',
                (subject, grade, date, start_time, end_time, created_at)
            )
            conn.commit()
            conn.close()
            
            return redirect(url_for('index'))
            
        except Exception as e:
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
        return render_template('delete.html', exams=[], error=str(e))

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)