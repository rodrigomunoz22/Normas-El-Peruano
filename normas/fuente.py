"""Consultas a El Peruano: listados (diariooficial) y texto de cada norma (busquedas)."""
from __future__ import annotations

import time
from datetime import date

import requests

from .parser import Norma, extraer_texto_norma, parsear_listado

BASE = "https://diariooficial.elperuano.pe"
URL_HOY = f"{BASE}/Normas/LoadNormasLegales"
URL_FILTRO = f"{BASE}/Normas/Filtro"
USER_AGENT = ("monitor-normas-elperuano/1.0 "
              "(resumen diario automatizado de uso personal; GitHub Actions)")
TIMEOUT = 45                 # segundos por intento
INTENTOS = 3                 # 1 intento + 2 reintentos
ESPERA_BASE = 5              # segundos; se duplica en cada reintento
MAX_CONSULTAS_LISTADO = 3    # consultas al listado por ejecución (los reintentos no cuentan)


class ErrorDeFuente(Exception):
    """No se pudo obtener una respuesta válida de El Peruano."""


class Fuente:
    def __init__(self, sesion: requests.Session | None = None, dormir=time.sleep):
        self.sesion = sesion or requests.Session()
        self.sesion.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "es-PE,es"})
        self.dormir = dormir
        self.consultas_listado = 0
        self.textos_pedidos = 0

    def _pedir(self, metodo: str, url: str, **kwargs) -> str:
        ultimo_error: Exception | None = None
        for intento in range(1, INTENTOS + 1):
            try:
                r = self.sesion.request(metodo, url, timeout=TIMEOUT, **kwargs)
                if r.status_code == 200:
                    r.encoding = r.encoding or "utf-8"
                    return r.text
                ultimo_error = ErrorDeFuente(f"HTTP {r.status_code} en {url}")
                if r.status_code in (400, 401, 403, 404):
                    break  # reintentar no ayuda
            except requests.RequestException as e:
                ultimo_error = e
            if intento < INTENTOS:
                self.dormir(ESPERA_BASE * 2 ** (intento - 1))
        raise ErrorDeFuente(f"Falló la consulta a {url}: {ultimo_error}")

    def _listado(self, metodo: str, url: str, **kwargs) -> list[Norma]:
        if self.consultas_listado >= MAX_CONSULTAS_LISTADO:
            raise ErrorDeFuente("Se alcanzó el máximo de consultas al listado por ejecución.")
        self.consultas_listado += 1
        cabeceras = {"X-Requested-With": "XMLHttpRequest"}
        return parsear_listado(self._pedir(metodo, url, headers=cabeceras, **kwargs))

    def normas_de_hoy(self) -> list[Norma]:
        """Todo lo publicado hoy (según el propio sitio)."""
        return self._listado("GET", URL_HOY,
                             params={"Length": "0", "X-Requested-With": "XMLHttpRequest"})

    def normas_por_fecha(self, desde: date, hasta: date | None = None,
                         solo_extraordinarias: bool = False) -> list[Norma]:
        """Normas de un rango de fechas; con solo_extraordinarias envía tipo=on."""
        hasta = hasta or desde
        datos = {"cddesde": desde.strftime("%d/%m/%Y"), "cdhasta": hasta.strftime("%d/%m/%Y")}
        if solo_extraordinarias:
            datos["tipo"] = "on"
        return self._listado("POST", URL_FILTRO, data=datos)

    def texto_de_norma(self, norma: Norma) -> str:
        """Texto completo de la norma, o '' si no tiene página en busquedas.elperuano.pe."""
        if "busquedas.elperuano.pe/dispositivo/" not in norma.url:
            return ""
        self.textos_pedidos += 1
        return extraer_texto_norma(self._pedir("GET", norma.url))
