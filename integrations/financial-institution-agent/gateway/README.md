# Banking Gateway ejecutable

Este servicio es la implementación de referencia del contrato canónico de
`../openapi.yaml`. Está diseñado para que Dify hable siempre con el mismo API,
mientras cada institución reemplaza `BankingConnector` por un adaptador de su
core, middleware o sistema propio.

## Ejecutar la demo

Requiere Python 3.11+ y `uv`:

```bash
cd integrations/financial-institution-agent/gateway
uv sync
BANKING_ENVIRONMENT=development uv run uvicorn banking_gateway.main:app --app-dir src --reload --port 8080
```

También se puede iniciar con Docker Compose:

```bash
docker compose up --build
```

Para usar la configuración de una institución, copia
`../institution.example.yaml` a un archivo privado, define
`BANKING_INSTITUTION_CONFIG=/ruta/institution.yaml` y monta ese archivo en el
contenedor. Sus capacidades se combinan con las que soporte el conector; una
función solo aparece cuando ambas capas la habilitan.

La demo usa el token `local-demo-token` y los siguientes datos de prueba:

- cliente: `customer-demo-001`;
- cuenta: `account-demo-001`;
- crédito: `loan-demo-001`.

Ejemplo:

```bash
curl http://localhost:8080/v1/health
curl -H 'Authorization: Bearer local-demo-token' \
  http://localhost:8080/v1/customers/customer-demo-001/accounts
```

Los datos son sintéticos. Este conector no es un ledger ni debe recibir datos
reales.

## Pruebas

```bash
uv sync --extra test
uv run pytest -q
```

Las pruebas cubren el contrato demo, autorización por recurso, sesiones
verificadas, firmas HMAC de identidad, idempotencia, deduplicación de WhatsApp
y respuestas `application/problem+json`.

## Canal WhatsApp opcional

El mismo gateway incluye un adaptador para WhatsApp Cloud API. Actívalo solo
cuando estén configurados el secreto de verificación, la firma de Meta, el
token del teléfono y la API de Dify:

```bash
BANKING_WHATSAPP_ENABLED=true
BANKING_WHATSAPP_VERIFY_TOKEN='secret-manager-value'
BANKING_WHATSAPP_APP_SECRET='secret-manager-value'
BANKING_WHATSAPP_IDENTITY_SECRET='secret-manager-value'
BANKING_WHATSAPP_ACCESS_TOKEN='secret-manager-value'
BANKING_WHATSAPP_PHONE_NUMBER_ID='phone-number-id'
BANKING_DIFY_BASE_URL='https://dify.example.com'
BANKING_DIFY_API_KEY='app-api-key'
```

Configura en Meta el webhook `https://gateway.example.com/channels/whatsapp/webhook`.
El adaptador valida `X-Hub-Signature-256`, conserva el `conversation_id` de
Dify en SQLite y envía cada mensaje al agente con `session_state=PUBLIC`. Para
consultas privadas, el broker puede registrar la sesión firmando un `POST
/channels/whatsapp/identity` con `BANKING_WHATSAPP_IDENTITY_SECRET`. El
adaptador genera una aserción corta que el `identity-proxy` valida antes de
inyectar `VERIFIED`; nunca se solicita una clave, PIN, CVV u OTP por este
adaptador. En varias réplicas, reemplaza SQLite por un store compartido y añade
deduplicación persistente por `message_id` antes de producción.

## Flujo local de autenticación

El gateway incluye una página segura en `/channels/auth` para ensayar el
recorrido de web y WhatsApp. `POST /channels/auth/start` resuelve un cliente
por celular, correo o identificador externo usando el conector institucional;
`POST /channels/auth/verify-otp` crea una sesión `VERIFIED`; y los endpoints
`/biometric/start` y `/biometric/complete` realizan el step-up del dispositivo.

En `BANKING_AUTH_VERIFICATION_MODE=local-acceptance`, cualquier código de seis
dígitos y cualquier assertion no vacío son aceptados para que la presentación
sea reproducible. Este modo se rechaza durante el arranque en producción. La
integración productiva debe reemplazarlo por el proveedor OTP de la institución
y un verificador WebAuthn/passkey, conservando el mismo contrato de sesión.

El seed de Fineract registra los celulares de los clientes y el conector los
resuelve sin pedir `customerId` al usuario. Configura
`BANKING_AUTH_BASE_URL` con la URL pública del gateway cuando WhatsApp deba
abrir el enlace desde un teléfono.

## Conectar una institución real

Implementa `BankingConnector` en un paquete privado del integrador y expón una
fábrica `module:factory` que reciba `Settings`:

```bash
BANKING_CONNECTOR=custom
BANKING_CONNECTOR_FACTORY=mi_institucion.connector:create_connector
```

```python
from banking_gateway.ports import BankingConnector
from banking_gateway.settings import Settings

def create_connector(settings: Settings) -> BankingConnector:
    return MiCoreConnector(settings)
```

Si la institución ya dispone de un BFF que implementa este mismo OpenAPI, se
puede usar el conector `canonical-http` sin duplicar el mapeo del core:

```bash
BANKING_CONNECTOR=canonical-http
BANKING_UPSTREAM_BASE_URL=https://bff.institucion.internal
BANKING_UPSTREAM_TOKEN=secret-manager-value
```

El conector usa rutas fijas del contrato, valida todas las respuestas con los
modelos canónicos y propaga sujeto, estado verificado, correlación e
idempotencia. No acepta URLs construidas por el agente.

El conector debe traducir el core y hacer, como mínimo:

- autorización por institución, sujeto y recurso;
- normalización de fechas, moneda, estados y montos;
- límites, timeouts, reintentos e idempotencia;
- auditoría con `X-Correlation-ID`;
- manejo de errores como `application/problem+json`;
- controles de disponibilidad y reconciliación.

### Identidad

En desarrollo se permite `BANKING_AUTH_MODE=static-demo`. En producción usa
`BANKING_AUTH_MODE=external-proxy` para datos privados; `dify-user` queda
disponible para contexto público o para una implementación institucional que
reemplace `TokenVerifier`.
En el primer modo un proxy privado valida el JWT, limpia y vuelve a escribir el
header configurado en `BANKING_VERIFIED_SUBJECT_HEADER`, y el gateway no queda
expuesto directamente a Internet. En `dify-user`, el proveedor API de Dify
debe tener activado `forward_end_user_identity` y compartir un secreto HMAC;
el gateway exige el Bearer estático del proveedor, el header dinámico
`X-Dify-End-User-ID` y su firma `X-Dify-End-User-Signature`. El conector
institucional debe resolver ese sujeto y comprobar su estado de verificación
antes de entregar datos privados. Para otro esquema, reemplaza `TokenVerifier`
por la validación OIDC/JWT aprobada por el equipo de seguridad.

Las rutas privadas además requieren `X-Verified-Session-State: VERIFIED`.
Ese header solo debe ser escrito por el proxy de identidad de la institución
después de validar MFA o el mecanismo equivalente; nunca debe venir de un
campo de texto del usuario ni de una instrucción del agente. El modo
`dify-user` por sí solo identifica la sesión de Dify, pero no prueba la
identidad bancaria, por lo que las consultas privadas deben pasar por ese
proxy o por un `TokenVerifier` institucional.

Por diseño, `dify-user` sin un verificador institucional solo puede usarse
para capacidades públicas. No se debe intentar enviar el header desde el
modelo o desde parámetros del usuario para saltar esta protección.

No se deben poner credenciales del core, secretos de base de datos ni claves
de clientes en Dify.
