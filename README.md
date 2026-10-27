# Chat de clase

Un chat sencillo para hablar entre compañeros de clase dentro de la misma red, con envío de archivos. Un ordenador hace de servidor y los demás entran desde el navegador, sin instalar nada.

> **Esto no es un proyecto serio.** Es un chat hecho para clase y para aprender. No está pensado para usarse fuera de una red de confianza ni para guardar nada importante. Lee la sección [Limitaciones](#limitaciones-y-seguridad) antes de usarlo.

## Cómo funciona

El servidor (`servidor_v5.py`) escucha en el puerto 5000 y entiende dos tipos de conexión en ese mismo puerto:

- **Navegador:** al abrir `http://IP_DEL_SERVIDOR:5000` el servidor entrega `index.html`, que se conecta de vuelta por WebSocket y funciona como cliente del chat.
- **Cliente antiguo de escritorio (tkinter):** el servidor sigue entendiendo el protocolo original por TCP, así que las versiones antiguas del cliente siguen funcionando contra este servidor.

Los clientes del navegador no necesitan Python ni instalar nada. Solo el ordenador que hace de servidor necesita Python 3.

El servidor usa únicamente la biblioteca estándar de Python, sin dependencias externas.

## Puesta en marcha

Solo en el ordenador que hará de servidor:

1. Instala Python 3 si no lo tienes.
2. Deja `servidor_v5.py` e `index.html` en la misma carpeta.
3. **Cambia la contraseña** (ver el apartado siguiente).
4. Arranca el servidor:

   ```bash
   python servidor_v5.py
   ```

5. El servidor imprime la dirección para el navegador, por ejemplo `http://10.21.0.4:5000`.

La primera vez, Windows puede preguntar si permites el acceso por el firewall. Hay que permitirlo en redes privadas; si no, los demás no podrán entrar. Solo se pregunta en el ordenador servidor.

Para entrar desde cualquier otro equipo de la misma red, abre esa dirección en el navegador, escribe la contraseña y, si el servidor no reconoce tu IP, un nombre.

## Cambia la contraseña antes de usarlo

La contraseña de clase está escrita dentro de `servidor_v5.py`, en la constante `CONTRASENA_SERVIDOR`. **Cámbiala antes de iniciar el servidor.** Si subes tu copia a un repositorio público, no pongas ahí la contraseña real.

## Nombres de usuario

El servidor tiene una tabla `NOMBRES` que asigna un nombre a algunas IP. Quien entra desde una IP de la tabla recibe ese nombre automáticamente y nadie más puede usarlo. El resto de equipos (por ejemplo los portátiles por wifi) escriben su nombre al entrar. Edita la tabla `NOMBRES` para adaptarla a tu red.

## Qué tiene

- Chat general con todos los conectados y conversaciones privadas entre dos personas (clic en un nombre), con contador de mensajes sin leer.
- Historial: quien entra desde el navegador ve los últimos 50 mensajes de General. Solo vive en la memoria del servidor y se pierde al pararlo. Los privados no se guardan.
- Menciones con `@nombre` (con Tab se autocompleta) que se resaltan para quien las recibe.
- Mensajes de varias líneas (Shift+Enter) y bloques de código con tres comillas invertidas, con botón para copiarlos. El código en línea se escribe entre comillas invertidas simples. El límite es de 2000 caracteres por mensaje.
- Subida y descarga de archivos desde el navegador, con vista previa de texto e imágenes, arrastrando el archivo o pegando una captura con Ctrl+V. Solo `.py`, `.java`, `.txt`, `.jpg` y `.png`, hasta 10 MB. Se guardan en la carpeta `chat_archivos`, junto al servidor.
- Tema claro y oscuro, avisos configurables (todo, solo menciones y privados, ninguno) con contador en la pestaña y pitido.
- Opción de recordar la contraseña y el nombre en el navegador (desactivada por defecto, no la uses en equipos compartidos) y botón para salir.
- Reconexión automática si se corta la conexión.

### Comandos

| Comando | Qué hace |
| --- | --- |
| `/usuarios` | Muestra quién está conectado |
| `/nick nombre` | Cambia tu nombre. Si tu IP tiene nombre asignado, el formato es `NombreAsignado (nuevo)` |
| `/color #RRGGBB` | Cambia el color de tu nombre (también hay un selector en la web) |
| `/silencio` | Activa o desactiva los avisos en el navegador |
| `/ayuda` | Muestra la ayuda |

Los mensajes privados se envían haciendo clic en un nombre de la lista de conectados.

## Estructura

| Archivo | Para qué sirve |
| --- | --- |
| `servidor_v5.py` | Servidor: chat, HTTP, WebSocket y protocolo antiguo en el mismo puerto |
| `index.html` | Cliente web (HTML, CSS y JavaScript en un solo archivo, sin librerías ni recursos externos) |
| `chat_archivos/` | Archivos subidos (se crea al ejecutarlo; no se sube al repositorio) |

## Limitaciones y seguridad

- **Sin cifrado.** Todo viaja en texto plano por HTTP y WebSocket, incluida la contraseña. Vale para una red de clase de confianza; no escribas contraseñas ni datos personales.
- **Una única contraseña compartida** para toda la clase y sin cuentas. Los nombres se reservan por IP, pero eso no es autenticación.
- **No hay moderación ni borrado de mensajes.** Quien tenga la contraseña puede escribir y subir archivos.
- **El historial es temporal**, como se explica arriba.
- Lo único que se ha hecho para endurecerlo: se limita el tamaño de líneas, mensajes y archivos, se comprueba el origen de las conexiones WebSocket, los nombres de archivo se limpian, la descarga y la subida exigen un token que solo recibe quien ya ha entrado, y el navegador escapa los mensajes para que no se pueda inyectar HTML.
- Se ha probado con Chromium en Linux. Su comportamiento en otros navegadores y sistemas operativos no está comprobado de forma sistemática.

## Créditos

Basado en el chat de [Ruben-Delgado/Chat](https://github.com/Ruben-Delgado/Chat). Esta versión añade el cliente web, el soporte de WebSocket y las mejoras descritas arriba.
