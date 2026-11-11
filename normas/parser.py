"""Convierte el HTML del listado de normas de El Peruano en objetos Norma.

Estructura observada en el sitio (noviembre de 2026):
  - cada norma es un <article> cuya clase empieza con "edicionesoficiales_articulos"
    (las de "Publicación Digital" usan "edicionesoficiales_articulos_dig");
  - entidad en <h4>; tipo y número en <h5> (normalmente dentro de un <a>);
  - fecha en el primer <b> ("Fecha: dd/mm/aaaa");
  - <strong class="extraordinaria"> si es de edición extraordinaria;
  - sumilla en el último <p>.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import asdict, dataclass

from bs4 import BeautifulSoup

RE_ID = re.compile(r"/dispositivo/(?:EX|NL)/([0-9A-Za-z-]+)")
RE_FECHA = re.compile(r"(\d{2}/\d{2}/\d{4})")
RE_CORTE_TIPO = re.compile(r"\s+N\s?[°ºo]\.?(?:\s|$)|\s+N\.[°º]|\s*\d")
MARCA_RESPUESTA = "var extraordinaria"


class ErrorDeEstructura(Exception):
    """El HTML recibido no tiene la forma esperada (posible cambio del sitio o bloqueo)."""


@dataclass(frozen=True)
class Norma:
    clave: str            # id del enlace (p. ej. "2573271-1") o hash si no hay enlace
    entidad: str          # tal como aparece, sin la marca "Publicación Digital"
    titulo: str           # tipo y número, p. ej. "DECRETO SUPREMO N° 248-2026-EF"
    tipo: str             # tipo normalizado, p. ej. "DECRETO SUPREMO"
    fecha: str            # dd/mm/aaaa (campo Fecha del listado)
    extraordinaria: bool
    sumilla: str          # resumen corto del listado
    url: str              # enlace a la fuente ("" si la norma no tiene enlace)
    digital: bool         # marcada como "Publicación Digital"

    def como_dict(self) -> dict:
        return asdict(self)


def normalizar(texto: str) -> str:
    """Mayúsculas, sin tildes y con espacios simples: para comparar nombres."""
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFD", texto or "") if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"\s+", " ", sin_tildes).strip().upper()


def _limpio(texto: str) -> str:
    return re.sub(r"\s+", " ", texto or "").strip()


def extraer_tipo(titulo: str) -> str:
    """'RESOLUCIÓN MINISTERIAL  N° 510-2026-EF/10' -> 'RESOLUCION MINISTERIAL'."""
    norm = normalizar(titulo)
    corte = RE_CORTE_TIPO.search(norm)
    if corte and corte.start() > 0:
        return norm[: corte.start()].strip()
    # Sin número (p. ej. "CONVENIO Protocolo de ..."): la primera palabra es el tipo.
    return norm.split(" ")[0] if norm else ""


def _clave(url: str, entidad: str, titulo: str, fecha: str) -> str:
    m = RE_ID.search(url or "")
    if m:
        return m.group(1)
    base = "|".join((normalizar(entidad), normalizar(titulo), fecha))
    return "h-" + hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


def parsear_listado(html: str) -> list[Norma]:
    """Devuelve las normas del listado. Lanza ErrorDeEstructura si el HTML no es el esperado."""
    sopa = BeautifulSoup(html or "", "html.parser")
    articulos = [
        a for a in sopa.find_all("article")
        if any(c.startswith("edicionesoficiales_articulos") for c in (a.get("class") or []))
    ]
    if not articulos:
        if MARCA_RESPUESTA in (html or ""):
            return []  # respuesta válida de un día (todavía) sin normas
        raise ErrorDeEstructura(
            "La respuesta no contiene normas ni la marca esperada del listado "
            f"({len(html or '')} bytes). El sitio pudo cambiar o bloquear la consulta."
        )

    normas: list[Norma] = []
    vistas: set[str] = set()
    for art in articulos:
        h4, h5 = art.find("h4"), art.find("h5")
        if h4 is None or h5 is None:
            raise ErrorDeEstructura("Hay una norma sin <h4> (entidad) o <h5> (tipo y número).")
        entidad_bruta = _limpio(h4.get_text(" "))
        digital = "PUBLICACION DIGITAL" in normalizar(entidad_bruta)
        entidad = _limpio(re.sub(r"(?i)publicaci[oó]n\s+digital", "", entidad_bruta))
        titulo = _limpio(h5.get_text(" "))
        enlace = h5.find("a")
        url = (enlace.get("href") or "").strip() if enlace else ""
        b = art.find("b")
        m = RE_FECHA.search(b.get_text(" ") if b else "")
        if not m:
            raise ErrorDeEstructura(f"No se encontró la fecha de la norma '{titulo}'.")
        fecha = m.group(1)
        parrafos = art.find_all("p")
        sumilla = _limpio(parrafos[-1].get_text(" ")) if len(parrafos) >= 2 else ""
        clave = _clave(url, entidad, titulo, fecha)
        if clave in vistas:
            continue  # el sitio a veces repite una norma en la misma respuesta
        vistas.add(clave)
        normas.append(Norma(
            clave=clave,
            entidad=entidad,
            titulo=titulo,
            tipo=extraer_tipo(titulo),
            fecha=fecha,
            extraordinaria=art.find("strong", class_="extraordinaria") is not None,
            sumilla=sumilla,
            url=url,
            digital=digital,
        ))
    return normas


def extraer_texto_norma(html: str) -> str:
    """Texto completo de la página busquedas.elperuano.pe/dispositivo/... ('' si no está)."""
    sopa = BeautifulSoup(html or "", "html.parser")
    visor = sopa.find(id="visor-html")
    if visor is None:
        return ""
    for sobrante in visor(["script", "style"]):
        sobrante.decompose()
    return _limpio(visor.get_text(" "))
