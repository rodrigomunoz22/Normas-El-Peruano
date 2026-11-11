"""Arma el correo (HTML y texto plano) y lo envía por SMTP de Gmail.

El envío usa el mismo mecanismo que el monitor de proyectos de ley
(smtplib.SMTP_SSL contra smtp.gmail.com:465 con contraseña de aplicación).
"""
from __future__ import annotations

import os
import smtplib
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from html import escape

from .prioridades import Agregado, Concentracion, Informe, Item

SMTP_HOST, SMTP_PUERTO = "smtp.gmail.com", 465
URL_FUENTE = "https://diariooficial.elperuano.pe/Normas"
AVISO_ALCANCE = ("Se leyó el texto completo solo de las normas de PCM y MEF y de las "
                 "designaciones, renuncias, encargos y viajes de ministerios, organismos "
                 "constitucionalmente autónomos y organismos reguladores. En las demás se "
                 "muestra el título y la sumilla del listado, sin resumen adicional.")


@dataclass
class Contexto:
    ahora_lima: datetime
    ahora_utc: datetime
    fecha_consultada: str                 # dd/mm/aaaa
    es_prueba: bool = False               # ejecución manual con fecha distinta de hoy
    errores: list[str] = field(default_factory=list)


def asunto(ctx: Contexto) -> str:
    base = f"Normas El Peruano — {ctx.ahora_lima:%d/%m/%Y, %H:%M}"
    return f"{base} (prueba con fecha {ctx.fecha_consultada})" if ctx.es_prueba else base


def _secciones(informe: Informe) -> list[tuple[str, list[Item]]]:
    return [
        ("1. PCM y MEF", informe.institucional),
        ("2. Edición extraordinaria de ayer", informe.extraordinaria_ayer),
        ("3. Prioridad alta", informe.alta),
        ("4. Prioridad media", informe.media),
    ]


def _etiquetas(item: Item, seccion_extra: bool) -> list[str]:
    etiquetas = []
    if item.norma.extraordinaria and not seccion_extra:
        etiquetas.append("Edición extraordinaria")
    if item.de_ayer:
        etiquetas.append(f"Publicada ayer ({item.norma.fecha})")
    if item.ya_informada:
        etiquetas.append("ya informada en un correo anterior")
    return etiquetas


def _cuerpo_item(item: Item) -> tuple[str, str]:
    """(resumen, nota). Sin revisión detallada se muestra la sumilla del listado."""
    if not item.detalle:
        return item.norma.sumilla, ""
    return item.resumen or item.norma.sumilla, item.nota_resumen


def _baja_por_entidad(items: list[Item]) -> "OrderedDict[str, list[Item]]":
    grupos: "OrderedDict[str, list[Item]]" = OrderedDict()
    for i in items:
        grupos.setdefault(i.norma.entidad, []).append(i)
    return grupos


def _linea_concentracion(c: Concentracion, modo: str) -> str:
    texto = f"{c.entidad} — {c.fecha}: {c.total} normas"
    if modo == "novedades":
        texto += f" acumuladas en el día, {c.nuevas} nueva(s) en este correo"
    return texto


def _desglose(c: Concentracion) -> str:
    return "; ".join(f"{tipo.capitalize()}: {n}" for tipo, n in c.conteo_por_tipo.items())


def _lineas_agregado(a: Agregado) -> tuple[str, list[str]]:
    """(título, líneas) del resumen agregado; cuenta resoluciones, no personas."""
    titulo = f"{a.entidad} — {a.etiqueta} ({a.fecha}): {len(a.normas)} resoluciones"
    lineas = []
    for accion, lugares in a.conteo.items():
        total = sum(1 for n in a.normas if _accion(n) == accion)
        detalle = ", ".join(f"{lugar} ({k})" for lugar, k in
                            sorted(lugares.items(), key=lambda x: (-x[1], x[0])))
        lineas.append(f"{accion}: {total} — {detalle}")
    return titulo, lineas


def _accion(norma) -> str:
    from .prioridades import accion_de
    return accion_de(norma.sumilla)


NOTA_AGREGADO = ("Conteo por resolución según la sumilla del listado; una resolución puede "
                 "comprender a más de una persona o distrito.")


def _pie(informe: Informe, ctx: Contexto) -> list[str]:
    lineas = [
        f"Se omitieron {informe.municipales_omitidas} normas municipales.",
        f"Normas revisadas: {informe.revisadas} (fecha consultada: {ctx.fecha_consultada}; "
        f"modo {informe.modo}).",
        f"Hora real de ejecución: {ctx.ahora_lima:%d/%m/%Y %H:%M} (Lima) / "
        f"{ctx.ahora_utc:%d/%m/%Y %H:%M} (UTC).",
        "Los resúmenes se generan automáticamente solo con el texto publicado; "
        "verifica siempre en el enlace de cada norma.",
        f"Fuente: Diario Oficial El Peruano, {URL_FUENTE}",
    ]
    if ctx.errores:
        lineas.append("Errores no fatales: " + " | ".join(ctx.errores))
    else:
        lineas.append("Errores no fatales: ninguno.")
    return lineas


# --- Texto plano -----------------------------------------------------------

def texto_plano(informe: Informe, ctx: Contexto) -> str:
    s: list[str] = [asunto(ctx), ""]
    if not informe.hay_contenido():
        s += ["No hay normas para informar en esta ejecución.", ""]
    else:
        s += [AVISO_ALCANCE, ""]
    for titulo, items in _secciones(informe):
        if not items:
            continue
        s += [titulo.upper(), "-" * len(titulo)]
        for it in items:
            n = it.norma
            etq = _etiquetas(it, titulo.startswith("2."))
            resumen, nota = _cuerpo_item(it)
            s.append(f"* {n.entidad} — {n.titulo}" + (f" [{'; '.join(etq)}]" if etq else ""))
            s.append(f"  {resumen}")
            if nota:
                s.append(f"  ({nota})")
            s.append(f"  Fecha: {n.fecha} | " + (n.url or "Sin enlace en la fuente"))
            s.append("")
    if informe.concentradas:
        t = "5. Instituciones con muchas normas"
        s += [t.upper(), "-" * len(t)]
        for c in informe.concentradas:
            s.append("* " + _linea_concentracion(c, informe.modo))
            s.append(f"  Desglose por tipo: {_desglose(c)}")
            if c.prioritaria:
                s.append("  Sus normas están detalladas en la sección 1.")
            else:
                for tipo, normas in c.por_tipo.items():
                    for n in normas:
                        s.append(f"  - {n.titulo}: " + (n.url or "sin enlace en la fuente"))
            s.append("")
    if informe.baja or informe.agregados:
        t = "6. Prioridad baja"
        s += [t.upper(), "-" * len(t)]
        for a in informe.agregados:
            titulo, lineas = _lineas_agregado(a)
            s.append(f"* {titulo}")
            s += [f"  - {l}" for l in lineas]
            s.append(f"  ({NOTA_AGREGADO})")
            if a.enlaces_en_concentracion:
                s.append("  Enlaces en la sección 5.")
            else:
                s += [f"  - {n.titulo}: " + (n.url or "sin enlace en la fuente") for n in a.normas]
        for entidad, items in _baja_por_entidad(informe.baja).items():
            s.append(f"* {entidad} ({len(items)}):")
            for i in items:
                resumen, nota = _cuerpo_item(i)
                s.append(f"  - {i.norma.titulo}"
                         + (" [Edición extraordinaria]" if i.norma.extraordinaria else "")
                         + (" [Publicada ayer]" if i.de_ayer else "")
                         + f": {resumen}" + (f" ({nota})" if nota else "")
                         + " | " + (i.norma.url or "sin enlace en la fuente"))
        s.append("")
    s += ["--"] + _pie(informe, ctx)
    return "\n".join(s) + "\n"


# --- HTML ------------------------------------------------------------------

def _a(url: str, texto: str) -> str:
    if not url:
        return f"{escape(texto)} <span style=\"color:#777\">(sin enlace en la fuente)</span>"
    return f'<a href="{escape(url, quote=True)}" style="color:#0b57a4">{escape(texto)}</a>'


def _chip(texto: str, color: str = "#b3261e") -> str:
    return (f' <span style="font-size:11px;color:#fff;background:{color};border-radius:3px;'
            f'padding:1px 5px;white-space:nowrap">{escape(texto)}</span>')


def html(informe: Informe, ctx: Contexto) -> str:
    h2 = '<h2 style="font-size:17px;border-bottom:2px solid #c00;padding-bottom:3px;margin:22px 0 8px">'
    p: list[str] = [
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#222;'
        'max-width:760px;line-height:1.45">',
        f'<h1 style="font-size:20px;margin:0 0 4px">{escape(asunto(ctx))}</h1>',
    ]
    if not informe.hay_contenido():
        p.append("<p>No hay normas para informar en esta ejecución.</p>")
    else:
        p.append(f'<p style="color:#555;font-size:12px;margin:0 0 6px">{escape(AVISO_ALCANCE)}</p>')
    for titulo, items in _secciones(informe):
        if not items:
            continue
        p.append(f"{h2}{escape(titulo)} ({len(items)})</h2>")
        for it in items:
            n = it.norma
            resumen, nota = _cuerpo_item(it)
            chips = "".join(
                _chip(e, "#b3261e" if e.startswith("Edición") else "#666")
                for e in _etiquetas(it, titulo.startswith("2.")))
            p.append(
                '<div style="margin:0 0 12px">'
                f'<div><b>{escape(n.entidad)}</b> — {_a(n.url, n.titulo)}{chips}</div>'
                f'<div>{escape(resumen)}</div>'
                + (f'<div style="color:#8a5a00;font-size:12px">{escape(nota)}</div>' if nota else "")
                + f'<div style="color:#666;font-size:12px">Fecha: {escape(n.fecha)}'
                + (f' · {_a(n.url, "Ver en El Peruano")}' if n.url else "")
                + "</div></div>")
    if informe.concentradas:
        p.append(f"{h2}5. Instituciones con muchas normas</h2>")
        for c in informe.concentradas:
            p.append(f'<div style="margin:0 0 12px"><div><b>'
                     f'{escape(_linea_concentracion(c, informe.modo))}</b></div>'
                     f'<div>Desglose por tipo: {escape(_desglose(c))}</div>')
            if c.prioritaria:
                p.append("<div>Sus normas están detalladas en la sección 1.</div>")
            else:
                enlaces = [_a(n.url, n.titulo) for normas in c.por_tipo.values() for n in normas]
                p.append('<div style="font-size:12px">' + " · ".join(enlaces) + "</div>")
            p.append("</div>")
    if informe.baja or informe.agregados:
        n_baja = len(informe.baja) + sum(len(a.normas) for a in informe.agregados)
        p.append(f"{h2}6. Prioridad baja ({n_baja})</h2>")
        for a in informe.agregados:
            titulo, lineas = _lineas_agregado(a)
            p.append(f'<div style="margin:0 0 10px;font-size:13px"><b>{escape(titulo)}</b>'
                     '<ul style="margin:2px 0 0;padding-left:18px">'
                     + "".join(f"<li>{escape(l)}</li>" for l in lineas) + "</ul>"
                     f'<div style="color:#8a5a00;font-size:12px">{escape(NOTA_AGREGADO)}</div>')
            if a.enlaces_en_concentracion:
                p.append('<div style="font-size:12px;color:#666">Enlaces en la sección 5.</div>')
            else:
                p.append('<div style="font-size:12px">'
                         + " · ".join(_a(n.url, n.titulo) for n in a.normas) + "</div>")
            p.append("</div>")
        for entidad, items in _baja_por_entidad(informe.baja).items():
            p.append(f'<div style="margin:0 0 8px;font-size:13px"><b>{escape(entidad)}</b> '
                     f'({len(items)})<ul style="margin:2px 0 0;padding-left:18px">')
            for i in items:
                resumen, nota = _cuerpo_item(i)
                p.append(
                    "<li>" + _a(i.norma.url, i.norma.titulo)
                    + (_chip("Edición extraordinaria") if i.norma.extraordinaria else "")
                    + (_chip("Publicada ayer", "#666") if i.de_ayer else "")
                    + f": {escape(resumen)}"
                    + (f' <span style="color:#8a5a00;font-size:12px">({escape(nota)})</span>'
                       if nota else "") + "</li>")
            p.append("</ul></div>")
    p.append('<hr style="border:0;border-top:1px solid #ccc;margin:20px 0 8px">'
             '<div style="font-size:12px;color:#555">'
             + "<br>".join(escape(l) for l in _pie(informe, ctx)) + "</div></div>")
    return "\n".join(p)


# --- Envío -----------------------------------------------------------------

def _credenciales() -> tuple[str, str, list[str]]:
    faltan = [v for v in ("GMAIL_USER", "GMAIL_APP_PASSWORD", "MAIL_TO") if not os.environ.get(v)]
    if faltan:
        raise RuntimeError("Faltan variables de entorno para enviar correo: " + ", ".join(faltan))
    destinatarios = [d.strip() for d in os.environ["MAIL_TO"].split(",") if d.strip()]
    return os.environ["GMAIL_USER"], os.environ["GMAIL_APP_PASSWORD"], destinatarios


def enviar(asunto_: str, cuerpo_texto: str, cuerpo_html: str | None = None) -> None:
    usuario, clave, destinatarios = _credenciales()
    msg = EmailMessage()
    msg["From"] = usuario
    msg["To"] = ", ".join(destinatarios)
    msg["Subject"] = asunto_
    msg.set_content(cuerpo_texto)
    if cuerpo_html:
        msg.add_alternative(cuerpo_html, subtype="html")
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PUERTO, timeout=60) as servidor:
        servidor.login(usuario, clave)
        servidor.send_message(msg)


def enviar_aviso_de_falla(detalle: str, ahora_lima: datetime) -> None:
    enviar(
        f"FALLA: Normas El Peruano — {ahora_lima:%d/%m/%Y, %H:%M}",
        "La ejecución del monitor de normas de El Peruano terminó con error.\n\n"
        f"Detalle: {detalle}\n\n"
        "Revisa el registro de la ejecución en la pestaña Actions del repositorio.\n",
    )
