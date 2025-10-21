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
    """Veritabanını ilk kez başlatır ve schema.sql'i uygular."""
    if not os.path.exists(DATABASE):
        with sqlite3.connect(DATABASE) as conn:
            with open('schema.sql', 'r', encoding='utf-8') as f:
                conn.executescript(f.read())
        print("Veritabanı oluşturuldu ve schema.sql uygulandı.")

def get_db_connection():
    """Veritabanı bağlantısı döndürür."""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def index():
    """Ana sayfa - FullCalendar takvimi."""
    return render_template('index.html')

@app.route('/events')
def events():
    """Takvim etkinlikleri JSON formatında döndürür."""
    conn = get_db_connection()
    exams = conn.execute(
        'SELECT * FROM exams ORDER BY date, start_time'
    ).fetchall()
    conn.close()
    
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
        subject = request.form.get('subject', '').strip()
        grade = request.form.get('grade', '').strip()
        date = request.form.get('date', '').strip()
        start_time = request.form.get('start_time', '').strip()
        end_time = request.form.get('end_time', '').strip()
        
        # Einfache Validierung
        if not all([subject, grade, date, start_time, end_time]):
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
    
    return render_template('add.html')

@app.route('/delete', methods=['GET', 'POST'])
def delete_exam():
    """Sınav silme sayfası."""
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

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)