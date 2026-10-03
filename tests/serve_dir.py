"""Sirve una carpeta por HTTP en 127.0.0.1 (para las pruebas). Uso: python3 tests/serve_dir.py <carpeta> <puerto>
No usa http.server tal cual: su arranque hace una resolución de nombres (getfqdn) que en algunos Mac de GitHub tarda más de 20 s."""
import functools, http.server, socketserver, sys


class Servidor(http.server.ThreadingHTTPServer):
    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]

    def handle_error(self, request, client_address):  # clientes que cortan a propósito: sin ruido
        pass


if __name__ == "__main__":
    class Manejador(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a, **k):
            pass
    h = functools.partial(Manejador, directory=sys.argv[1])
    Servidor(("127.0.0.1", int(sys.argv[2])), h).serve_forever()
