# Plantilla de agentes para instituciones financieras

Esta carpeta ya no representa una integración específica con Apache Fineract. Es la plantilla reutilizable para desplegar agentes de atención de clientes en cooperativas y bancos, independientemente del core bancario que utilicen.

Fineract se conserva solamente como un core demo opcional para probar la plantilla. El agente no conoce Fineract, sus tablas, sus rutas ni sus estados internos.

## Arquitectura

```text
WhatsApp / Web / Voz / App institucional
                 |
       Proxy OIDC, MFA y sesión
                 |
          Dify: agente de clientes
                 |
       OpenAPI canónico de banca común
                 |
       Banking Gateway de la institución
                 |
  Conector del core elegido por el cliente
                 |
 Core bancario: Fineract, COBIS, Bantotal, Temenos,
 Finacle, core propio u otro
```

Dify se integra una sola vez con el contrato canónico de `openapi.yaml`. Para cada institución solo se cambia el `banking gateway`, la configuración de capacidades y las políticas. Esto evita reescribir prompts y agentes cuando cambia el core.

La plantilla incluye una extensión opt-in en Dify para que los proveedores de
herramientas API puedan reenviar el `external_user_id` de cada usuario como
identidad dinámica. Esto evita usar un sujeto compartido cuando el agente se
consume desde WhatsApp, webchat u otro canal.

## Contenido

- `openapi.yaml`: contrato canónico que debe implementar el gateway de cada institución.
- `agents/cliente-financiero-ecuador.md`: agente base para atención a clientes.
- `agents/dify-api-provider.credentials.example.json`: configuración opt-in para propagar la identidad externa de Dify.
- `institution.example.yaml`: configuración por institución y capacidades habilitadas.
- `connectors/README.md`: contrato de implementación para integrar cualquier core.
- `gateway/`: gateway ejecutable de referencia con conector demo intercambiable.
- `identity-proxy/`: proxy OIDC/JWT de referencia para validar identidad y assurance antes del gateway.
- `channels/`: adaptación de canales, incluido WhatsApp Cloud API opcional.
- `PRODUCTION_READINESS.md`: matriz de lo que ya incluye la plantilla y lo que debe aportar cada institución.
- `demo/fineract/`: entorno opcional de referencia para pruebas locales; no es la arquitectura productiva.
- `.env.example`: parámetros no sensibles del gateway de una institución.

## Cómo se instala en una institución

1. Copiar `institution.example.yaml` a la configuración de la institución y cargarlo mediante `BANKING_INSTITUTION_CONFIG`.
2. Implementar el `banking gateway` contra el core existente usando el contrato canónico.
3. Desplegar `identity-proxy` delante del gateway y conectar el IdP, consentimiento, MFA/OTP, auditoría y handoff humano.
4. Importar `openapi.yaml` en Dify apuntando al gateway privado.
5. Crear el agente usando el prompt de `agents/cliente-financiero-ecuador.md`.
6. Deshabilitar las capacidades no implementadas en la configuración; el gateway publica la intersección entre política y conector.
7. Ejecutar pruebas de contrato, seguridad, carga, conciliación y recuperación antes de habilitar clientes reales.

Para comprobar el gateway de una institución:

```bash
cp .env.example .env
set -a && source .env && set +a
./scripts/check.sh
```

Para levantar una implementación local completa del contrato, sin instalar
Fineract ni depender de ningún core:

```bash
cd gateway
uv sync
BANKING_ENVIRONMENT=development uv run uvicorn banking_gateway.main:app --app-dir src --port 8080
```

Consulta `gateway/README.md` para conectar un conector real mediante
`BANKING_CONNECTOR=custom` y `BANKING_CONNECTOR_FACTORY=module:factory`.

El agente nunca debe llamar directamente al core ni a la base de datos. El proxy de identidad valida el JWT institucional y el nivel de assurance; el gateway es responsable de autorización, normalización de datos, límites, idempotencia, trazabilidad y traducción de errores.

## Capacidades del agente

La plantilla está pensada para cubrir la experiencia de un canal bancario moderno:

- consultas de perfil, cuentas, saldos y movimientos recientes;
- consultas de tarjetas y movimientos de tarjeta, cuando la institución lo habilita;
- certificados de cuenta, saldo o tributarios, cuando la institución lo habilita;
- consulta de beneficiarios y facturadores autorizados;
- productos de ahorro y crédito;
- consulta de préstamos, cuotas y cronogramas;
- solicitudes de apertura y originación en estado pendiente;
- intenciones pendientes de pagos y transferencias, cuando la institución las habilita;
- seguimiento del estado de solicitudes e intenciones autorizadas;
- información pública de productos, requisitos y oficinas;
- derivación a asesor, reclamos, fraude y soporte.

Transferencias, pagos, certificados, geolocalización y notificaciones son módulos opcionales. No se simulan si el gateway no los declara en `/v1/capabilities`.

## Seguridad

El canal debe entregar una sesión verificada al gateway. Una cédula, número de cuenta o `customerId` escrito en el chat no prueba identidad. El agente no solicita claves, PIN, CVV ni OTP, y no puede aprobar créditos, desembolsar, cobrar, transferir, reversar o cerrar productos sin un flujo operativo con autorización humana.

La plantilla es un producto de integración y experiencia conversacional; las
intenciones de pago o transferencia no ejecutan movimientos contables y deben
pasar por el core, MFA, antifraude, aprobación y ledger de la institución. La
plantilla tampoco reemplaza el sistema de identidad ni las obligaciones
regulatorias de cada entidad en Ecuador.
