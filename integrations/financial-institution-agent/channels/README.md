# Canales

El agente no depende de un canal específico. El gateway incluye un adaptador
opcional para WhatsApp Cloud API en `/channels/whatsapp/webhook`:

```text
WhatsApp Cloud API -> firma Meta -> gateway -> API de aplicación Dify
                                      |
                         session_state=PUBLIC por defecto
```

El adaptador valida la firma HMAC, deduplica `message_id`, conserva el
`conversation_id` de Dify y responde por el número institucional. No contiene
reglas del core bancario.

Cuando Dify se configura con `forward_end_user_identity`, el gateway recibe el
`external_user_id` del canal como identidad dinámica. El conector de la
institución debe relacionarlo con su cliente y comprobar si la sesión tiene
autorización privada; el número de WhatsApp por sí solo nunca es una prueba de
identidad. Para acceder a datos privados, el proxy institucional también debe
inyectar `X-Verified-Subject` y `X-Verified-Session-State: VERIFIED`; el agente
no puede fabricar esos headers.

Para producción con varias réplicas, sustituye el SQLite local por un store
compartido y conecta el estado de identidad de la institución. El webhook no
debe habilitar datos privados solo porque el remitente conozca su número de
cédula o cuenta.

La verificación de WhatsApp debe ocurrir en un flujo institucional separado
(por ejemplo, un enlace seguro o un intercambio OIDC/MFA). Después de verificar
al usuario, el canal o su broker debe obtener un JWT de corta duración con el
assurance aprobado y enviarlo al `identity-proxy`; el proxy es quien convierte
ese assurance en `X-Verified-Session-State: VERIFIED`. El agente Dify no puede
elevar por sí mismo una conversación `PUBLIC` y el relay de identidad de Dify
solo identifica al usuario, no sustituye MFA.

El broker firma el JSON original del endpoint interno
`POST /channels/whatsapp/identity` con
`X-Institution-Identity-Signature` usando
`BANKING_WHATSAPP_IDENTITY_SECRET`:

```json
{
  "sender": "593999999999",
  "subject": "cliente-interno-001",
  "state": "VERIFIED",
  "expiresAt": 1790000000
}
```

La expiración máxima aceptada es 24 horas; para revocar antes de ese plazo se
debe reemplazar el estado en el store compartido del canal.
