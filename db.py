import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), 'users.db')

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    # Controlliamo se la colonna totp_secret esiste, altrimenti l'aggiungiamo
    cursor = conn.execute("PRAGMA table_info(users)")
    columns = [row['name'] for row in cursor.fetchall()]
    
    if len(columns) > 0 and 'totp_secret' not in columns:
        # La tabella esiste ma è vecchia, droppiamo per ricrearla pulita
        conn.execute('DROP TABLE IF EXISTS users')
        
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            cognome TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            totp_secret TEXT,
            data_nascita TEXT,
            luogo_nascita TEXT,
            codice_fiscale TEXT,
            partita_iva TEXT,
            indirizzo TEXT,
            cap TEXT,
            citta TEXT,
            provincia TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def create_user(nome, cognome, email, password_hash, totp_secret):
    conn = get_db_connection()
    try:
        conn.execute(
            'INSERT INTO users (nome, cognome, email, password_hash, totp_secret) VALUES (?, ?, ?, ?, ?)',
            (nome, cognome, email, password_hash, totp_secret)
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False # Email already exists
    finally:
        conn.close()

def get_user_by_email(email):
    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
    conn.close()
    return user

def get_user_by_id(user_id):
    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    return user

def update_user(user_id, nome, cognome, email, data_nascita=None, luogo_nascita=None, codice_fiscale=None, partita_iva=None, indirizzo=None, cap=None, citta=None, provincia=None):
    conn = get_db_connection()
    try:
        conn.execute(
            '''UPDATE users SET 
               nome = ?, cognome = ?, email = ?, 
               data_nascita = ?, luogo_nascita = ?, codice_fiscale = ?, 
               partita_iva = ?, indirizzo = ?, cap = ?, 
               citta = ?, provincia = ? 
               WHERE id = ?''',
            (nome, cognome, email, data_nascita, luogo_nascita, codice_fiscale, partita_iva, indirizzo, cap, citta, provincia, user_id)
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()
