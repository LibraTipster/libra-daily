# Libra · imagen diaria para Telegram

Cada día a las 4:30 PM (Lima) genera una imagen con el diseño oscuro y dorado de Libra Tipster, con 3 picks de mañana
como abreboca ("+ N picks más… solo para miembros") y la invitación a entrar al área de miembros LibraTips Premium.
La envía al canal de Telegram con el bot. Lee los picks directamente de Notion (Propuestas Claude).

## Configuración (una sola vez)
1. **Notion**: notion.so/profile/integrations → Nueva integración (interna) → copia el token. Abre la base
   *Propuestas Claude* → "···" → Conexiones → agrega esa integración (solo lectura basta).
2. **Telegram**: token del bot (BotFather) e id del canal (negativo, tipo -100…). El bot debe ser administrador
   del canal con permiso de publicar.
3. Aquí en el repo: Settings → Secrets and variables → Actions → New repository secret. Crea
   `NOTION_TOKEN`, `BOT_TOKEN`, `CHANNEL_ID` y, cuando tengas el enlace, `MEMBERS_URL`.
4. Prueba: pestaña Actions → "Imagen diaria Libra" → Run workflow con dry_run = 1. Al terminar, descarga la imagen
   en "Artifacts" y revísala. Con dry_run = 0 la envía de verdad al canal.

Desde entonces corre sola todos los días. Si un día no hay picks Normal con probabilidad y cuota, no envía nada.
