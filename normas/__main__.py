"""Punto de entrada:  python -m normas [--modo completo|novedades] [--fecha dd/mm/aaaa] [--dry-run]"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import correo, estado, resumen
from .filtros import cargar_reglas
from .fuente import Fuente
from .prioridades import Informe, armar_informe

LIMA = ZoneInfo("America/Lima")
DIR_SALIDA = Path("salida")


def _argumentos(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="normas", description="Resumen de normas de El Peruano.")
    p.add_argument("--modo", choices=["completo", "novedades"], default="completo")
    p.add_argument("--fecha", help="dd/mm/aaaa; por defecto, hoy en Lima")
    p.add_argument("--dry-run", action="store_true",
                   help="imprime el correo; no envía nada ni toca el estado")
    p.add_argument("--no-enviar", action="store_true",
                   help="no envía; deja el correo en salida/correo.html y salida/correo.txt")
    p.add_argument("--aviso-falla", metavar="DETALLE",
                   help="solo envía un correo de aviso de falla y termina")
    return p.parse_args(argv)


def obtener_textos(informe: Informe, fuente: Fuente, cache: dict, reglas: dict,
                   errores: list[str]) -> tuple[dict[str, str], bool]:
    """Pide el texto completo de las normas a resumir. Devuelve (textos, fallo_total)."""
    cfg = reglas.get("resumenes", {})
    maximo = int(cfg.get("max_textos_por_ejecucion", 40))
    pausa = float(cfg.get("pausa_entre_textos_segundos", 1.0))
    pendientes = [i.norma for i in informe.items_con_resumen()
                  if i.norma.clave not in cache
                  and "busquedas.elperuano.pe/dispositivo/" in i.norma.url]
    if len(pendientes) > maximo:
        errores.append(f"{len(pendientes) - maximo} norma(s) quedaron sin texto completo por el "
                       f"límite de {maximo} textos por ejecución.")
    textos: dict[str, str] = {}
    fallas = 0
    for k, norma in enumerate(pendientes[:maximo]):
        if k:
            time.sleep(pausa)
        try:
            texto = fuente.texto_de_norma(norma)
        except Exception as e:
            fallas += 1
            errores.append(f"No se pudo obtener el texto de {norma.titulo}: {e}")
            continue
        if texto:
            textos[norma.clave] = texto
        else:
            fallas += 1
            errores.append(f"La página de {norma.titulo} no trae el texto de la norma.")
    intentados = min(len(pendientes), maximo)
    return textos, (intentados > 0 and fallas == intentados)


def ejecutar(args: argparse.Namespace) -> int:
    ahora_utc = datetime.now(timezone.utc)
    ahora_lima = ahora_utc.astimezone(LIMA)

    if args.aviso_falla:
        correo.enviar_aviso_de_falla(args.aviso_falla, ahora_lima)
        print("Aviso de falla enviado.")
        return 0

    reglas = cargar_reglas()
    hoy_real = ahora_lima.date()
    hoy: date = datetime.strptime(args.fecha, "%d/%m/%Y").date() if args.fecha else hoy_real
    es_prueba = hoy != hoy_real
    guardar_estado = not (args.dry_run or args.no_enviar or es_prueba)
    errores: list[str] = []
    grave = False

    # --- 1. Consultas a El Peruano (como máximo 2 al listado) ---
    fuente = Fuente()
    normas_hoy = fuente.normas_por_fecha(hoy) if es_prueba else fuente.normas_de_hoy()
    extras_ayer = []
    if args.modo == "completo":
        extras_ayer = fuente.normas_por_fecha(hoy - timedelta(days=1), solo_extraordinarias=True)
    print(f"Normas de {hoy:%d/%m/%Y}: {len(normas_hoy)}. "
          f"Extraordinarias de ayer: {len(extras_ayer)}.")
    otras_fechas = sum(1 for n in normas_hoy if n.fecha != f"{hoy:%d/%m/%Y}")
    if otras_fechas:
        errores.append(f"{otras_fechas} norma(s) del listado de hoy traen otra fecha.")
    if args.modo == "completo" and not normas_hoy:
        errores.append("El listado de hoy todavía no tiene normas a la hora de esta ejecución.")

    # --- 2. Clasificación ---
    vistas = estado.cargar_vistas()
    informe = armar_informe(args.modo, normas_hoy, extras_ayer, set(vistas), reglas)
    dias = int(reglas.get("estado", {}).get("dias_a_conservar", 10))

    sin_novedades = args.modo == "novedades" and not informe.hay_contenido()
    if sin_novedades and not reglas.get("novedades", {}).get("enviar_sin_novedades", False):
        print("Sin novedades: no se envía correo.")
        if guardar_estado:
            estado.guardar_vistas(
                estado.actualizar_vistas(vistas, informe.claves_revisadas, hoy, dias))
        return 0

    # --- 3. Resúmenes ---
    cache = estado.cargar_resumenes()
    textos, fallo_textos = obtener_textos(informe, fuente, cache, reglas, errores)
    grave = grave or fallo_textos
    hay_clave = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if textos and not hay_clave:
        if not args.dry_run:
            raise RuntimeError("Falta ANTHROPIC_API_KEY: no se pueden generar los resúmenes.")
        errores.append("Dry-run sin ANTHROPIC_API_KEY: no se llamó a Claude.")
    errores_resumen = resumen.resumir(informe.items_con_resumen(), textos, cache, hoy, reglas,
                                      usar_claude=hay_clave)
    errores += errores_resumen
    grave = grave or bool(errores_resumen)

    # --- 4. Correo ---
    ctx = correo.Contexto(ahora_lima=ahora_lima, ahora_utc=ahora_utc,
                          fecha_consultada=f"{hoy:%d/%m/%Y}", es_prueba=es_prueba,
                          errores=errores)
    asunto, cuerpo_txt, cuerpo_html = (correo.asunto(ctx), correo.texto_plano(informe, ctx),
                                       correo.html(informe, ctx))
    if args.dry_run:
        print(f"Asunto: {asunto}\n")
        print(cuerpo_txt)
    elif args.no_enviar:
        DIR_SALIDA.mkdir(exist_ok=True)
        (DIR_SALIDA / "correo.html").write_text(
            f"<!doctype html><meta charset='utf-8'><title>{asunto}</title>{cuerpo_html}",
            encoding="utf-8")
        (DIR_SALIDA / "correo.txt").write_text(cuerpo_txt, encoding="utf-8")
        print(f"Correo no enviado; quedó en {DIR_SALIDA}/correo.html y correo.txt")
    else:
        correo.enviar(asunto, cuerpo_txt, cuerpo_html)
        print(f"Correo enviado: {asunto}")

    # --- 5. Estado (solo después de enviar) ---
    if guardar_estado:
        estado.guardar_vistas(estado.actualizar_vistas(vistas, informe.claves_revisadas, hoy, dias))
        estado.guardar_resumenes(cache, hoy, dias)

    print(f"Consultas al listado: {fuente.consultas_listado}. "
          f"Textos de normas pedidos: {fuente.textos_pedidos}.")
    if grave:
        print("La ejecución terminó con errores que requieren atención:", file=sys.stderr)
        for e in errores:
            print(f"  - {e}", file=sys.stderr)
        return 1
    return 0


def main(argv=None) -> None:
    sys.exit(ejecutar(_argumentos(argv)))


if __name__ == "__main__":
    main()
