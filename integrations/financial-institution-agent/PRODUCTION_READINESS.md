# Matriz de preparación para producción

La plantilla implementa el plano común del agente. Antes de atender clientes
reales, el integrador debe cerrar los elementos que dependen de la institución.

| Área                  | Implementado en la plantilla                                                                                           | A completar por la institución                                                                                                  |
| --------------------- | ---------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| Contrato de datos     | OpenAPI canónico, modelos y errores                                                                                    | Validar mapeos contra el core real                                                                                              |
| Gateway               | FastAPI, límites conversacionales, capacidades, correlación e idempotencia                                             | Desplegar en red privada y configurar observabilidad                                                                            |
| Core                  | Demo en memoria, conector opcional Fineract, `canonical-http` para BFFs y puerto `custom`                              | Implementar autorización y mapeo para COBIS, Bantotal, Temenos, core propio, etc., o publicar el contrato canónico desde el BFF |
| Identidad             | Proxy OIDC/JWT de referencia, `external-proxy` para datos privados, `dify-user` con relay opt-in para contexto público | Configurar el IdP real, resolución sujeto-cliente, revocación, scopes y assurance aprobado por seguridad                        |
| Dify                  | Agente público y agente autenticado con OpenAPI, credenciales y relay de identidad separados                           | Crear/publicar ambas aplicaciones y elegir modelo/proveedor                                                                     |
| Canales               | WhatsApp Cloud API con firma, conversaciones aisladas por perfil y deduplicación                                       | Secrets de Meta, store compartido/cola y handoff humano                                                                         |
| Operaciones sensibles | Pagos y transferencias solo como intenciones `pending`; no hay ejecución contable                                      | Implementar MFA, antifraude, límites, maker-checker, aprobación y ledger                                                        |
| Ecuador               | Locale `es-EC`, USD, máscara y guardas conversacionales                                                                | Cumplimiento, privacidad, reclamos, retención y políticas de la entidad                                                         |
| Continuidad           | Healthcheck, Compose y errores normalizados                                                                            | Backups, DR, carga, alertas, SLO y reconciliación                                                                               |

Una capacidad solo se publica cuando está habilitada por la política
institucional y por el conector. La demo no representa un ledger ni debe
recibir datos reales.
