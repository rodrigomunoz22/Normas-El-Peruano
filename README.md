# Monitor de normas legales de El Peruano

Tres veces al día (8:30, 12:00 y 15:30, hora de Lima) lee las normas legales publicadas en el
[Diario Oficial El Peruano](https://diariooficial.elperuano.pe/Normas), las clasifica por
prioridad, resume las relevantes con la API de Claude y envía un correo en español.
Corre en GitHub Actions: no depende de que ninguna computadora esté encendida.

## Qué hace cada ejecución

| Hora (Lima) | Cron (UTC)    | Modo      | Qué consulta                                        | Qué envía |
|-------------|---------------|-----------|-----------------------------------------------------|-----------|
| 8:30        | `30 13 * * *` | completo  | Todo lo de ayer + todo lo de hoy                    | Todo lo de hoy, la extraordinaria de ayer y las normas ordinarias de ayer que no salieron en un correo anterior (etiqueta "Publicada ayer") |
| 12:00       | `0 17 * * *`  | novedades | Todo lo de hoy                                      | Solo las normas que no están en `data/vistas.json` |
| 15:30       | `30 20 * * *` | novedades | Todo lo de hoy                                      | Igual; si no hay novedades, no envía correo |

GitHub puede retrasar las ejecuciones programadas (15–30 minutos o más). Por eso el correo
muestra la hora real de ejecución y el modo novedades se apoya en el estado guardado, no en la hora.

Orden del correo: 1) PCM y MEF, 2) edición extraordinaria de ayer (solo 8:30), 3) prioridad
alta, 4) prioridad media (incluye gobiernos regionales), 5) instituciones con más de 5 normas en
un día, 6) prioridad baja agrupada. El pie indica cuántas normas municipales se omitieron,
cuántas normas se revisaron, la hora real (Lima y UTC) y los errores no fatales.

## Instalación

1. Sube este código a un repositorio de GitHub (rama `main`).
2. En **Settings → Secrets and variables → Actions → Secrets**, crea:

   | Secreto              | Contenido |
   |----------------------|-----------|
   | `ANTHROPIC_API_KEY`  | Clave de la API de Claude (console.anthropic.com) |
   | `GMAIL_USER`         | Cuenta de Gmail que envía el correo |
   | `GMAIL_APP_PASSWORD` | Contraseña de aplicación de esa cuenta (requiere verificación en dos pasos) |
   | `MAIL_TO`            | Destinatario; varios separados por comas |

   Ningún secreto va en el código. El envío usa SMTP de Gmail (`smtp.gmail.com:465`), el mismo
   mecanismo del monitor de proyectos de ley; aquí los secretos tienen otros nombres
   (`GMAIL_*` y `MAIL_TO` en vez de `EMAIL_*`).
3. (Opcional) En **Variables**, crea `CLAUDE_MODEL` para elegir el modelo. Si no existe se usa
   `claude-opus-5-5`. Un modelo más barato (por ejemplo `claude-haiku-5-5`) reduce el costo.
4. En **Settings → Actions → General → Workflow permissions**, marca *Read and write
   permissions* (el workflow guarda el estado con un commit).
5. Pestaña **Actions** → *Paso 0 - prueba de conexión* → **Run workflow**. Debe imprimir cuántas
   normas obtuvo. Si falla, el sitio no responde desde los servidores de GitHub y el resto no
   funcionará.

## Pruebas manuales

Desde **Actions → Resumen de normas de El Peruano → Run workflow**:

- `fecha` (dd/mm/aaaa, opcional), `modo` (completo o novedades) y `enviar`.
- Con `enviar` desmarcado no se envía nada y el correo queda como artefacto (`correo-de-prueba`).
- Las pruebas con `fecha` distinta de hoy o sin envío **no** modifican el estado.

Desde la terminal (Python 3.12):

```bash
pip install -r requirements-dev.txt
```

```bash
python -m normas --fecha 10/11/2026 --modo completo --dry-run
```

```bash
python -m pytest
```

`--dry-run` imprime el correo y no envía nada. Sin `ANTHROPIC_API_KEY` en el entorno, el dry-run
muestra la sumilla del listado en lugar del resumen de Claude y lo indica.

## Cómo editar las reglas

Todo está en [`config/reglas.yml`](config/reglas.yml); no hace falta tocar código:

- `entidades_prioritarias`: entidades de la primera sección (PCM y MEF).
- `municipalidades.excepciones`: municipalidades que **no** se excluyen. Valor inicial:
  Municipalidad Metropolitana de Lima. **Confirma si quieres esa y agrega o quita otras.**
- `alta`, `media`, `baja`: tipos de norma y expresiones que definen cada prioridad.
  - `baja.gana_sobre_media: true` hace que una Resolución Ministerial de designación o de viaje
    vaya a prioridad baja aunque por tipo sería media.
  - `baja.gana_sobre_alta: false` mantiene las Resoluciones Supremas en alta aunque sean
    designaciones. Cámbialo a `true` si prefieres verlas agrupadas en baja.
- `concentracion.umbral`: una entidad se reporta aparte si emite **más** de este número en un día.
- `novedades.enviar_sin_novedades`: `true` para recibir correo aunque no haya normas nuevas.
- `resumenes`: normas por llamada a Claude, límite de caracteres y de textos por ejecución.

Las comparaciones ignoran tildes y mayúsculas. Después de editar, corre `python -m pytest`.

## Resúmenes y regla de no inventar

- Claude solo recibe el texto publicado de cada norma y tiene instrucción de no agregar contexto
  ni cifras que no estén en ese texto, y de declarar cuando el texto es insuficiente.
- Solo se resumen las normas de la sección PCM/MEF, la extraordinaria de ayer, alta y media. Las
  de prioridad baja (y las designaciones de PCM/MEF) muestran la sumilla del listado en una línea.
- Si no se pudo obtener el texto completo, no se llama a Claude: se muestra la sumilla del
  listado con la nota "Resumen basado solo en el título y la sumilla del listado".
- Si el texto supera `max_caracteres_por_norma` se corta y el correo lo indica.
- Los resúmenes hechos se guardan en `data/resumenes.json` para no repetir llamadas.
- Un resumen automático puede contener errores: cada norma lleva su enlace a la fuente.

## Fallas

No hay fallos silenciosos. Si el sitio no responde, cambia la estructura del HTML, falta un
secreto, falla el envío o fallan los resúmenes de Claude, el workflow termina en rojo y un paso
final envía un correo de aviso con el enlace a la ejecución. (Si lo que falla es el propio Gmail,
ese aviso tampoco saldrá; queda la notificación de GitHub por workflow fallido.)
Cuando fallan solo los resúmenes, el correo se envía igual con las sumillas del listado y la
ejecución se marca como fallida.

## Estado y keepalive

- `data/vistas.json` guarda las claves de las normas ya enviadas (últimos 10 días). La clave es
  el id del enlace (`.../dispositivo/NL/<id>`); si la norma no tiene ese id, un hash de entidad +
  tipo y número + fecha.
- El estado se actualiza solo después de enviar el correo. Si el commit falla, la siguiente
  ejecución puede repetir normas, pero no perderlas.
- `keepalive.yml` hace un commit semanal para que GitHub no desactive la programación por
  inactividad (60 días).

## Uso del sitio y términos de uso

**Revisa los términos de uso de El Peruano antes de dejar esto en producción**; este proyecto no
los ha verificado. Lo que sí se comprobó el 11/11/2026 es el `robots.txt`: el de
`diariooficial.elperuano.pe` no excluye `/Normas/LoadNormasLegales` ni `/Normas/Filtro`, y el de
`busquedas.elperuano.pe` permite todo (`Allow: /`). Eso no equivale a una autorización.

Para cargar poco al sitio, el monitor:

- se identifica con un User-Agent propio (`monitor-normas-elperuano/1.0 ...`);
- hace como máximo 2 consultas al listado por ejecución (tope de 3), con timeout de 45 s y hasta
  2 reintentos con espera creciente;
- pide además el texto completo de cada norma **a resumir** en `busquedas.elperuano.pe`
  (una petición por norma, con 1 s de pausa, tope de 40 por ejecución, sin repetir las ya
  resumidas). Si quieres limitarte estrictamente a 3 peticiones por ejecución, pon
  `resumenes.max_textos_por_ejecucion: 0`: los resúmenes saldrán solo de la sumilla del listado.

## Estructura

```
normas/        parser.py, fuente.py, filtros.py, prioridades.py, resumen.py, correo.py, estado.py, __main__.py
config/        reglas.yml
data/          vistas.json, resumenes.json
scripts/       paso0_conexion.py
tests/         fixtures/ (respuestas HTML reales) y test_normas.py
.github/workflows/  paso0-conexion.yml, resumen.yml, keepalive.yml, pruebas.yml
```
