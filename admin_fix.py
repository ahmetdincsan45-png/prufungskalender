import os, sqlite3
from werkzeug.security import generate_password_hash, check_password_hash

db_path = os.environ.get("SQLITE_DB_PATH")
if not db_path:
    db_path = os.path.join(os.getcwd(), "prufungskalender.local.db")

print("DB:", db_path)
conn = sqlite3.connect(db_path)
cur = conn.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS admin_credentials (username TEXT, password_hash TEXT)")
cur.execute("DELETE FROM admin_credentials")
pw_hash = generate_password_hash("45ee551A.")
cur.execute("INSERT INTO admin_credentials (username, password_hash) VALUES (?, ?)", ("Ahmet", pw_hash))
conn.commit()
cur.execute("SELECT username, password_hash FROM admin_credentials")
row = cur.fetchone()
print("CREDS:", row[0], bool(check_password_hash(row[1], "45ee551A.")))
conn.close()
