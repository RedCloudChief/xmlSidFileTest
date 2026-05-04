import os
from flask import Flask, request, send_from_directory, send_file, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from generate_d1_report import run_validation
from datetime import timedelta
import tempfile
import db
import pyotp
import urllib.parse

app = Flask(__name__, static_folder='.')
app.secret_key = 'geo-d1-super-secret-key-premium' # In produzione dovrebbe essere una variabile d'ambiente
app.permanent_session_lifetime = timedelta(hours=1)

# Inizializza il DB all'avvio
with app.app_context():
    db.init_db()

@app.route('/')
def index():
    user_id = session.get('user_id')
    if not user_id or not db.get_user_by_id(user_id):
        session.pop('user_id', None)
        return redirect(url_for('login_page'))
    resp = send_from_directory('.', 'index.html')
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    return resp

@app.route('/login')
def login_page():
    user_id = session.get('user_id')
    if user_id and db.get_user_by_id(user_id):
        return redirect(url_for('index'))
    return send_from_directory('.', 'login.html')

@app.route('/login_action', methods=['POST'])
def login_action():
    email = request.form.get('email')
    password = request.form.get('password')
    
    user = db.get_user_by_email(email)
    if user and check_password_hash(user['password_hash'], password):
        session.permanent = True
        session['user_id'] = user['id']
        return redirect(url_for('index'))
    else:
        return redirect(url_for('login_page', error='Credenziali non valide.'))

@app.route('/register_action', methods=['POST'])
def register_action():
    nome = request.form.get('nome')
    cognome = request.form.get('cognome')
    email = request.form.get('email')
    password = request.form.get('password')
    
    if not email or not password or not nome or not cognome:
        return redirect(url_for('login_page', error='Tutti i campi sono obbligatori.'))
        
    password_hash = generate_password_hash(password)
    totp_secret = pyotp.random_base32()
    
    if db.create_user(nome, cognome, email, password_hash, totp_secret):
        totp_uri = pyotp.totp.TOTP(totp_secret).provisioning_uri(name=email, issuer_name="GEO-D1")
        encoded_uri = urllib.parse.quote(totp_uri)
        return redirect(url_for('login_page', registered='true', uri=encoded_uri))
    else:
        return redirect(url_for('login_page', error='Email già registrata.'))

@app.route('/verify_2fa', methods=['POST'])
def verify_2fa():
    if 'pre_2fa_user_id' not in session:
        return redirect(url_for('login_page', error='Sessione scaduta o non valida.'))
        
    code = request.form.get('totp_code')
    user = db.get_user_by_id(session['pre_2fa_user_id'])
    
    if not user:
        return redirect(url_for('login_page', error='Utente non trovato.'))
        
    totp = pyotp.TOTP(user['totp_secret'])
    if totp.verify(code):
        session.permanent = True
        session['user_id'] = user['id']
        session.pop('pre_2fa_user_id', None)
        return redirect(url_for('index'))
    else:
        return redirect(url_for('login_page', require_2fa='true', error='Codice 2FA errato.'))

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('login_page'))

@app.route('/<path:path>')
def serve_static(path):
    path_lower = path.lower()
    
    # Estensioni sicure consentite
    allowed_extensions = {'.html', '.css', '.js', '.png', '.jpg', '.jpeg', '.svg', '.pdf', '.xml', '.xlt', '.ico'}
    
    # Blocca i file non autorizzati
    if not any(path_lower.endswith(ext) for ext in allowed_extensions):
        return "Access Denied: File type not allowed", 403
        
    # Blocca i file HTML se non loggato, eccetto login.html
    if path_lower.endswith('.html') and path_lower != 'login.html':
        user_id = session.get('user_id')
        if not user_id or not db.get_user_by_id(user_id):
            session.pop('user_id', None)
            return redirect(url_for('login_page'))
        
    resp = send_from_directory('.', path)
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    return resp

@app.route('/verify', methods=['POST'])
def verify():
    if 'user_id' not in session:
        return "Unauthorized", 401

    if 'file' not in request.files:
        xml_content = request.form.get('xml_data')
        filename = request.form.get('filename', 'uploaded.xml')
    else:
        file = request.files['file']
        xml_content = file.read()
        filename = file.filename

    if not xml_content:
        return "No XML content provided", 400

    # On Windows, we must be careful with file handles
    fd, output_path = tempfile.mkstemp(suffix='.pdf')
    os.close(fd) # Close immediately, let fpdf manage the handle

    show_labels = request.form.get('show_labels', 'true').lower() == 'true'
    try:
        print(f"Generating report for {filename} to {output_path} (Labels: {show_labels})...")
        run_validation(xml_content, output_path, filename, show_labels=show_labels)
        
        if not os.path.exists(output_path):
            return "PDF generation failed - file not found", 500
            
        size = os.path.getsize(output_path)
        print(f"Report generated successfully. Size: {size} bytes.")
        
        if size < 1000: # A minimal PDF is usually > 1KB
            return f"Generated PDF is too small or empty ({size} bytes)", 500

        # We return the file and use a custom cleanup if needed, 
        # but for simplicity we'll just send it.
        # Flask's send_file is safe for closed files.
        return send_file(output_path, 
                         mimetype='application/pdf',
                         as_attachment=True,
                         download_name=filename.replace('.xml', '.ControlloFileDomanda.pdf'))
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Error: {str(e)}")
        return f"Error generating report: {str(e)}", 500
    finally:
        # We don't delete here because send_file needs to read it
        # In a production app, we would use a response processor or a scheduled cleanup
        pass

@app.route('/api/user')
def api_user():
    if 'user_id' not in session:
        return {"error": "Unauthorized"}, 401
    user = db.get_user_by_id(session['user_id'])
    if user:
        user_dict = dict(user)
        return {
            "nome": user_dict.get('nome'), 
            "cognome": user_dict.get('cognome'), 
            "email": user_dict.get('email'),
            "data_nascita": user_dict.get('data_nascita'),
            "luogo_nascita": user_dict.get('luogo_nascita'),
            "codice_fiscale": user_dict.get('codice_fiscale'),
            "partita_iva": user_dict.get('partita_iva'),
            "indirizzo": user_dict.get('indirizzo'),
            "cap": user_dict.get('cap'),
            "citta": user_dict.get('citta'),
            "provincia": user_dict.get('provincia')
        }
    return {"error": "Not found"}, 404

@app.route('/api/user/update', methods=['POST'])
def api_user_update():
    if 'user_id' not in session:
        return {"error": "Unauthorized"}, 401
    
    data = request.json
    nome = data.get('nome')
    cognome = data.get('cognome')
    email = data.get('email')
    
    # New fields
    data_nascita = data.get('data_nascita')
    luogo_nascita = data.get('luogo_nascita')
    codice_fiscale = data.get('codice_fiscale')
    partita_iva = data.get('partita_iva')
    indirizzo = data.get('indirizzo')
    cap = data.get('cap')
    citta = data.get('citta')
    provincia = data.get('provincia')
    
    if not nome or not cognome or not email or not codice_fiscale:
        return {"error": "Nome, Cognome, Email e Codice Fiscale sono obbligatori"}, 400
        
    success = db.update_user(
        session['user_id'], nome, cognome, email,
        data_nascita, luogo_nascita, codice_fiscale, partita_iva,
        indirizzo, cap, citta, provincia
    )
    if success:
        return {"status": "success"}
    else:
        return {"error": "Errore durante l'aggiornamento o email già presente"}, 500

@app.route('/api/analyze', methods=['POST'])
def api_analyze():
    if 'user_id' not in session:
        return {"error": "Unauthorized"}, 401

    if 'file' not in request.files:
        return {"error": "Nessun file fornito"}, 400
    
    file = request.files['file']
    xml_content = file.read()
    filename = file.filename
    
    if not xml_content:
        return {"error": "Contenuto vuoto"}, 400
        
    try:
        from generate_d1_report import parse_xml, check_containment
        data = parse_xml(xml_content, filename)
        fuoriuscite = check_containment(data['objects'])
        return {"fuoriuscite": fuoriuscite}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"error": str(e)}, 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8000))
    print(f"Avvio server Flask su http://localhost:{port}...")
    app.run(host='0.0.0.0', port=port, debug=True)
