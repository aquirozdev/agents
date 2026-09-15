# Proxy de identidad OIDC

Este servicio opcional se coloca delante del `banking-gateway`. Valida el JWT
emitido por el proveedor OIDC de la institución, elimina cualquier cabecera
confiable enviada por el cliente y escribe `X-Verified-Subject` y
`X-Verified-Session-State` únicamente con información derivada del token
verificado. El gateway recibe además un bearer interno distinto del token del
cliente.

El valor de `IDENTITY_PROXY_VERIFIED_ASSURANCE_VALUES` debe representar el nivel
de autenticación que la institución considera equivalente a MFA. Un token válido
con assurance insuficiente se reenvía como `PUBLIC`, por lo que las rutas
privadas continúan bloqueadas.

## Configuración mínima

```bash
IDENTITY_PROXY_ENVIRONMENT=production
IDENTITY_PROXY_ISSUER=https://idp.example.com/realms/cooperative
IDENTITY_PROXY_JWKS_URL=https://idp.example.com/realms/cooperative/protocol/openid-connect/certs
IDENTITY_PROXY_AUDIENCE=banking-agent
IDENTITY_PROXY_ALGORITHMS='["RS256"]'
IDENTITY_PROXY_GATEWAY_BASE_URL=http://banking-gateway:8080
IDENTITY_PROXY_GATEWAY_TOKEN=secret-manager-value
IDENTITY_PROXY_INSTITUTION_ID=cooperative-demo
IDENTITY_PROXY_VERIFIED_ASSURANCE_VALUES='["mfa","urn:institution:mfa"]'
```

Para el canal WhatsApp integrado con Dify, configura además un relay firmado:

```bash
IDENTITY_PROXY_DIFY_RELAY_SECRET=the-same-secret-as-BANKING_DIFY_IDENTITY_SECRET
IDENTITY_PROXY_IDENTITY_ASSERTION_SECRET=the-same-secret-as-BANKING_AUTH_IDENTITY_SECRET
```

La primera firma demuestra que Dify transportó la identidad persistida y la
segunda demuestra que el broker institucional marcó la sesión como verificada.
Sin la segunda firma el relay siempre se clasifica como `PUBLIC`.

Construcción y ejecución:

```bash
docker build -t financial-institution-identity-proxy .
docker run --read-only --security-opt no-new-privileges:true \
  -p 8081:8081 --env-file .env financial-institution-identity-proxy
```

Publica el mismo contrato OpenAPI a través del proxy (`https://agent.example.com`)
y conserva el gateway en una red privada. El proxy no resuelve clientes ni
consulta el core; esa responsabilidad continúa en el conector institucional.

Antes de producción, valida con el equipo de seguridad la configuración del IdP,
la expiración y revocación de tokens, el mapeo del sujeto a cliente, la
clasificación de assurance y las políticas de logs. Nunca registres JWTs ni
cabeceras de autorización.
