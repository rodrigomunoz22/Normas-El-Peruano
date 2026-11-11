"""Resúmenes con la API de Claude.

Reglas de fondo:
  - Claude solo recibe el texto obtenido de El Peruano y solo puede usar ese texto.
  - Si no hay texto completo, NO se llama a Claude: se muestra la sumilla del
    listado tal cual y se marca "Resumen basado solo en el título".
  - Los resúmenes hechos se guardan (data/resumenes.json) para no repetir llamadas.
"""
from __future__ import annotations

import json
import os
from datetime import date

from .prioridades import Item

# Identificador vigente según la documentación de modelos de la API de Claude
# (consultada el 11/11/2026). Se cambia con la variable de entorno CLAUDE_MODEL.
MODELO_POR_DEFECTO = "claude-opus-5-5"

NOTA_SOLO_TITULO = ("Resumen basado solo en el título y la sumilla del listado "
                    "(no se pudo leer el texto de la norma).")
NOTA_TEXTO_PARCIAL = "Resumen basado en un texto parcial (la norma excede el límite configurado)."
NOTA_INSUFICIENTE = "El texto disponible no alcanza para un resumen completo."
NOTA_SIN_CLAUDE = "Sin resumen de Claude: se muestra la sumilla del listado."

INSTRUCCIONES = """\
Eres un asistente que resume normas legales peruanas publicadas en el Diario Oficial El Peruano.

Recibirás un JSON con varias normas. Cada una trae "clave", "entidad", "titulo", "sumilla" y \
"texto" (el texto publicado; puede estar cortado si "texto_cortado" es true).

Para cada norma escribe un resumen en español de 2 o 3 líneas (como máximo 80 palabras) que \
diga qué dispone la norma y a quién o a qué se aplica.

Según el contenido de la norma, el resumen debe incluir además:
- Designaciones, renuncias, encargos y ceses: el nombre completo de cada persona, el cargo y \
la entidad; quién renuncia o deja el cargo y quién lo asume; y la fecha desde la que rige, si \
el texto la indica.
- Autorizaciones de viaje: quiénes viajan (nombre y cargo), el destino, las fechas, el motivo \
y los montos de pasajes y de viáticos tal como aparecen en el texto, con su moneda, y quién \
cubre los gastos. Si el texto no indica algún monto, o dice que el viaje no irroga gastos al \
Estado, dilo expresamente; no calcules totales que el texto no traiga.

Reglas obligatorias:
- Usa únicamente la información del texto entregado para esa norma. Copia nombres y montos \
exactamente como están escritos.
- No agregues contexto, antecedentes, interpretaciones, cifras, fechas, nombres ni montos que \
no estén en ese texto.
- Si el texto no permite saber qué dispone la norma, o está cortado antes de la parte \
resolutiva, dilo en el resumen y marca "texto_insuficiente": true. No rellenes con suposiciones.
- No des opiniones ni recomendaciones.
- Devuelve exactamente una entrada por cada clave recibida, con la misma clave."""

ESQUEMA = {
    "type": "object",
    "properties": {
        "resumenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "clave": {"type": "string"},
                    "resumen": {"type": "string"},
                    "texto_insuficiente": {"type": "boolean"},
                },
                "required": ["clave", "resumen", "texto_insuficiente"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["resumenes"],
    "additionalProperties": False,
}


class ErrorDeResumen(Exception):
    pass


def modelo_configurado() -> str:
    return os.environ.get("CLAUDE_MODEL", "").strip() or MODELO_POR_DEFECTO


def validar_respuesta(texto_json: str, claves_esperadas: list[str]) -> dict[str, dict]:
    """Valida el JSON devuelto por el modelo. Lanza ErrorDeResumen si no cumple."""
    try:
        datos = json.loads(texto_json)
    except (TypeError, ValueError) as e:
        raise ErrorDeResumen(f"La respuesta no es JSON válido: {e}") from e
    lista = datos.get("resumenes") if isinstance(datos, dict) else None
    if not isinstance(lista, list):
        raise ErrorDeResumen("La respuesta no trae la lista 'resumenes'.")
    salida: dict[str, dict] = {}
    for e in lista:
        if not isinstance(e, dict):
            raise ErrorDeResumen("Hay una entrada que no es un objeto.")
        clave, resumen = e.get("clave"), e.get("resumen")
        if not isinstance(clave, str) or not isinstance(resumen, str) or not resumen.strip():
            raise ErrorDeResumen("Hay una entrada sin clave o sin resumen.")
        if clave not in claves_esperadas:
            raise ErrorDeResumen(f"La respuesta trae una clave que no se pidió: {clave}")
        if clave in salida:
            raise ErrorDeResumen(f"La respuesta repite la clave {clave}.")
        salida[clave] = {"resumen": " ".join(resumen.split()),
                         "texto_insuficiente": bool(e.get("texto_insuficiente"))}
    faltan = [c for c in claves_esperadas if c not in salida]
    if faltan:
        raise ErrorDeResumen(f"Faltan resúmenes para: {', '.join(faltan)}")
    return salida


def _llamar_claude(cliente, modelo: str, lote: list[dict]) -> str:
    """Una llamada a la API con salida estructurada (JSON validado por esquema)."""
    respuesta = cliente.messages.create(
        model=modelo,
        max_tokens=16000,
        system=INSTRUCCIONES,
        output_config={"format": {"type": "json_schema", "schema": ESQUEMA}},
        messages=[{"role": "user",
                   "content": json.dumps({"normas": lote}, ensure_ascii=False)}],
    )
    if respuesta.stop_reason == "refusal":
        raise ErrorDeResumen("El modelo rechazó la solicitud (stop_reason=refusal).")
    if respuesta.stop_reason == "max_tokens":
        raise ErrorDeResumen("La respuesta se cortó por límite de tokens.")
    texto = next((b.text for b in respuesta.content if b.type == "text"), None)
    if texto is None:
        raise ErrorDeResumen("La respuesta no trae texto.")
    return texto


def resumir(items: list[Item], textos: dict[str, str], cache: dict[str, dict], hoy: date,
            reglas: dict, cliente=None, usar_claude: bool = True) -> list[str]:
    """Completa item.resumen e item.nota_resumen. Devuelve la lista de errores no fatales.

    textos: {clave: texto completo} de las normas cuyo texto se pudo obtener.
    cache:  resúmenes ya hechos; se actualiza con los nuevos.
    """
    cfg = reglas.get("resumenes", {})
    por_llamada = max(1, int(cfg.get("normas_por_llamada", 6)))
    max_car = int(cfg.get("max_caracteres_por_norma", 20000))
    modelo = modelo_configurado()
    errores: list[str] = []
    pendientes: list[tuple[Item, dict]] = []

    for item in items:
        n = item.norma
        guardado = cache.get(n.clave)
        if guardado:
            item.resumen, item.nota_resumen = guardado["resumen"], guardado.get("nota", "")
            continue
        texto = textos.get(n.clave, "")
        if not texto:
            item.resumen, item.nota_resumen = n.sumilla, NOTA_SOLO_TITULO
            continue
        if not usar_claude:
            item.resumen, item.nota_resumen = n.sumilla, NOTA_SIN_CLAUDE
            continue
        cortado = len(texto) > max_car
        pendientes.append((item, {
            "clave": n.clave, "entidad": n.entidad, "titulo": n.titulo, "sumilla": n.sumilla,
            "texto": texto[:max_car], "texto_cortado": cortado,
        }))

    if pendientes and cliente is None:
        import anthropic  # se importa aquí para que las pruebas no necesiten la clave
        cliente = anthropic.Anthropic()

    for i in range(0, len(pendientes), por_llamada):
        grupo = pendientes[i:i + por_llamada]
        claves = [d["clave"] for _, d in grupo]
        resultado, ultimo = None, None
        for _intento in range(2):  # una repetición si el JSON no pasa la validación
            try:
                resultado = validar_respuesta(
                    _llamar_claude(cliente, modelo, [d for _, d in grupo]), claves)
                break
            except Exception as e:  # errores de la API o de validación: se informan abajo
                ultimo = e
        for item, d in grupo:
            if resultado is None:
                item.resumen, item.nota_resumen = item.norma.sumilla, NOTA_SIN_CLAUDE
                continue
            r = resultado[d["clave"]]
            notas = []
            if d["texto_cortado"]:
                notas.append(NOTA_TEXTO_PARCIAL)
            if r["texto_insuficiente"]:
                notas.append(NOTA_INSUFICIENTE)
            item.resumen, item.nota_resumen = r["resumen"], " ".join(notas)
            cache[d["clave"]] = {"resumen": item.resumen, "nota": item.nota_resumen,
                                 "modelo": modelo, "fecha": hoy.isoformat()}
        if resultado is None:
            errores.append(f"Falló el resumen con Claude de {len(grupo)} norma(s) "
                           f"({type(ultimo).__name__}: {ultimo}); se muestra la sumilla.")
    return errores
