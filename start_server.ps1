# Script per avviare un server locale per GEO-D1/D2
# Questo è necessario per caricare i file dei confini locali (GeoJSON)
# che i browser bloccano per sicurezza se aperti direttamente come file.

Write-Host "Inizializzazione server locale su http://localhost:8000..." -ForegroundColor Cyan
Write-Host "Premi Ctrl+C per fermare il server." -ForegroundColor Yellow

# Avvia il server Flask (Backend + Frontend)
python app.py
