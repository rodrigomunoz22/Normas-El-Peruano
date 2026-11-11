"""Estado entre ejecuciones: claves ya enviadas (data/vistas.json) y resúmenes hechos."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
RUTA_VISTAS = RAIZ / "data" / "vistas.json"
RUTA_RESUMENES = RAIZ / "data" / "resumenes.json"


def _leer(ruta: Path) -> dict:
    if not ruta.exists():
        return {}
    with open(ruta, encoding="utf-8") as f:
        datos = json.load(f)   # si el archivo está corrupto, que falle: no hay fallos silenciosos
    if not isinstance(datos, dict):
        raise ValueError(f"{ruta} no tiene el formato esperado.")
    return datos


def _escribir(ruta: Path, datos: dict) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")


def _podar(registros: dict, hoy: date, dias: int, campo: str | None = None) -> dict:
    """Conserva solo los registros cuya fecha (ISO) esté dentro de los últimos <dias> días."""
    limite = (hoy - timedelta(days=dias)).isoformat()
    def fecha(v):
        return v if campo is None else v.get(campo, "")
    return {k: v for k, v in registros.items() if fecha(v) >= limite}


# --- Claves vistas ---------------------------------------------------------

def cargar_vistas(ruta: Path = RUTA_VISTAS) -> dict[str, str]:
    """Devuelve {clave: fecha ISO en que se vio por primera vez}."""
    return dict(_leer(ruta).get("vistas", {}))


def actualizar_vistas(vistas: dict[str, str], claves: list[str], hoy: date,
                      dias: int = 10) -> dict[str, str]:
    """Agrega las claves nuevas con la fecha de hoy y descarta las de más de <dias> días."""
    nuevas = dict(vistas)
    for c in claves:
        nuevas.setdefault(c, hoy.isoformat())
    return _podar(nuevas, hoy, dias)


def guardar_vistas(vistas: dict[str, str], ruta: Path = RUTA_VISTAS) -> None:
    _escribir(ruta, {"vistas": vistas})


# --- Resúmenes ya hechos (para no repetir llamadas a Claude) ---------------

def cargar_resumenes(ruta: Path = RUTA_RESUMENES) -> dict[str, dict]:
    """Devuelve {clave: {"resumen", "nota", "modelo", "fecha"}}."""
    return dict(_leer(ruta).get("resumenes", {}))


def guardar_resumenes(resumenes: dict[str, dict], hoy: date, dias: int = 10,
                      ruta: Path = RUTA_RESUMENES) -> None:
    _escribir(ruta, {"resumenes": _podar(resumenes, hoy, dias, campo="fecha")})


# --- Franjas horarias (8:37, 12:09 y 15:42) --------------------------------
# GitHub puede retrasar u omitir una ejecución programada. Por eso el workflow
# también corre en horarios de respaldo y aquí se anota qué franjas del día ya
# se atendieron: un respaldo solo actúa si la franja quedó pendiente.

RUTA_FRANJAS = RAIZ / "data" / "franjas.json"
FRANJAS = ["08:37", "12:09", "15:42"]   # hora de Lima; deben coincidir con resumen.yml
FRANJA_COMPLETA = "08:37"


def cargar_franjas_hechas(hoy: date, ruta: Path = RUTA_FRANJAS) -> list[str]:
    """Franjas ya atendidas hoy ([] si el registro es de otro día)."""
    datos = _leer(ruta)
    return list(datos.get("hechas", [])) if datos.get("fecha") == hoy.isoformat() else []


def franjas_pendientes(hora_lima: str, hechas: list[str]) -> list[str]:
    """Franjas cuya hora ya llegó (hh:mm de Lima) y que aún no se atendieron."""
    return [f for f in FRANJAS if f <= hora_lima and f not in hechas]


def modo_automatico(pendientes: list[str]) -> str | None:
    """'completo' si falta la primera franja, 'novedades' si falta otra, None si nada."""
    if not pendientes:
        return None
    return "completo" if FRANJA_COMPLETA in pendientes else "novedades"


def guardar_franjas(hoy: date, hechas: list[str], ruta: Path = RUTA_FRANJAS) -> None:
    _escribir(ruta, {"fecha": hoy.isoformat(), "hechas": sorted(set(hechas))})
