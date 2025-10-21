from flask import Flask

app = Flask(__name__)

@app.route('/')
def hello():
    return '''
    <h1>🎉 Test Başarılı!</h1>
    <p>Flask çalışıyor!</p>
    <p>Şu anda: 21 Oktober 2025</p>
    <a href="/test">Test Sayfası</a>
    '''

@app.route('/test')
def test():
    return '<h2>✅ Test sayfası da çalışıyor!</h2>'

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)