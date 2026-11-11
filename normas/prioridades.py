"""Clasificación por prioridad, criterio de concentración y armado del informe.

Todo aquí es lógica pura (sin red ni disco) para poder probarla con fixtures.
"""
from __future__ import annotations

import re
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass, field

from .filtros import separar_municipales
from .parser import Norma, normalizar

ALTA, MEDIA, BAJA = "alta", "media", "baja"


def _coincide_tipo(tipo: str, tipos: list[str]) -> bool:
    for t in tipos or []:
        t = normalizar(t)
        if tipo == t or tipo.startswith(t + " "):
            return True
    return False


def _sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto)
                   if unicodedata.category(c) != "Mn")


def _coincide_patron(texto: str, patrones: list[str]) -> bool:
    # A los patrones solo se les quitan las tildes: pasarlos a mayúsculas rompería \b y \w.
    return any(re.search(_sin_tildes(p), texto, re.IGNORECASE) for p in patrones or [])


def es_prioritaria(norma: Norma, reglas: dict) -> bool:
    """True si la entidad es de prioridad institucional (PCM, MEF u otras configuradas)."""
    entidad = normalizar(norma.entidad)
    return entidad in {normalizar(e) for e in reglas.get("entidades_prioritarias") or []}


def prioridad(norma: Norma, reglas: dict) -> str:
    """Devuelve 'alta', 'media' o 'baja' según config/reglas.yml."""
    alta, media, baja = reglas.get("alta", {}), reglas.get("media", {}), reglas.get("baja", {})
    entidad = normalizar(norma.entidad)
    sumilla = normalizar(norma.sumilla)
    titulo_y_sumilla = f"{normalizar(norma.titulo)} {sumilla}"

    es_alta = _coincide_tipo(norma.tipo, alta.get("tipos")) or _coincide_patron(
        titulo_y_sumilla, alta.get("patrones"))
    es_baja = _coincide_patron(sumilla, baja.get("patrones")) or entidad in {
        normalizar(e) for e in baja.get("entidades") or []}
    es_media = _coincide_tipo(norma.tipo, media.get("tipos")) or any(
        entidad.startswith(normalizar(p)) for p in media.get("prefijos_entidad") or [])

    if es_alta and not (es_baja and baja.get("gana_sobre_alta", False)):
        return ALTA
    if es_baja and (not es_media or baja.get("gana_sobre_media", True)):
        return BAJA
    if es_media:
        return MEDIA
    if es_baja:
        return BAJA
    return reglas.get("prioridad_por_defecto", MEDIA)


def es_designacion_o_viaje(norma: Norma, reglas: dict) -> bool:
    """True si la sumilla habla de designaciones, renuncias, encargos o viajes."""
    patrones = reglas.get("revision_detallada", {}).get("patrones") or []
    return _coincide_patron(normalizar(norma.sumilla), patrones)


def requiere_detalle(norma: Norma, reglas: dict) -> bool:
    """True si hay que abrir la página de la norma y resumir su texto completo."""
    cfg = reglas.get("revision_detallada", {})
    if cfg.get("entidades_prioritarias", True) and es_prioritaria(norma, reglas):
        return True
    if not es_designacion_o_viaje(norma, reglas):
        return False
    grupos = cfg.get("designaciones_y_viajes")
    if not grupos:
        return True                        # sin lista de entidades: todas las designaciones
    entidad = normalizar(norma.entidad)
    def en(lista: str) -> bool:
        return entidad in {normalizar(e) for e in grupos.get(lista) or []}
    if en("autonomos") or en("reguladores"):
        return True
    # En los ministerios solo cuentan las normas del propio ministerio, no las de
    # los organismos adscritos que El Peruano agrupa bajo el nombre del sector.
    return en("ministerios") and _coincide_tipo(norma.tipo, grupos.get("tipos_de_ministerio"))


@dataclass
class Item:
    """Una norma dentro de una sección del correo."""
    norma: Norma
    prioridad: str
    detalle: bool = False          # se abre la página de la norma y Claude la resume
    ya_informada: bool = False     # ya había salido en un correo anterior
    de_ayer: bool = False          # norma ordinaria de ayer que no alcanzó a salir en un correo
    resumen: str = ""
    nota_resumen: str = ""         # p. ej. "Resumen basado solo en el título..."


@dataclass
class Concentracion:
    """Institución con más normas que el umbral en una misma fecha."""
    entidad: str
    fecha: str
    total: int
    nuevas: int
    por_tipo: "OrderedDict[str, list[Norma]]"   # tipo -> normas a listar (con enlace)
    conteo_por_tipo: "OrderedDict[str, int]"    # tipo -> total del día
    prioritaria: bool                            # PCM/MEF: solo se agrega la línea de total


@dataclass
class Informe:
    modo: str
    institucional: list[Item] = field(default_factory=list)
    extraordinaria_ayer: list[Item] = field(default_factory=list)
    alta: list[Item] = field(default_factory=list)
    media: list[Item] = field(default_factory=list)
    concentradas: list[Concentracion] = field(default_factory=list)
    baja: list[Item] = field(default_factory=list)
    municipales_omitidas: int = 0
    revisadas: int = 0
    claves_revisadas: list[str] = field(default_factory=list)

    def items_con_resumen(self) -> list[Item]:
        """Ítems de revisión detallada: se lee su texto completo y Claude los resume."""
        todos = (self.institucional + self.extraordinaria_ayer + self.alta + self.media
                 + self.baja)
        return [i for i in todos if i.detalle]

    def total_a_enviar(self) -> int:
        en_grupos = sum(len(ns) for c in self.concentradas if not c.prioritaria
                        for ns in c.por_tipo.values())
        return (len(self.institucional) + len(self.extraordinaria_ayer) + len(self.alta)
                + len(self.media) + len(self.baja) + en_grupos)

    def hay_contenido(self) -> bool:
        return self.total_a_enviar() > 0


def _sin_duplicados(normas: list[Norma]) -> list[Norma]:
    vistas, salida = set(), []
    for n in normas:
        if n.clave not in vistas:
            vistas.add(n.clave)
            salida.append(n)
    return salida


def armar_informe(modo: str, normas_hoy: list[Norma], normas_ayer: list[Norma],
                  claves_vistas: set[str], reglas: dict) -> Informe:
    """Decide qué norma va en qué sección.

    modo 'completo'  -> se envía todo lo de hoy, la edición extraordinaria de ayer y las
                        normas ordinarias de ayer que no salieron en ningún correo
                        (por ejemplo, las publicadas después de las 15:30).
    modo 'novedades' -> solo las normas de hoy cuya clave no esté en claves_vistas.
    """
    if modo not in ("completo", "novedades"):
        raise ValueError(f"Modo desconocido: {modo}")
    if modo == "novedades":
        normas_ayer = []

    normas_ayer = _sin_duplicados(normas_ayer)
    claves_extra = {n.clave for n in normas_ayer if n.extraordinaria}
    claves_ordinarias_ayer = {n.clave for n in normas_ayer if not n.extraordinaria}
    todas = _sin_duplicados(normas_ayer + normas_hoy)
    informe = Informe(modo=modo, revisadas=len(todas), claves_revisadas=[n.clave for n in todas])

    if modo == "completo":
        candidatas = [n for n in todas if not (n.clave in claves_ordinarias_ayer
                                               and n.clave in claves_vistas)]
    else:
        candidatas = [n for n in todas if n.clave not in claves_vistas]
    claves_candidatas = {n.clave for n in candidatas}

    sin_muni, _ = separar_municipales(todas, reglas)
    _, omitidas = separar_municipales(candidatas, reglas)
    informe.municipales_omitidas = len(omitidas)

    # --- Concentración: más de <umbral> normas de una entidad en una misma fecha ---
    umbral = int(reglas.get("concentracion", {}).get("umbral", 5))
    grupos: "OrderedDict[tuple[str, str], list[Norma]]" = OrderedDict()
    for n in sin_muni:
        grupos.setdefault((normalizar(n.entidad), n.fecha), []).append(n)
    concentrados: set[tuple[str, str]] = set()
    for llave, miembros in grupos.items():
        if len(miembros) <= umbral:        # estrictamente "mayor que"
            continue
        a_listar = [n for n in miembros if n.clave in claves_candidatas]
        if not a_listar:
            continue                       # en novedades: nada nuevo de esta entidad
        concentrados.add(llave)
        por_tipo: "OrderedDict[str, list[Norma]]" = OrderedDict()
        conteo: "OrderedDict[str, int]" = OrderedDict()
        for n in miembros:
            conteo[n.tipo] = conteo.get(n.tipo, 0) + 1
        for n in a_listar:
            por_tipo.setdefault(n.tipo, []).append(n)
        informe.concentradas.append(Concentracion(
            entidad=miembros[0].entidad, fecha=miembros[0].fecha, total=len(miembros),
            nuevas=len(a_listar), por_tipo=por_tipo, conteo_por_tipo=conteo,
            prioritaria=es_prioritaria(miembros[0], reglas)))

    # --- Secciones: cada norma aparece una sola vez, en la primera que le corresponda ---
    for n in candidatas:
        if n.clave not in {m.clave for m in sin_muni}:
            continue
        p = prioridad(n, reglas)
        item = Item(norma=n, prioridad=p, detalle=requiere_detalle(n, reglas),
                    ya_informada=(modo == "completo" and n.clave in claves_vistas),
                    de_ayer=n.clave in claves_ordinarias_ayer)
        if es_prioritaria(n, reglas):
            informe.institucional.append(item)
        elif n.clave in claves_extra:
            informe.extraordinaria_ayer.append(item)
        elif p == ALTA:
            informe.alta.append(item)
        elif p == MEDIA:
            informe.media.append(item)
        elif (normalizar(n.entidad), n.fecha) in concentrados and not item.detalle:
            continue                       # la entrada agrupada reemplaza a la línea individual
        else:
            informe.baja.append(item)
    return informe
