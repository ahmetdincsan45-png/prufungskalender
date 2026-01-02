import urllib.request, urllib.parse
import http.cookiejar

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

login_data = urllib.parse.urlencode({
    'login_attempt': '1',
    'username': 'Ahmet',
    'password': '45ee551A.'
}).encode('utf-8')

resp1 = opener.open('http://127.0.0.1:5000/stats', data=login_data)
print('LOGIN_STATUS:', resp1.getcode())

resp2 = opener.open('http://127.0.0.1:5000/stats')
content = resp2.read().decode('utf-8', errors='ignore')
print('STATS_STATUS:', resp2.getcode())
print('HAS_DASH:', ('Genel Bakış' in content) or ('Ziyaretçi İstatistikleri' in content))
