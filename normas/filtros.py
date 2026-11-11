"""Carga de reglas y filtro de normas municipales."""
from __future__ import annotations

from pathlib import Path

import yaml

from .parser import Norma, normalizar

RUTA_REGLAS = Path(__file__).resolve().parent.parent / "config" / "reglas.yml"


def cargar_reglas(ruta: Path | str = RUTA_REGLAS) -> dict:
    with open(ruta, encoding="utf-8") as f:
        reglas = yaml.safe_load(f)
    if not isinstance(reglas, dict):
        raise ValueError(f"{ruta} no contiene un diccionario de reglas válido.")
    return reglas


def es_municipal_excluida(norma: Norma, reglas: dict) -> bool:
    """True si la entidad empieza con el prefijo municipal y no está en las excepciones."""
    cfg = reglas.get("municipalidades", {})
    prefijo = normalizar(cfg.get("prefijo", "MUNICIPALIDAD"))
    entidad = normalizar(norma.entidad)
    if not entidad.startswith(prefijo):
        return False
    excepciones = {normalizar(e) for e in cfg.get("excepciones") or []}
    return entidad not in excepciones


def separar_municipales(normas: list[Norma], reglas: dict) -> tuple[list[Norma], list[Norma]]:
    """Devuelve (normas que pasan, normas municipales omitidas)."""
    pasan, omitidas = [], []
    for n in normas:
        (omitidas if es_municipal_excluida(n, reglas) else pasan).append(n)
    return pasan, omitidas
