# Contrato para conectores de core

Un conector es responsabilidad de la institución o de su integrador de core. Su función es implementar el contrato canónico del gateway y traducirlo al core que exista en el cliente.

## Reglas del conector

- No exponer credenciales del core a Dify.
- No permitir que el modelo construya URLs, SQL o comandos del core.
- Validar tenant, institución, sesión, scopes y relación entre el cliente y cada recurso.
- Rechazar datos privados si `AuthContext.session_state` no es `VERIFIED`,
  incluso si el identificador del sujeto parece válido.
- Normalizar fechas a ISO 8601 y montos a números decimales con moneda explícita.
- Mantener los identificadores internos del core fuera de la conversación cuando no sean necesarios.
- Aplicar idempotency keys en toda operación que cree solicitudes o produzca efectos.
- Persistir también el `request_fingerprint` recibido en `AuthContext`; si una
  clave se reutiliza con otro payload, responder con conflicto y no crear una
  segunda solicitud.
- Si se usa `canonical-http`, el gateway propaga ese valor como
  `X-Request-Fingerprint` para que el BFF institucional pueda aplicar la misma
  garantía en su store transaccional.
- Traducir errores del core a `application/problem+json` sin filtrar trazas ni datos sensibles.
- Registrar `correlationId`, actor, sesión, operación, resultado y versión del conector.
- Propagar `AuthContext.correlation_id` al core o al BFF y conservarlo en cada
  llamada de auditoría y conciliación.
- Resolver `AuthContext.subject` a la identidad interna del cliente cuando el
  sujeto provenga de WhatsApp, OIDC o Dify; las rutas `/v1/me/*` nunca deben
  exigir que el modelo conozca el identificador del core.
- Publicar `/v1/capabilities` para que el agente no ofrezca funciones no disponibles.
- Tratar `createTransfer` y `createPayment` como intenciones pendientes: el
  conector debe delegar MFA, antifraude, límites, aprobación y ejecución al
  flujo transaccional institucional.
- Implementar `getOperationStatus` desde el sistema de solicitudes o workflow
  institucional, sin consultar directamente datos que pertenezcan a otro
  sujeto.

## Mapeo mínimo

| Contrato canónico | Ejemplo de origen posible                          |
| ----------------- | -------------------------------------------------- |
| `Customer`        | cliente, socio, asociado, titular                  |
| `Account`         | cuenta de ahorros, cuenta corriente, cuenta básica |
| `Transaction`     | movimiento, transacción, asiento publicado         |
| `Loan`            | crédito, préstamo, operación de cartera            |
| `Product`         | producto de ahorro o crédito                       |
| `Application`     | solicitud de afiliación, apertura u originación    |
| `TransferRequest` | orden de transferencia pendiente                   |
| `PaymentRequest`  | orden de pago pendiente                            |

El conector puede estar implementado como un servicio independiente, un módulo del BFF institucional o un gateway de integración. El contrato no exige Java, Python, Node, Fineract ni una tecnología concreta.

Si el BFF institucional ya expone este contrato, el gateway incluye el modo
`BANKING_CONNECTOR=canonical-http` para reenviar las operaciones con headers de
identidad y correlación. Si el BFF usa otro contrato, implementa el puerto
`custom` y conserva las mismas reglas de autorización.

El gateway valida la presencia de todos los métodos del puerto y ejecuta
`capabilities()` durante el arranque. Un conector incompleto no debe llegar a
producción ni fallar solamente cuando un cliente invoque una herramienta.
