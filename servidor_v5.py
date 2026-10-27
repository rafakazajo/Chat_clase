# v5 — cambios sobre la 4.2 (el chat y sus comandos siguen igual):
#   · CHAT EN EL NAVEGADOR: en el mismo puerto (5000) el servidor entiende dos cosas:
#       - Un navegador:       http://IP_DEL_SERVIDOR:5000   (sirve index.html, que debe estar en esta carpeta)
#       - Un cliente tkinter: igual que siempre (chat_v4.x.py sigue funcionando).
#     No hace falta instalar nada: solo Python y los archivos servidor_v5.py + index.html.
#   · Archivos desde el navegador por HTTP (subida y descarga), con un token que solo recibe
#     quien ya ha entrado en el chat con la contraseña.
#   · Seguridad: las líneas demasiado largas cortan la conexión (antes se leían enteras antes de
#     recortarlas), el login tiene tiempo máximo y una contraseña incorrecta espera un poco.
#   · La consola ya no se rompe si alguien escribe emojis.

# v4.2 — cambios sobre la 4.0 (que sigue funcionando igual):
#   · /privado funciona también con nombres que llevan espacios, p. ej. "/privado Ruben (apodo) hola"
#     (el cliente 4.2 usa /privado para los mensajes directos en pestañas).
#   · La ayuda ya no muestra /archivos, /descargar ni /subir: los usa el programa por dentro
#     (botones "Enviar archivo" y "Archivos"), no se escriben en el chat.

# v3.2 — cambios sobre la 3.1 (que sigue funcionando igual):
#   · Cada usuario elige el color de su nombre (/color #RRGGBB) y se sincroniza en todos los clientes.
#   · Solo se permiten archivos .py, .java, .txt, .jpg y .png (validado en el servidor).
#   · Los archivos se guardan en la carpeta "chat_archivos" y se pueden descargar.
#   · Nuevos comandos: /color, /archivos (lista) y /descargar nombre.
# Lo que ya funcionaba (contraseña, nombres reservados por IP, privados, /nick, etc.) no se ha tocado.

import re
import os
import json
import base64
import socket
import threading
import time
import hashlib
import secrets
import sys
import urllib.parse
from collections import deque
from datetime import datetime

PUERTO = 5000
MAX_MSG = 2000            # el navegador manda los saltos de línea como "↵", así que cabe código
MENSAJES_HISTORIAL = 50   # últimos mensajes de General que ve quien entra desde el navegador
CONTRASENA_SERVIDOR = "Las contraseñas no se suben"  # Cámbiala antes de ejecutar el servidor.
MAX_ARCHIVO = 10 * 1024 * 1024
EXTENSIONES_PERMITIDAS = {'.py', '.java', '.txt', '.jpg', '.png'}
CARPETA_SUBIDAS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_archivos")

# Colores por defecto (el usuario puede cambiar el suyo con /color #RRGGBB).
PALETA = ["#0b7285", "#9c36b5", "#c92a2a", "#e67700", "#2b8a3e", "#1864ab", "#a61e4d", "#5f3dc4"]
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# Asigna aquí un nombre a cada IP.
# Estos nombres quedan RESERVADOS: solo pueden usarlos las IPs de esta lista.
NOMBRES = {
    "127.0.0.1": "Ruben",
    "10.21.0.1": "Miriam",
    "10.21.0.2": "Jorge",
    "10.21.0.4": "Ruben",
    "10.21.0.5": "Dani",
    "10.21.0.6": "Sebas",
}

clientes = {}          # conexión -> nombre
ips = {}               # conexión -> IP
colores = {}           # conexión -> "#rrggbb" elegido
locks_envio = {}       # conexión -> cerrojo para que los envíos no se mezclen
lock = threading.Lock()

NOMBRE_RE = re.compile(r"^[\w.\-]{1,20}$")

MAX_LINEA = 4096          # una línea más larga que esto corta la conexión
ESPERA_NAVEGADOR = 1.5    # segundos que se espera a ver si quien conecta es un navegador
GUID_WS = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_TRAMA_WS = 8192
INDEX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
TIPOS_ARCHIVO = {
    ".py": "text/plain; charset=utf-8", ".java": "text/plain; charset=utf-8",
    ".txt": "text/plain; charset=utf-8", ".jpg": "image/jpeg", ".png": "image/png",
}
historial = deque(maxlen=MENSAJES_HISTORIAL)   # solo en memoria: se pierde al parar el servidor
tokens = {}            # token -> conexión web (permite subir y bajar archivos por HTTP)


def leer_linea(f):
    """Una línea del cliente ('' si se cierra). Si es demasiado larga corta la conexión
    en vez de llenar la memoria del servidor."""
    linea = f.readline(MAX_LINEA)
    if len(linea) >= MAX_LINEA and not linea.endswith("\n"):
        raise ConnectionError("línea demasiado larga")
    return linea


AYUDA = (
    "Comandos: /usuarios  /privado nombre mensaje  /nick nuevonombre  /color #RRGGBB  /ayuda  "
    "(en tu cliente: /silencio para notificaciones, salir para irte; @nombre para mencionar)"
)


def hora():
    return datetime.now().strftime("%H:%M")


def lock_envio_de(conn):
    with lock:
        if conn not in locks_envio:
            locks_envio[conn] = threading.Lock()
        return locks_envio[conn]


def enviar(conn, texto):
    # Un cerrojo por conexión: varios hilos (chat, descargas...) no pueden
    # escribir a la vez sobre el mismo socket y mezclar líneas.
    with lock_envio_de(conn):
        try:
            conn.sendall((texto + "\n").encode("utf-8"))
            return True
        except OSError:
            return False


def enviar_a_todos(texto, excepto=None):
    # Se copia la lista y se envía fuera del lock para que un cliente lento no bloquee a todos
    with lock:
        destinos = [c for c in clientes if c is not excepto]
    for c in destinos:
        enviar(c, texto)


def nombre_valido(n):
    return bool(NOMBRE_RE.match(n))


def reservado_para_otro(nombre, ip):
    """True si el nombre (o ese nombre con número al final, p. ej. Miriam2)
    pertenece a otra IP de NOMBRES. Así nadie puede hacerse pasar por Miriam."""
    base = nombre.rstrip("0123456789").lower()
    for ip_dueña, n in NOMBRES.items():
        if ip_dueña == ip:
            continue
        if nombre.lower() == n.lower() or (base and base == n.lower()):
            return True
    return False


def nombre_libre(nombre):
    """Llamar con el lock cogido. Evita nombres repetidos añadiendo un número."""
    usados = {n.lower() for n in clientes.values()}
    base, i = nombre, 2
    while nombre.lower() in usados:
        nombre = f"{base}{i}"
        i += 1
    return nombre


def buscar_conn(nombre):
    """Llamar con el lock cogido."""
    for c, n in clientes.items():
        if n.lower() == nombre.lower():
            return c
    return None


def color_libre():
    """Primer color de la paleta que no esté usando nadie.
    Llamar con el lock ya cogido (no vuelve a cogerlo)."""
    usados = set(colores.values())
    for c in PALETA:
        if c not in usados:
            return c
    return "#495057"


def enviar_colores():
    """Difunde la tabla nombre -> color a todos los conectados."""
    with lock:
        tabla = {clientes[c]: colores.get(c) for c in clientes}
    enviar_a_todos("@@COLOR " + json.dumps(tabla))


def drenar_subida(f):
    """Si una subida se rechaza, descarta las líneas DATA pendientes hasta FINARCHIVO
    para que no acaben apareciendo como mensajes de chat.
    Si el terminador no llega (cliente roto o malicioso), cierra la conexión."""
    for _ in range(60000):
        linea = leer_linea(f)
        if not linea:
            raise ConnectionError("subida sin terminar: cliente desconectado")
        if linea.strip() == "FINARCHIVO":
            return
    raise ConnectionError("subida sin terminar: límite de drenado agotado")


def guardar_archivo(nombre_archivo, contenido):
    """Guarda en CARPETA_SUBIDAS sin pisar nada (añade _2, _3...). Devuelve la ruta, o None si falla."""
    try:
        os.makedirs(CARPETA_SUBIDAS, exist_ok=True)
    except OSError:
        return None
    base, ext = os.path.splitext(nombre_archivo)
    destino = os.path.join(CARPETA_SUBIDAS, nombre_archivo)
    contador = 2
    while True:
        try:
            with open(destino, "xb") as archivo:  # creación exclusiva: evita sobrescrituras simultáneas
                archivo.write(contenido)
            return destino
        except FileExistsError:
            destino = os.path.join(CARPETA_SUBIDAS, f"{base}_{contador}{ext}")
            contador += 1
        except OSError:
            return None


def anunciar_subida(conn, destino):
    nombre_final = os.path.basename(destino)
    enviar(conn, f"*** Archivo recibido y guardado en el servidor: {nombre_final} ***")
    with lock:
        quien = clientes.get(conn, "Un usuario")
    enviar_a_todos(f"*** {quien} ha subido el archivo {nombre_final} ***", excepto=conn)


def comando(conn, linea, f=None):
    partes = linea.split(maxsplit=2)
    cmd = partes[0].lower()

    if cmd == "/ayuda":
        enviar(conn, "*** " + AYUDA + " ***")

    elif cmd == "/usuarios":
        with lock:
            lista = ", ".join(sorted(clientes.values(), key=str.lower))
        enviar(conn, f"*** Conectados: {lista} ***")

    elif cmd == "/color":
        argumento = linea[len("/color"):].strip()
        if not COLOR_RE.match(argumento):
            enviar(conn, "*** Uso: /color #RRGGBB (p. ej. /color #1864ab) ***")
            return
        color = argumento.lower()
        with lock:
            colores[conn] = color
        enviar(conn, f"*** Tu color es ahora {color} ***")
        enviar_colores()

    elif cmd == "/archivos":
        try:
            entradas = sorted(os.listdir(CARPETA_SUBIDAS))
        except OSError:
            entradas = []
        tabla = {}
        for e in entradas:
            ruta = os.path.join(CARPETA_SUBIDAS, e)
            if os.path.isfile(ruta) and os.path.splitext(e)[1].lower() in EXTENSIONES_PERMITIDAS:
                try:
                    tabla[e] = os.path.getsize(ruta)
                except OSError:
                    pass
        enviar(conn, "@@ARCHIVOS " + json.dumps(tabla))

    elif cmd == "/descargar":
        resto = linea[len("/descargar"):].strip()
        nombre_archivo = os.path.basename(resto)
        extension = os.path.splitext(nombre_archivo)[1].lower()
        if not resto or nombre_archivo != resto or extension not in EXTENSIONES_PERMITIDAS:
            enviar(conn, "@@ERRARCH Ese archivo no está disponible")
            return
        ruta = os.path.join(CARPETA_SUBIDAS, nombre_archivo)
        if not os.path.isfile(ruta):
            enviar(conn, "@@ERRARCH Ese archivo no existe en el servidor")
            return
        try:
            with open(ruta, "rb") as fh:
                contenido = fh.read(MAX_ARCHIVO + 1)
        except OSError:
            enviar(conn, "@@ERRARCH No se pudo leer el archivo")
            return
        if len(contenido) > MAX_ARCHIVO:
            enviar(conn, "@@ERRARCH El archivo es demasiado grande")
            return
        enviar(conn, "@@DATAIN " + json.dumps({"nombre": nombre_archivo, "tamano": len(contenido)}))
        codificado = base64.b64encode(contenido).decode("ascii")
        for i in range(0, len(codificado), 400):
            if not enviar(conn, "@@DATA " + codificado[i:i + 400]):
                return
        enviar(conn, "@@DATAFIN")

    elif cmd == "/nick":
        nuevo = linea[len(partes[0]):].strip()
        if not nuevo:
            enviar(conn, "*** Uso: /nick nuevonombre ***")
            return
        error = ""
        with lock:
            # validación y asignación dentro del mismo bloque: dos clientes no
            # pueden validar el mismo nombre a la vez y adoptarlo ambos
            ip = ips.get(conn, "")
            nombre_reservado = NOMBRES.get(ip)
            if nombre_reservado:
                # Las IP reservadas conservan su identidad: Nombre (nombrenuevo).
                patron = re.compile(r"^" + re.escape(nombre_reservado) + r" \(([\w.\-]{1,20})\)$")
                if not patron.match(nuevo):
                    error = f"*** Esta IP solo puede cambiar el nombre con el formato: {nombre_reservado} (nombrenuevo) ***"
            elif not nombre_valido(nuevo):
                error = "*** Uso: /nick nombre (sin espacios ni símbolos raros, máx. 20 caracteres) ***"
            if not error and reservado_para_otro(nuevo, ip):
                error = "*** Ese nombre está reservado para otra persona ***"
            if not error:
                otro = buscar_conn(nuevo)
                if otro is not None and otro is not conn:
                    error = "*** Ese nombre ya está en uso ***"
            if not error:
                viejo = clientes[conn]
                clientes[conn] = nuevo
        if error:
            enviar(conn, error)  # fuera del lock: enviar() vuelve a cogerlo
            return
        enviar_a_todos(f"*** {viejo} ahora se llama {nuevo} ***")
        enviar_colores()  # el color se conserva aunque cambies de nombre

    elif cmd == "/subir":
        if f is None:
            enviar(conn, "*** No se puede recibir el archivo en este momento ***")
            return
        partes_subida = linea.split(maxsplit=2)
        if len(partes_subida) != 3:
            enviar(conn, "*** Uso interno de subida no válido ***")
            drenar_subida(f)
            return
        nombre_archivo, tamaño_txt = partes_subida[1], partes_subida[2]
        nombre_archivo = os.path.basename(nombre_archivo).replace("\\x00", "")
        extension = os.path.splitext(nombre_archivo)[1].lower()
        try:
            tamaño = int(tamaño_txt)
        except ValueError:
            enviar(conn, "*** Tamaño de archivo no válido ***")
            drenar_subida(f)
            return
        if not nombre_archivo or extension not in EXTENSIONES_PERMITIDAS:
            enviar(conn, "*** Tipo de archivo no permitido: solo .py, .java, .txt, .jpg y .png ***")
            drenar_subida(f)
            return
        if tamaño < 0 or tamaño > MAX_ARCHIVO:
            enviar(conn, "*** El tamaño máximo permitido es 10 MB ***")
            drenar_subida(f)
            return
        trozos = []
        total = 0
        while True:
            linea_datos = leer_linea(f)
            if not linea_datos:
                enviar(conn, "*** Subida interrumpida ***")
                return
            linea_datos = linea_datos.strip()
            if linea_datos == "FINARCHIVO":
                break
            if not linea_datos.startswith("DATA "):
                enviar(conn, "*** Protocolo de subida no válido ***")
                drenar_subida(f)
                return
            bloque = linea_datos[5:]
            total += len(bloque)
            if total > ((MAX_ARCHIVO + 2) // 3) * 4 + 8:
                enviar(conn, "*** Archivo demasiado grande ***")
                drenar_subida(f)
                return
            trozos.append(bloque)
        try:
            contenido = base64.b64decode("".join(trozos), validate=True)
        except (ValueError, base64.binascii.Error):
            enviar(conn, "*** El archivo recibido no es válido ***")
            return
        if len(contenido) != tamaño:
            enviar(conn, "*** El tamaño recibido no coincide ***")
            return
        destino = guardar_archivo(nombre_archivo, contenido)
        if destino is None:
            enviar(conn, "*** No se pudo guardar el archivo en el servidor ***")
            return
        anunciar_subida(conn, destino)

    elif cmd == "/privado":
        if len(partes) < 3:
            enviar(conn, "*** Uso: /privado nombre mensaje ***")
            return
        destino, texto = partes[1], partes[2]
        resto = linea[len(partes[0]):].strip()
        with lock:
            # se busca el nombre conectado más largo que encaje, así funcionan también
            # los nombres con espacios ("Ruben (apodo)") aunque haya otro "Ruben"
            c_dest = None
            for c, n in sorted(clientes.items(), key=lambda par: -len(par[1])):
                if resto.lower().startswith(n.lower() + " ") and resto[len(n):].strip():
                    c_dest, texto = c, resto[len(n):].strip()
                    break
            origen = clientes[conn]
            nombre_dest = clientes.get(c_dest)
        if c_dest is None:
            enviar(conn, f"*** No hay nadie conectado llamado {destino} ***")
            return
        enviar(c_dest, f"[{hora()}] (privado) de {origen}: {texto}")
        enviar(conn, f"[{hora()}] (privado) para {nombre_dest}: {texto}")

    else:
        enviar(conn, "*** Comando desconocido. Escribe /ayuda ***")


def pedir_nombre(conn, f, ip):
    """Pide un nombre hasta que sea válido y no esté reservado. None si se desconecta."""
    enviar(conn, "Tu IP no tiene nombre asignado. Escribe tu nombre:")
    while True:
        linea = leer_linea(f)
        if not linea:
            return None
        candidato = linea.strip().replace(" ", "_")[:20]
        if not candidato:
            enviar(conn, "El nombre no puede estar vacío. Escribe tu nombre:")
        elif not nombre_valido(candidato):
            enviar(conn, "Usa solo letras, números, punto, guion o guion bajo. Escribe tu nombre:")
        elif reservado_para_otro(candidato, ip):
            enviar(conn, "Ese nombre está reservado para otra persona. Elige otro:")
        else:
            return candidato


def atender(conn, addr):
    ip = addr[0]
    f = conn.makefile("r", encoding="utf-8", errors="replace")
    nombre = NOMBRES.get(ip)
    registrado = False
    try:
        conn.settimeout(60)  # si no completa el login en un minuto, se cierra
        enviar(conn, "CONTRASENA")
        respuesta_clave = leer_linea(f).strip()
        if not respuesta_clave.startswith("/clave ") or respuesta_clave[len("/clave "):] != CONTRASENA_SERVIDOR:
            time.sleep(1.5)  # frena los intentos seguidos
            enviar(conn, "*** CONTRASENA_INCORRECTA ***")
            return
        if not nombre:
            nombre = pedir_nombre(conn, f, ip)
            if nombre is None:
                return

        with lock:
            nombre = nombre_libre(nombre)
            clientes[conn] = nombre
            ips[conn] = ip
            colores[conn] = color_libre()
        registrado = True
        conn.settimeout(None)

        print(f"[+] {nombre} conectado desde {ip}")
        enviar(conn, f"Hola {nombre}, ya estás en el chat. Escribe /ayuda para ver los comandos.")
        if isinstance(conn, ConexionWeb):
            token = secrets.token_hex(16)
            with lock:
                tokens[token] = conn
            enviar(conn, f"@@TOKEN {token}")
            with lock:
                previos = list(historial)
            enviar(conn, "@@HISTORIAL_INI")
            for previo in previos:
                enviar(conn, previo)
            enviar(conn, "@@HISTORIAL_FIN")
        enviar_a_todos(f"*** {nombre} se ha conectado ***", excepto=conn)
        enviar_colores()

        while True:
            linea = leer_linea(f)
            if not linea:
                break
            linea = linea.strip()[:MAX_MSG]
            if not linea:
                continue
            if linea.startswith("/"):
                comando(conn, linea, f)
                continue
            with lock:
                nombre = clientes[conn]
            print(f"[{hora()}] {nombre}: {linea}")
            texto_chat = f"[{hora()}] {nombre}: {linea}"
            with lock:
                historial.append(texto_chat)
            enviar_a_todos(texto_chat, excepto=conn)
    except (OSError, ValueError):
        pass
    finally:
        with lock:
            nombre = clientes.pop(conn, nombre)
            ips.pop(conn, None)
            colores.pop(conn, None)
            locks_envio.pop(conn, None)
            for t in [k for k, v in tokens.items() if v is conn]:
                tokens.pop(t, None)
        try:
            conn.close()
        except OSError:
            pass
        if registrado:
            print(f"[-] {nombre} desconectado")
            enviar_a_todos(f"*** {nombre} se ha desconectado ***")
            enviar_colores()


# ------------------------------------------------------------------ navegador (HTTP + WebSocket)
class ConexionWeb:
    """Un WebSocket que se comporta como el socket de texto del chat: cada mensaje es una línea."""

    def __init__(self, sock):
        self.sock = sock
        self.lock_w = threading.Lock()
        self.lector = LectorWS(self)
        self.cierre_enviado = False  # el cierre se manda una sola vez

    def settimeout(self, segundos):
        self.sock.settimeout(segundos)

    def makefile(self, *args, **kwargs):
        return self.lector

    def sendall(self, datos):
        texto = datos.decode("utf-8").rstrip("\n")
        self.enviar_trama(0x1, texto.encode("utf-8"))

    def enviar_trama(self, opcode, carga=b""):
        n = len(carga)
        if n < 126:
            cabecera = bytes([0x80 | opcode, n])
        elif n < 65536:
            cabecera = bytes([0x80 | opcode, 126]) + n.to_bytes(2, "big")
        else:
            cabecera = bytes([0x80 | opcode, 127]) + n.to_bytes(8, "big")
        with self.lock_w:  # varios hilos escriben en el mismo socket: una trama detrás de otra
            if opcode == 0x8:
                if self.cierre_enviado:
                    return
                self.cierre_enviado = True
            self.sock.sendall(cabecera + carga)

    def close(self):
        try:
            self.enviar_trama(0x8, b"\x03\xe8")  # cierre normal (1000)
        except OSError:
            pass
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class LectorWS:
    """Lee tramas WebSocket y las entrega como líneas, igual que makefile() de un socket normal."""

    def __init__(self, ws):
        self.ws = ws

    def _leer(self, n):
        datos = bytearray()
        while len(datos) < n:
            trozo = self.ws.sock.recv(n - len(datos))
            if not trozo:
                raise ConnectionError("conexión cerrada")
            datos += trozo
        return bytes(datos)

    def readline(self, limite=-1):
        mensaje = bytearray()
        while True:
            b0, b1 = self._leer(2)
            fin, opcode = b0 & 0x80, b0 & 0x0F
            if not b1 & 0x80:
                raise ConnectionError("el cliente debe enmascarar sus tramas")
            n = b1 & 0x7F
            if n == 126:
                n = int.from_bytes(self._leer(2), "big")
            elif n == 127:
                n = int.from_bytes(self._leer(8), "big")
            if n > MAX_TRAMA_WS:
                raise ConnectionError("trama demasiado grande")
            mascara = self._leer(4)
            carga = bytearray(self._leer(n))
            for i in range(n):
                carga[i] ^= mascara[i % 4]
            if opcode == 0x8:      # cierre
                try:
                    self.ws.enviar_trama(0x8, bytes(carga[:2]))
                except OSError:
                    pass
                return ""
            if opcode == 0x9:      # ping -> pong
                self.ws.enviar_trama(0xA, bytes(carga[:125]))
                continue
            if opcode == 0xA:      # pong
                continue
            if opcode not in (0x0, 0x1):
                raise ConnectionError("solo se admite texto")
            mensaje += carga
            if len(mensaje) > MAX_TRAMA_WS:
                raise ConnectionError("mensaje demasiado grande")
            if fin:
                break
        texto = bytes(mensaje).decode("utf-8", errors="replace")
        return texto.replace("\r", " ").replace("\n", " ") + "\n"


def conn_de_token(token):
    with lock:
        return tokens.get(token)


def responder(sock, codigo, razon, cuerpo=b"", tipo="text/plain; charset=utf-8", extra=None):
    cabecera = [
        f"HTTP/1.1 {codigo} {razon}", f"Content-Type: {tipo}", f"Content-Length: {len(cuerpo)}",
        "Connection: close", "X-Content-Type-Options: nosniff", "Referrer-Policy: no-referrer",
        "Cache-Control: no-cache",
    ]
    for clave, valor in (extra or {}).items():
        cabecera.append(f"{clave}: {valor}")
    sock.sendall(("\r\n".join(cabecera) + "\r\n\r\n").encode("utf-8") + cuerpo)


def responder_json(sock, codigo, razon, datos):
    responder(sock, codigo, razon, json.dumps(datos).encode("utf-8"), "application/json; charset=utf-8")


def leer_peticion(sock):
    datos = b""
    while b"\r\n\r\n" not in datos:
        trozo = sock.recv(4096)
        if not trozo:
            raise ConnectionError("conexión cerrada")
        datos += trozo
        if len(datos) > 16384:
            raise ConnectionError("cabeceras demasiado largas")
    cabecera, _, resto = datos.partition(b"\r\n\r\n")
    lineas = cabecera.decode("latin-1").split("\r\n")
    metodo, ruta, _version = lineas[0].split(" ", 2)
    cabeceras = {}
    for linea in lineas[1:]:
        clave, _, valor = linea.partition(":")
        cabeceras[clave.strip().lower()] = valor.strip()
    return metodo, ruta, cabeceras, resto


def servir_index(sock):
    try:
        with open(INDEX, "rb") as fh:
            cuerpo = fh.read()
    except OSError:
        responder(sock, 500, "Internal Server Error", "Falta index.html junto al servidor.\n".encode("utf-8"))
        return
    csp = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
           "img-src 'self' data:; connect-src 'self' ws: wss:; frame-ancestors 'none'")
    responder(sock, 200, "OK", cuerpo, "text/html; charset=utf-8",
              {"Content-Security-Policy": csp, "X-Frame-Options": "DENY"})


def servir_archivo(sock, nombre, token, descargar):
    if conn_de_token(token) is None:
        responder(sock, 403, "Forbidden", "Sesión no válida\n".encode("utf-8"))
        return
    extension = os.path.splitext(nombre)[1].lower()
    if (not nombre or any(c in nombre for c in "/\\\x00") or extension not in TIPOS_ARCHIVO):
        responder(sock, 404, "Not Found", "Ese archivo no está disponible\n".encode("utf-8"))
        return
    ruta = os.path.join(CARPETA_SUBIDAS, nombre)
    try:
        with open(ruta, "rb") as fh:
            contenido = fh.read(MAX_ARCHIVO + 1)
    except OSError:
        responder(sock, 404, "Not Found", "Ese archivo no existe en el servidor\n".encode("utf-8"))
        return
    if len(contenido) > MAX_ARCHIVO:
        responder(sock, 413, "Payload Too Large", "El archivo es demasiado grande\n".encode("utf-8"))
        return
    disposicion = ("attachment" if descargar else "inline") + "; filename*=UTF-8''" + urllib.parse.quote(nombre)
    responder(sock, 200, "OK", contenido, TIPOS_ARCHIVO[extension], {"Content-Disposition": disposicion})


def recibir_subida(sock, cab, resto, consulta, token):
    conn = conn_de_token(token)
    if conn is None:
        responder_json(sock, 403, "Forbidden", {"error": "Sesión no válida"})
        return
    try:
        tamano = int(cab.get("content-length", ""))
    except ValueError:
        responder_json(sock, 411, "Length Required", {"error": "Falta el tamaño del archivo"})
        return
    if tamano < 0 or tamano > MAX_ARCHIVO:
        responder_json(sock, 413, "Payload Too Large", {"error": "El tamaño máximo permitido es 10 MB"})
        return
    nombre = (consulta.get("nombre") or [""])[0]
    nombre = re.sub(r"[^\w.\-()]", "_", os.path.basename(nombre.replace("\\", "/")))[:100]
    if not nombre or os.path.splitext(nombre)[1].lower() not in EXTENSIONES_PERMITIDAS:
        responder_json(sock, 400, "Bad Request", {"error": "Tipo de archivo no permitido: solo .py, .java, .txt, .jpg y .png"})
        return
    cuerpo = bytearray(resto)
    sock.settimeout(60)
    while len(cuerpo) < tamano:
        trozo = sock.recv(min(65536, tamano - len(cuerpo)))
        if not trozo:
            raise ConnectionError("subida interrumpida")
        cuerpo += trozo
    destino = guardar_archivo(nombre, bytes(cuerpo[:tamano]))
    if destino is None:
        responder_json(sock, 500, "Internal Server Error", {"error": "No se pudo guardar el archivo en el servidor"})
        return
    anunciar_subida(conn, destino)
    responder_json(sock, 200, "OK", {"nombre": os.path.basename(destino)})


def abrir_websocket(sock, addr, cab):
    clave = cab.get("sec-websocket-key", "")
    origen = cab.get("origin")
    if origen and urllib.parse.urlsplit(origen).netloc != cab.get("host", ""):
        responder(sock, 403, "Forbidden", "Origen no permitido\n".encode("utf-8"))  # evita webs ajenas
        return
    if not clave or cab.get("sec-websocket-version") != "13":
        responder(sock, 400, "Bad Request", "Falta la clave de WebSocket\n".encode("utf-8"))
        return
    aceptar = base64.b64encode(hashlib.sha1((clave + GUID_WS).encode("ascii")).digest()).decode("ascii")
    sock.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  f"Sec-WebSocket-Accept: {aceptar}\r\n\r\n").encode("ascii"))
    atender(ConexionWeb(sock), addr)  # hasta que se cierre la conexión


def atender_http(sock, addr):
    try:
        sock.settimeout(30)
        metodo, ruta, cab, resto = leer_peticion(sock)
        url = urllib.parse.urlsplit(ruta)
        camino = urllib.parse.unquote(url.path)
        consulta = urllib.parse.parse_qs(url.query)
        token = cab.get("x-token") or (consulta.get("t") or [""])[0]
        if camino == "/ws" and cab.get("upgrade", "").lower() == "websocket":
            abrir_websocket(sock, addr, cab)
        elif metodo == "GET" and camino in ("/", "/index.html"):
            servir_index(sock)
        elif metodo == "GET" and camino.startswith("/archivo/"):
            servir_archivo(sock, camino[len("/archivo/"):], token, "descargar" in consulta)
        elif metodo == "POST" and camino == "/subir":
            recibir_subida(sock, cab, resto, consulta, token)
        else:
            responder(sock, 404, "Not Found", "No existe\n".encode("utf-8"))
    except (OSError, ValueError):
        pass
    finally:
        try:
            sock.close()
        except OSError:
            pass


def recibir_conexion(conn, addr):
    """Un navegador empieza hablando ('GET ...'); un cliente tkinter espera el saludo del servidor."""
    try:
        limite = time.time() + ESPERA_NAVEGADOR
        inicio = b""
        conn.settimeout(0.1)
        while len(inicio) < 4 and time.time() < limite:
            try:
                inicio = conn.recv(8, socket.MSG_PEEK)
            except socket.timeout:
                continue
            if not inicio:
                conn.close()
                return
            time.sleep(0.02)
        conn.settimeout(None)
    except OSError:
        try:
            conn.close()
        except OSError:
            pass
        return
    if inicio[:4] in (b"GET ", b"POST", b"HEAD", b"OPTI", b"PUT ", b"DELE", b"PATC"):
        atender_http(conn, addr)
    else:
        atender(conn, addr)


def ip_local():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # no envía nada: solo elige la interfaz de red
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "IP_DEL_SERVIDOR"


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # los emojis no rompen la consola
    os.makedirs(CARPETA_SUBIDAS, exist_ok=True)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", PUERTO))
    s.listen()
    print(f"Servidor activo en el puerto {PUERTO}. Ctrl+C para parar.")
    print(f"Chat en el navegador: http://{ip_local()}:{PUERTO}")
    try:
        while True:
            conn, addr = s.accept()
            threading.Thread(target=recibir_conexion, args=(conn, addr), daemon=True).start()
    except KeyboardInterrupt:
        print("\nServidor detenido.")
    finally:
        s.close()


if __name__ == "__main__":
    main()
