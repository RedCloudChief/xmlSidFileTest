import os
from flask import Flask, request, send_from_directory, send_file
from generate_d1_report import run_validation
import tempfile

app = Flask(__name__, static_folder='.')

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory('.', path)

@app.route('/verify', methods=['POST'])
def verify():
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

    try:
        print(f"Generating report for {filename} to {output_path}...")
        run_validation(xml_content, output_path, filename)
        
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

@app.route('/api/analyze', methods=['POST'])
def api_analyze():
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
