# GIFSniffer

Web app per scaricare video Pinterest/Instagram o convertirli in GIF con controlli avanzati.

## Funzionalità
- Analisi URL e formati disponibili (format_id, risoluzione, fps, codec).
- Download video con scelta format ID, risoluzione target e qualità (CRF).
- Download GIF con scelta larghezza, FPS, numero colori, velocità.
- Supporto autenticazione Instagram via username/password o cookie file (consigliato).

## Nota importante su Instagram
Per ridurre rate-limit/ban:
1. Usa cookie autenticati (`INSTAGRAM_COOKIES_FILE`) esportati dal browser.
2. Mantieni basso il numero di richieste concorrenti.
3. Aggiorna spesso i cookie.
4. Usa un proxy residenziale/rotazione IP a livello VPS/reverse proxy se necessario.

## Installazione VPS (Docker)
```bash
git clone <tuo-repo> GIFSniffer
cd GIFSniffer
cp .env.example .env
# configura variabili Instagram
docker compose up -d --build
```

App disponibile su `http://IP_VPS:8000`.

## Dominio + HTTPS consigliato (Nginx + Certbot)
- Punta record A del dominio al VPS.
- Reverse proxy Nginx verso `127.0.0.1:8000`.
- Certificato con Let's Encrypt.

Esempio server block:
```nginx
server {
    listen 80;
    server_name tuo-dominio.it;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Avvio locale senza Docker
Richiede `ffmpeg` installato.
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Caricare su GitHub
```bash
git init
git add .
git commit -m "feat: initial GIFSniffer app"
git branch -M main
git remote add origin git@github.com:TUOUSER/GIFSniffer.git
git push -u origin main
```
