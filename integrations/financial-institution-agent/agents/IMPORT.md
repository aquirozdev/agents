# Publicar el agente en Dify

Esta plantilla separa la experiencia conversacional del acceso al core. Para
crear una instancia por banco o cooperativa:

1. Levanta o registra el `banking gateway` privado de la institución.
2. En Dify crea una aplicación tipo Agent/Chatflow con el prompt de
   `cliente-financiero-ecuador.md` como instrucciones del sistema.
3. Importa `../openapi.yaml` como proveedor de herramientas personalizado y
   apunta el servidor al dominio privado del gateway.
4. Configura las credenciales del proveedor usando
   `dify-api-provider.credentials.example.json`; guarda
   `api_key_value` en el gestor de secretos y no uses el token de la demo en
   producción.
5. Configura el canal y el middleware de identidad para mantener el estado
   `PUBLIC|VERIFIED|HUMAN_REVIEW`; si lo entregas al prompt, úsalo solo como
   contexto conversacional, nunca como prueba de autenticación.
6. Publica solo las capacidades que el gateway devuelve en
   `GET /v1/capabilities`; el agente no debe inventar una herramienta para una
   capacidad ausente.

La credencial estática del proveedor API de Dify sirve para la demo o para
datos públicos. Para datos privados, el proxy debe asociar cada llamada a una
sesión verificada y emitir/revalidar el contexto de identidad; no se debe usar
un token compartido que permita consultar a cualquier cliente.

El proxy debe escribir `X-Verified-Session-State: VERIFIED` únicamente después
de completar la autenticación aprobada por la institución. El gateway rechaza
las operaciones privadas sin ese header.

La aplicación Dify nunca debe generar ese header como parámetro de herramienta;
debe inyectarlo el proxy de identidad después de validar la sesión.

Si el canal invoca Dify mediante la API de aplicación, activa en el proveedor
API la opción `forward_end_user_identity=true` para contexto público. Dify
reenviará el `external_user_id` de la sesión como `X-Dify-End-User-ID` y una
firma HMAC en `X-Dify-End-User-Signature`; configura el mismo secreto en Dify y
`BANKING_DIFY_IDENTITY_SECRET`. Para datos privados, publica el OpenAPI detrás
del proxy institucional y usa `BANKING_AUTH_MODE=external-proxy`; el modo
`dify-user` por sí solo no habilita consultas privadas.

Si WhatsApp es el canal, configura `IDENTITY_PROXY_DIFY_RELAY_SECRET` con el
mismo valor de `BANKING_DIFY_IDENTITY_SECRET` y
`IDENTITY_PROXY_IDENTITY_ASSERTION_SECRET` con el mismo valor de
`BANKING_WHATSAPP_IDENTITY_SECRET`. El broker de identidad registra una sesión
verificada mediante `POST /channels/whatsapp/identity`; sin esa aserción el
proxy mantiene la sesión en `PUBLIC` aunque Dify conozca el usuario.

## Pruebas mínimas antes de habilitar clientes

- una sesión pública no puede leer perfil, cuentas, movimientos ni créditos;
- una sesión verificada solo puede acceder a recursos de su sujeto;
- una cédula, número de cuenta o `customerId` escrito en chat no cambia la
  identidad de la sesión;
- las solicitudes de cliente y crédito quedan en `pending` y no aprueban ni
  desembolsan;
- fraude, robo, pérdida y movimientos no reconocidos derivan al canal seguro;
- claves, PIN, CVV, vencimiento y OTP nunca se solicitan ni se repiten;
- el gateway oculta identificadores y datos que no sean necesarios;
- se conserva `X-Correlation-ID` para auditoría y soporte.

El proveedor OpenAPI de Dify es la interfaz estable. La implementación que
traduce esa interfaz al core vive fuera del agente y puede reemplazarse sin
rediseñar el prompt.
