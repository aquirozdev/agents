# Demo comercial del agente financiero

Esta demo representa el flujo de producción:

```text
Chat Web de Dify
      |
Modelo Cloudflare Workers AI
      |
Herramientas OpenAPI
      |
Banking Gateway
      |
Conector demo o Fineract
```

La conversación usa datos sintéticos. Para una presentación comercial se
recomienda iniciar con `BANKING_CONNECTOR=demo`, porque permite mostrar la
experiencia completa. Fineract queda disponible para demostrar que el gateway
puede cambiar de core sin cambiar el agente.

## 1. Levantar el gateway de la demo

Si Dify ya está levantado con el Compose del repositorio, usa este archivo
para conectar ambos servicios a la red Docker de Dify:

```bash
docker compose \
  -f integrations/financial-institution-agent/demo/docker-compose.dify.yaml \
  up --build -d
```

En ese caso, el proveedor OpenAPI de Dify debe apuntar a:

```text
http://banking-gateway:8080
```

Si Dify está fuera de Docker, usa el Compose del gateway y un hostname
accesible desde Dify, como se explica en la sección siguiente.

```bash
cd integrations/financial-institution-agent/gateway
docker compose up --build
```

El gateway queda en `http://localhost:8080` y usa:

- token: `local-demo-token`;
- cliente: `customer-demo-001`;
- cuenta: `account-demo-001`;
- crédito: `loan-demo-001`.

Verificación rápida:

```bash
curl http://localhost:8080/v1/health
curl -H 'Authorization: Bearer local-demo-token' \
  http://localhost:8080/v1/capabilities
```

No uses estos valores fuera de la demo.

## 2. Publicar el gateway para Dify

Si Dify está en otra máquina o en Cloudflare, publica únicamente el gateway
mediante un hostname de Cloudflare Tunnel. El origen debe continuar siendo el
gateway; no publiques Fineract.

Para una prueba temporal:

```bash
cloudflared tunnel --url http://localhost:8080
```

Para la demo inicial, el túnel puede ir sin Access porque el gateway ya exige
`Authorization: Bearer local-demo-token`. En producción añade Cloudflare
Access, pero conserva una autorización separada para el gateway: no sustituyas
su bearer por el service token de Access. Si Dify no puede enviar ambos
headers, coloca un proxy de borde que valide Access y añada el bearer interno
antes de reenviar al gateway.

## 3. Configurar Cloudflare como modelo en Dify

En la instancia local de Dify ya está instalado el plugin de Cloudflare Workers
AI. La configuración compatible con esta demo es:

```text
Base URL: https://api.cloudflare.com/client/v4/accounts/<ACCOUNT_ID>/ai/v1
API key: token de Workers AI
Modelo: @cf/meta/llama-3.3-70b-instruct-fp8-fast
```

El modelo debe tener habilitado function calling para que pueda invocar las
herramientas bancarias. Si el modelo elegido no llama herramientas, el agente
solo responderá texto y no consultará el gateway.

El token debe ser el token crudo, sin anteponer `Bearer`, y debe estar creado
para la misma cuenta con permisos `Workers AI - Read` y `Workers AI - Edit`.
Cloudflare documenta esos permisos para tokens creados manualmente. Si Dify
devuelve `Authentication error` (HTTP 401), revisa también que el Account ID
pertenezca a esa cuenta; reemplaza la credencial en el proveedor y vuelve a
probar el chat.

## 4. Crear la aplicación en Dify

1. Crea una aplicación pública tipo Agent o Chatflow.
2. Usa como instrucciones `../agents/publico-financiero-ecuador.md`.
3. Reemplaza `{{INSTITUTION_NAME}}` por `Cooperativa Demo`.
4. Importa `../openapi-public.yaml` y configura `Authorization: Bearer local-public-token`.
5. Crea una aplicación separada para clientes autenticados.
6. Usa `../agents/cliente-financiero-ecuador.md` e importa `../openapi-customer.yaml`.
7. Configura `Authorization: Bearer local-demo-token` en el proveedor autenticado.
8. Publica ambas aplicaciones como Web Apps. El canal WhatsApp selecciona la aplicación automáticamente según la sesión.

En el Compose local, el proxy SSRF de Dify debe permitir únicamente la IP o el
dominio interno del gateway. El `docker/.env` de esta demo ya permite el
gateway actual (`172.21.0.15` y `banking-gateway`); si se recrean los
contenedores y cambia la IP, actualiza ese valor y recrea `ssrf_proxy`.

La aplicación pública no ve ningún cliente. La aplicación autenticada usa el
cliente sintético `customer-demo-001` solo después de completar el flujo local
de OTP; eso permite mostrar la experiencia sin exponer datos reales.
Para el OTP local usa `+593 999 292 849` y cualquier código de seis dígitos.

## 5. Guion de presentación

Prueba estas conversaciones en orden:

1. `¿Cuál es mi saldo disponible?`
2. `Muéstrame mis últimos movimientos.`
3. `¿Cuánto debo de mi crédito y cuándo es la próxima cuota?`
4. `¿Qué productos de ahorro tienen?`
5. `Quiero solicitar un crédito de 1.000 dólares a 12 meses.`

La última debe crear una solicitud `pending`; nunca debe aprobar ni
desembolsar el crédito.

También demuestra estos controles:

- pedir información de otro cliente debe ser rechazado;
- el agente nunca debe pedir PIN, CVV, contraseña ni OTP;
- transferencias y pagos deben aparecer como no habilitados;
- fraude, robo o movimientos no reconocidos deben derivarse a un canal seguro.

## 6. Cambiar a Fineract

Para demostrar el reemplazo de core:

```bash
cd integrations/financial-institution-agent/demo/fineract
docker compose \
  -f docker-compose.yaml \
  -f docker-compose.gateway.yaml \
  up --build
```

Después ejecuta `./seed-demo.sh`, usa el identificador de cliente que imprime
el script y cambia el servidor de la herramienta en Dify al gateway de esta
composición. El agente y el contrato OpenAPI no cambian.

Fineract es una prueba de integración de lectura. Para una institución real,
se reemplaza ese conector por el adaptador del core del cliente.

## 7. Estado de la instancia local preparada

Con el Compose de Dify y el gateway demo levantados, la aplicación preparada
queda disponible en:

```text
Consola Dify: http://127.0.0.1/
Web App:      http://127.0.0.1/chat/mjxFVZfYjIhnFcvM
Gateway:      http://127.0.0.1:8080
```

La cuenta administrativa local usada para preparar la demo es
`aquirozdev@gmail.com`, con la contraseña temporal entregada al responsable
del entorno. Cámbiala después de la presentación.
