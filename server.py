import os
from http.server import HTTPServer, SimpleHTTPRequestHandler

class SecureHandler(SimpleHTTPRequestHandler):
    BLOCKED_EXTENSIONS = ('.py', '.pyc', '.txt', '.sh', '.env', '.md', '.backup')
    BLOCKED_FILES = ('requirements.txt', 'Procfile', 'render.yaml')

    def do_GET(self):
        clean_path = self.path.split('?')[0].split('#')[0]
        
        # Bloquer les fichiers et dossiers cachés (.git, .env...)
        if any(part.startswith('.') for part in clean_path.split('/')):
            self.send_error(404, "File not found")
            return
            
        # Bloquer le code source et les fichiers système
        lower_path = clean_path.lower()
        if lower_path.endswith(self.BLOCKED_EXTENSIONS) or any(clean_path.rstrip('/').endswith('/' + f) or clean_path == '/' + f for f in self.BLOCKED_FILES):
            self.send_error(404, "File not found")
            return

        super().do_GET()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    server = HTTPServer(('0.0.0.0', port), SecureHandler)
    print(f"Serveur actif sur le port {port}", flush=True)
    server.serve_forever()
