"""Pruebas con respuestas HTML reales de El Peruano (descargadas el 11/11/2026).

Fixtures:
  extraordinaria.html           POST /Normas/Filtro 10/11/2026 con tipo=on  (7 normas)
  dia_completo.html             POST /Normas/Filtro 10/11/2026 sin tipo     (45 normas)
  regionales_municipales.html   POST /Normas/Filtro 05/11/2026 sin tipo     (83 normas)
  vacio.html                    GET  /Normas/LoadNormasLegales a las 00:09 del 11/11/2026 (0)
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from normas import correo, estado, resumen
from normas.filtros import cargar_reglas, es_municipal_excluida, separar_municipales
from normas.parser import (ErrorDeEstructura, Norma, extraer_tipo, normalizar,
                           parsear_listado)
from normas.prioridades import (ALTA, BAJA, MEDIA, armar_informe, es_designacion_o_viaje,
                                es_prioritaria, prioridad)

FIXTURES = Path(__file__).parent / "fixtures"


def _leer(nombre: str) -> list[Norma]:
    return parsear_listado((FIXTURES / nombre).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def reglas() -> dict:
    return cargar_reglas()


@pytest.fixture(scope="module")
def dia() -> list[Norma]:
    return _leer("dia_completo.html")


@pytest.fixture(scope="module")
def extra() -> list[Norma]:
    return _leer("extraordinaria.html")


@pytest.fixture(scope="module")
def regionales() -> list[Norma]:
    return _leer("regionales_municipales.html")


def norma(entidad="SALUD", titulo="RESOLUCIÓN MINISTERIAL N° 1-2026/MINSA",
          sumilla="Aprueban documento técnico", fecha="10/11/2026", clave=None,
          extraordinaria=False) -> Norma:
    return Norma(clave=clave or f"k-{entidad}-{titulo}", entidad=entidad, titulo=titulo,
                 tipo=extraer_tipo(titulo), fecha=fecha, extraordinaria=extraordinaria,
                 sumilla=sumilla, url="", digital=False)


# --- Parser ------------------------------------------------------------------

def test_parser_cuenta_normas(dia, extra, regionales):
    assert len(dia) == 45
    assert len(extra) == 7
    assert len(regionales) == 83


def test_parser_respuesta_vacia_es_valida():
    assert _leer("vacio.html") == []


def test_parser_falla_si_cambia_la_estructura():
    with pytest.raises(ErrorDeEstructura):
        parsear_listado("<html><body>Acceso denegado</body></html>")
    with pytest.raises(ErrorDeEstructura):
        parsear_listado("")


def test_parser_campos_de_una_norma(dia):
    n = next(x for x in dia if x.clave == "2573271-1")
    assert n.entidad == "DEFENSA"
    assert n.titulo == "RESOLUCIÓN JEFATURAL N° 00105-2026-CENEPRED/J"
    assert n.tipo == "RESOLUCION JEFATURAL"
    assert n.fecha == "10/11/2026"
    assert n.extraordinaria is True
    assert n.url == "https://busquedas.elperuano.pe/dispositivo/EX/2573271-1"
    assert n.sumilla.startswith("Aceptan renuncia y designan Asesora")


def test_parser_extraordinarias(dia, extra):
    assert all(n.extraordinaria for n in extra)
    assert {n.clave for n in dia if n.extraordinaria} == {n.clave for n in extra}


def test_parser_claves_unicas_y_fechas(dia, regionales):
    for normas in (dia, regionales):
        assert len({n.clave for n in normas}) == len(normas)
        assert all(len(n.fecha) == 10 and n.entidad and n.titulo and n.tipo for n in normas)


def test_parser_publicacion_digital_y_normas_sin_id(dia):
    digital = next(n for n in dia if n.digital)
    assert digital.entidad == "MUNICIPALIDAD DE MI PERÚ"      # sin la marca "Publicación Digital"
    assert digital.clave.startswith("h-")                     # sin id en el enlace: hash
    sin_enlace = [n for n in dia if not n.url]
    assert all(n.clave.startswith("h-") for n in sin_enlace)


def test_extraer_tipo():
    assert extraer_tipo("DECRETO SUPREMO  N° 248-2026-EF") == "DECRETO SUPREMO"
    assert extraer_tipo("RESOLUCIÓN  SBS N° 02628-2026") == "RESOLUCION SBS"
    assert extraer_tipo("RESOLUCIÓN LEGISLATIVA  003-2026-2027-S") == "RESOLUCION LEGISLATIVA"
    assert extraer_tipo("Ordenanzas  N° 232-MDMP") == "ORDENANZAS"
    assert extraer_tipo("CONVENIO  Protocolo de Optimización del TLC") == "CONVENIO"
    assert extraer_tipo("LEY N° 32500") == "LEY"


def test_normalizar():
    assert normalizar("  Economía   y Finanzas ") == "ECONOMIA Y FINANZAS"


# --- Filtro municipal ----------------------------------------------------------

def test_filtro_municipal_excluye(dia, reglas):
    pasan, omitidas = separar_municipales(dia, reglas)
    assert len(omitidas) == 3
    assert {n.entidad for n in omitidas} == {
        "MUNICIPALIDAD DE LA VICTORIA", "MUNICIPALIDAD DE LINCE", "MUNICIPALIDAD DE MI PERÚ"}
    assert len(pasan) == 42


def test_filtro_municipal_excepcion_lima(regionales, reglas):
    pasan, omitidas = separar_municipales(regionales, reglas)
    lima = [n for n in regionales if normalizar(n.entidad) == "MUNICIPALIDAD METROPOLITANA DE LIMA"]
    assert len(lima) == 2                                   # una de ellas es "Publicación Digital"
    assert all(n in pasan for n in lima)
    assert {normalizar(n.entidad) for n in omitidas} == {
        "MUNICIPALIDAD DE MAGDALENA DEL MAR", "MUNICIPALIDAD DE SAN ISIDRO",
        "MUNICIPALIDAD PROVINCIAL DE ANTA"}


def test_filtro_municipal_casos_sinteticos(reglas):
    assert es_municipal_excluida(norma(entidad="MUNICIPALIDAD PROVINCIAL DEL CALLAO"), reglas)
    assert not es_municipal_excluida(norma(entidad="GOBIERNO REGIONAL DE TUMBES"), reglas)
    sin_excepciones = {**reglas, "municipalidades": {"prefijo": "MUNICIPALIDAD", "excepciones": []}}
    assert es_municipal_excluida(norma(entidad="MUNICIPALIDAD METROPOLITANA DE LIMA"),
                                 sin_excepciones)


def test_gobiernos_regionales_pasan_a_media(regionales, reglas):
    gr = [n for n in regionales if n.entidad.startswith("GOBIERNO REGIONAL")]
    assert len(gr) == 2
    informe = armar_informe("completo", regionales, [], set(), reglas)
    claves_media = {i.norma.clave for i in informe.media}
    for n in gr:
        assert not es_municipal_excluida(n, reglas)
        if prioridad(n, reglas) == MEDIA:
            assert n.clave in claves_media
    assert any(prioridad(n, reglas) == MEDIA for n in gr)


# --- Prioridades ---------------------------------------------------------------

@pytest.mark.parametrize("titulo,sumilla,esperada", [
    ("LEY N° 32500", "Ley que modifica el Código Penal", ALTA),
    ("RESOLUCIÓN LEGISLATIVA N° 32501", "Aprueba tratado", ALTA),
    ("DECRETO LEGISLATIVO N° 1700", "Decreto Legislativo que regula algo", ALTA),
    ("DECRETO DE URGENCIA N° 005-2026", "Dictan medidas extraordinarias", ALTA),
    ("RESOLUCIÓN SUPREMA N° 200-2026-PCM", "Designan viceministro", ALTA),
    ("DECRETO SUPREMO N° 120-2026-PCM",
     "Decreto Supremo que declara el Estado de Emergencia en distritos de Lima", ALTA),
    ("RESOLUCIÓN JEFATURAL N° 5-2026", "Declaran en emergencia hídrica la cuenca", ALTA),
    ("DECRETO SUPREMO N° 009-2026-IN", "Decreto Supremo que modifica el Reglamento", MEDIA),
    ("RESOLUCIÓN MINISTERIAL N° 645-2026-MTC/01.03", "Otorgan concesión única", MEDIA),
    ("RESOLUCIÓN SBS N° 100-2026", "Aprueban reglamento de gestión de riesgos", MEDIA),
    ("RESOLUCIÓN MINISTERIAL N° 682-2026-MINEDU", "Designan Asesor II del Despacho", BAJA),
    ("RESOLUCIÓN MINISTERIAL N° 1-2026", "Aceptan renuncia de Director General", BAJA),
    ("RESOLUCIÓN JEFATURAL N° 9-2026", "Encargan funciones de Gerente General", BAJA),
    ("RESOLUCIÓN MINISTERIAL N° 643-2026-MTC/01.02", "Autorizan viaje de inspector a EE.UU.", BAJA),
])
def test_prioridad_por_tipo_y_contenido(reglas, titulo, sumilla, esperada):
    assert prioridad(norma(titulo=titulo, sumilla=sumilla), reglas) == esperada


def test_prioridad_jne_es_baja(dia, reglas):
    jne = [n for n in dia if n.entidad == "JURADO NACIONAL DE ELECCIONES"]
    assert len(jne) == 16
    assert all(prioridad(n, reglas) == BAJA for n in jne)


def test_diseno_no_se_confunde_con_designacion(reglas):
    n = norma(sumilla="Aprueban el diseño del programa presupuestal")
    assert prioridad(n, reglas) == MEDIA


def test_entidades_prioritarias(dia, reglas):
    prioritarias = [n for n in dia if es_prioritaria(n, reglas)]
    assert sorted(n.entidad for n in prioritarias) == [
        "ECONOMÍA Y FINANZAS"] * 3 + ["PRESIDENCIA DEL CONSEJO DE MINISTROS"]


# --- Informe: 8:30 (completo) frente a 12:00 y 15:30 (novedades) ---------------

def _todas_las_claves(informe) -> list[str]:
    individuales = (informe.institucional + informe.extraordinaria_ayer + informe.alta
                    + informe.media + informe.baja)
    return [i.norma.clave for i in individuales]


def test_completo_secciones_del_dia_real(dia, reglas):
    informe = armar_informe("completo", dia, [], set(), reglas)
    assert informe.revisadas == 45
    assert informe.municipales_omitidas == 3
    # 1) PCM y MEF siempre en la primera sección, sea cual sea el tipo.
    assert len(informe.institucional) == 4
    viaje_mef = next(i for i in informe.institucional if "510-2026-EF" in i.norma.titulo)
    assert viaje_mef.detalle and viaje_mef.prioridad == BAJA
    ds_mef = next(i for i in informe.institucional if "248-2026-EF" in i.norma.titulo)
    assert ds_mef.detalle
    # 3) Alta: la Resolución Legislativa del Congreso.
    assert [i.norma.entidad for i in informe.alta] == ["CONGRESO DE LA REPÚBLICA"]
    # Cada norma aparece una sola vez.
    claves = _todas_las_claves(informe)
    assert len(claves) == len(set(claves))
    # Revisión detallada: solo PCM/MEF y designaciones, renuncias, encargos y viajes.
    detalladas = informe.items_con_resumen()
    assert all(es_prioritaria(i.norma, reglas) or es_designacion_o_viaje(i.norma, reglas)
               for i in detalladas)
    assert {i.norma.clave for i in informe.institucional} <= {i.norma.clave for i in detalladas}
    assert all(not i.detalle for i in informe.alta + informe.media)
    # Designaciones y viajes: solo ministerios, autónomos y reguladores.
    con_detalle = {i.norma.titulo for i in informe.baja if i.detalle}
    assert con_detalle == {
        "RESOLUCIÓN MINISTERIAL N° 1009-2026/MINSA",            # ministerio
        "RESOLUCIÓN MINISTERIAL N° 682-2026-MINEDU",
        "RESOLUCIÓN MINISTERIAL N° 00400-2026-PRODUCE",
        "RESOLUCIÓN MINISTERIAL N° 643-2026-MTC/01.02",
        "RESOLUCIÓN N° 000204-2026-P/JNE",                      # autónomos
        "RESOLUCIÓN JEFATURAL N° 000160-2026-JN/ONPE",
        "RESOLUCIÓN SBS N° 02628-2026",
    }
    sin_detalle = {i.norma.titulo for i in informe.baja if not i.detalle}
    assert "RESOLUCIÓN JEFATURAL N° 00105-2026-CENEPRED/J" in sin_detalle   # adscrito a DEFENSA
    assert "RESOLUCIÓN N° 198-2026-IMARPE/PE" in sin_detalle                # adscrito a PRODUCE
    assert "RESOLUCIÓN N° 0715-CU-2026" in sin_detalle                      # universidad
    assert "RESOLUCIÓN N° 0020-2026-APN-PE" in sin_detalle                  # organismo adscrito


def test_completo_incluye_extraordinaria_de_ayer(dia, extra, reglas):
    # "Ayer" = 10/11 (extraordinarias) y "hoy" = un día sintético con una sola norma.
    hoy = [norma(entidad="SALUD", fecha="11/11/2026", clave="hoy-1")]
    informe = armar_informe("completo", hoy, extra, set(), reglas)
    assert len(informe.extraordinaria_ayer) == 7
    assert {i.norma.clave for i in informe.extraordinaria_ayer} == {n.clave for n in extra}
    assert [i.norma.clave for i in informe.media] == ["hoy-1"]


def test_extraordinaria_de_ayer_de_pcm_va_a_la_primera_seccion(reglas):
    e = [norma(entidad="PRESIDENCIA DEL CONSEJO DE MINISTROS", clave="x1", extraordinaria=True,
               titulo="DECRETO SUPREMO N° 1-2026-PCM", fecha="09/11/2026"),
         norma(entidad="SALUD", clave="x2", extraordinaria=True, fecha="09/11/2026")]
    informe = armar_informe("completo", [], e, set(), reglas)
    assert [i.norma.clave for i in informe.institucional] == ["x1"]
    assert [i.norma.clave for i in informe.extraordinaria_ayer] == ["x2"]


def test_novedades_ignora_extraordinaria_de_ayer_y_lo_ya_visto(dia, extra, reglas):
    vistas = {n.clave for n in dia[:40]}
    informe = armar_informe("novedades", dia, extra, vistas, reglas)
    assert informe.extraordinaria_ayer == []
    enviadas = set(_todas_las_claves(informe))
    assert enviadas.isdisjoint(vistas)
    nuevas_no_municipales = [n for n in dia[40:] if not es_municipal_excluida(n, reglas)]
    assert enviadas == {n.clave for n in nuevas_no_municipales}


def test_novedades_sin_nada_nuevo_no_tiene_contenido(dia, reglas):
    informe = armar_informe("novedades", dia, [], {n.clave for n in dia}, reglas)
    assert not informe.hay_contenido()
    assert informe.concentradas == []
    assert informe.municipales_omitidas == 0


def test_completo_reenvia_aunque_ya_este_visto_y_lo_marca(dia, reglas):
    informe = armar_informe("completo", dia, [], {n.clave for n in dia}, reglas)
    assert informe.hay_contenido()
    assert all(i.ya_informada for i in informe.institucional)


# --- Concentración (más de 5 normas de una entidad en un día) -------------------

def test_concentracion_jne_reemplaza_lineas_de_baja(dia, reglas):
    informe = armar_informe("completo", dia, [], set(), reglas)
    assert len(informe.concentradas) == 1
    c = informe.concentradas[0]
    assert (c.entidad, c.fecha, c.total, c.nuevas) == (
        "JURADO NACIONAL DE ELECCIONES", "10/11/2026", 16, 16)
    assert dict(c.conteo_por_tipo) == {"RESOLUCION": 16}
    assert sum(len(v) for v in c.por_tipo.values()) == 16
    # La entrada agrupada reemplaza las líneas del JNE, salvo su autorización de viaje,
    # que conserva línea propia porque lleva revisión detallada.
    jne_baja = [i for i in informe.baja if i.norma.entidad == "JURADO NACIONAL DE ELECCIONES"]
    assert [i.norma.titulo for i in jne_baja] == ["RESOLUCIÓN N° 000204-2026-P/JNE"]
    assert jne_baja[0].detalle


def test_concentracion_umbral_es_estrictamente_mayor(reglas):
    def lote(k):
        return [norma(entidad="JURADO NACIONAL DE ELECCIONES", titulo=f"RESOLUCIÓN N° {i}-2026",
                      sumilla="Confirman resolución", clave=f"o{i}") for i in range(k)]
    cinco = armar_informe("completo", lote(5), [], set(), reglas)
    assert cinco.concentradas == [] and len(cinco.baja) == 5
    seis = armar_informe("completo", lote(6), [], set(), reglas)
    assert len(seis.concentradas) == 1 and seis.baja == []
    assert seis.concentradas[0].total == 6


def test_concentracion_umbral_configurable(reglas):
    r = {**reglas, "concentracion": {"umbral": 2}}
    normas = [norma(entidad="ORGANISMO X", titulo=f"RESOLUCIÓN N° {i}-2026", clave=f"o{i}")
              for i in range(3)]
    assert len(armar_informe("completo", normas, [], set(), r).concentradas) == 1


def test_concentracion_cuenta_por_fecha_y_excluye_municipales(reglas):
    dos_fechas = ([norma(entidad="ORGANISMO X", titulo=f"RESOLUCIÓN N° {i}", clave=f"a{i}",
                         fecha="10/11/2026") for i in range(3)]
                  + [norma(entidad="ORGANISMO X", titulo=f"RESOLUCIÓN N° 9{i}", clave=f"b{i}",
                           fecha="11/11/2026") for i in range(3)])
    assert armar_informe("completo", dos_fechas, [], set(), reglas).concentradas == []
    muni = [norma(entidad="MUNICIPALIDAD DE LINCE", titulo=f"ORDENANZA N° {i}", clave=f"m{i}")
            for i in range(8)]
    assert armar_informe("completo", muni, [], set(), reglas).concentradas == []


def test_concentracion_de_entidad_prioritaria_solo_agrega_linea(reglas):
    mef = [norma(entidad="ECONOMÍA Y FINANZAS", titulo=f"RESOLUCIÓN MINISTERIAL N° {i}-2026-EF",
                 clave=f"e{i}") for i in range(7)]
    informe = armar_informe("completo", mef, [], set(), reglas)
    assert len(informe.institucional) == 7             # siguen en la primera sección
    assert len(informe.concentradas) == 1 and informe.concentradas[0].prioritaria
    assert informe.concentradas[0].total == 7


def test_concentracion_en_novedades_muestra_acumulado_y_nuevas(dia, reglas):
    jne = [n for n in dia if n.entidad == "JURADO NACIONAL DE ELECCIONES"]
    vistas = {n.clave for n in dia} - {n.clave for n in jne[:3]}
    informe = armar_informe("novedades", dia, [], vistas, reglas)
    c = informe.concentradas[0]
    assert (c.total, c.nuevas) == (16, 3)
    assert sum(len(v) for v in c.por_tipo.values()) == 3
    assert informe.hay_contenido()


# --- Estado y deduplicación entre ejecuciones -----------------------------------

def test_deduplicacion_entre_ejecuciones(dia, reglas, tmp_path):
    ruta = tmp_path / "vistas.json"
    hoy = date(2026, 11, 10)
    # 8:30: se envía todo lo publicado hasta entonces (las primeras 30).
    primera = armar_informe("completo", dia[:30], [], set(estado.cargar_vistas(ruta)), reglas)
    estado.guardar_vistas(estado.actualizar_vistas({}, primera.claves_revisadas, hoy), ruta)
    # 12:00: aparecen 15 más; solo esas son novedad.
    vistas = estado.cargar_vistas(ruta)
    segunda = armar_informe("novedades", dia, [], set(vistas), reglas)
    assert set(_todas_las_claves(segunda)).isdisjoint({n.clave for n in dia[:30]})
    assert segunda.hay_contenido()
    estado.guardar_vistas(estado.actualizar_vistas(vistas, segunda.claves_revisadas, hoy), ruta)
    # 15:30: nada nuevo.
    tercera = armar_informe("novedades", dia, [], set(estado.cargar_vistas(ruta)), reglas)
    assert not tercera.hay_contenido()


def test_estado_conserva_solo_diez_dias():
    hoy = date(2026, 11, 20)
    vistas = {"vieja": "2026-11-09", "limite": "2026-11-10", "reciente": "2026-11-19"}
    nuevas = estado.actualizar_vistas(vistas, ["hoy", "reciente"], hoy, dias=10)
    assert set(nuevas) == {"limite", "reciente", "hoy"}
    assert nuevas["reciente"] == "2026-11-19"          # no se pisa la fecha original


def test_estado_resumenes_ida_y_vuelta(tmp_path):
    ruta = tmp_path / "resumenes.json"
    datos = {"a": {"resumen": "x", "nota": "", "modelo": "m", "fecha": "2026-11-10"},
             "b": {"resumen": "y", "nota": "", "modelo": "m", "fecha": "2026-10-01"}}
    estado.guardar_resumenes(datos, date(2026, 11, 10), 10, ruta)
    assert set(estado.cargar_resumenes(ruta)) == {"a"}


# --- Resúmenes (con un cliente de Claude simulado) -------------------------------

class ClienteFalso:
    """Imita client.messages.create devolviendo las respuestas preparadas, en orden."""
    def __init__(self, respuestas):
        self.respuestas = list(respuestas)
        self.llamadas = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.llamadas.append(kwargs)
        r = self.respuestas.pop(0)
        if isinstance(r, Exception):
            raise r
        contenido = r if isinstance(r, str) else json.dumps(r(kwargs))
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=contenido)])


def _eco(kwargs):
    normas = json.loads(kwargs["messages"][0]["content"])["normas"]
    return {"resumenes": [{"clave": n["clave"], "resumen": f"Resumen de {n['clave']}",
                           "texto_insuficiente": False} for n in normas]}


def _items(dia, reglas):
    return armar_informe("completo", dia, [], set(), reglas).items_con_resumen()


def test_resumen_agrupa_llamadas_y_usa_cache(dia, reglas):
    items = _items(dia, reglas)
    textos = {i.norma.clave: "Texto completo de la norma." for i in items}
    cache: dict = {}
    cliente = ClienteFalso([_eco] * 10)
    errores = resumen.resumir(items, textos, cache, date(2026, 11, 10), reglas, cliente=cliente)
    assert errores == []
    por_llamada = reglas["resumenes"]["normas_por_llamada"]
    assert len(cliente.llamadas) == -(-len(items) // por_llamada)
    assert all(i.resumen == f"Resumen de {i.norma.clave}" for i in items)
    assert set(cache) == {i.norma.clave for i in items}
    # Segunda ejecución: todo sale del caché, sin llamadas nuevas.
    otro = ClienteFalso([])
    items2 = _items(dia, reglas)
    assert resumen.resumir(items2, {}, cache, date(2026, 11, 10), reglas, cliente=otro) == []
    assert otro.llamadas == [] and all(i.resumen.startswith("Resumen de") for i in items2)


def test_resumen_sin_texto_usa_sumilla_y_lo_declara(dia, reglas):
    items = _items(dia, reglas)
    cliente = ClienteFalso([])
    resumen.resumir(items, {}, {}, date(2026, 11, 10), reglas, cliente=cliente)
    assert cliente.llamadas == []                       # sin texto no se llama a Claude
    assert all(i.resumen == i.norma.sumilla for i in items)
    assert all(i.nota_resumen == resumen.NOTA_SOLO_TITULO for i in items)


def test_resumen_json_invalido_reintenta_y_luego_informa(dia, reglas):
    items = _items(dia, reglas)[:2]
    textos = {i.norma.clave: "Texto." for i in items}
    cache: dict = {}
    cliente = ClienteFalso(["esto no es json", '{"resumenes": []}'])
    errores = resumen.resumir(items, textos, cache, date(2026, 11, 10), reglas, cliente=cliente)
    assert len(cliente.llamadas) == 2 and len(errores) == 1
    assert cache == {}
    assert all(i.resumen == i.norma.sumilla and i.nota_resumen == resumen.NOTA_SIN_CLAUDE
               for i in items)


def test_resumen_marca_texto_cortado_e_insuficiente(dia, reglas):
    item = _items(dia, reglas)[0]
    r = {**reglas, "resumenes": {**reglas["resumenes"], "max_caracteres_por_norma": 10}}
    def respuesta(kwargs):
        n = json.loads(kwargs["messages"][0]["content"])["normas"][0]
        assert len(n["texto"]) == 10 and n["texto_cortado"] is True
        return {"resumenes": [{"clave": n["clave"], "resumen": "No se puede determinar.",
                               "texto_insuficiente": True}]}
    resumen.resumir([item], {item.norma.clave: "x" * 500}, {}, date(2026, 11, 10), r,
                    cliente=ClienteFalso([respuesta]))
    assert resumen.NOTA_TEXTO_PARCIAL in item.nota_resumen
    assert resumen.NOTA_INSUFICIENTE in item.nota_resumen


def test_validar_respuesta_rechaza_claves_ajenas_o_faltantes():
    ok = '{"resumenes":[{"clave":"a","resumen":"r","texto_insuficiente":false}]}'
    assert resumen.validar_respuesta(ok, ["a"])["a"]["resumen"] == "r"
    with pytest.raises(resumen.ErrorDeResumen):
        resumen.validar_respuesta(ok, ["a", "b"])
    with pytest.raises(resumen.ErrorDeResumen):
        resumen.validar_respuesta(ok, ["z"])


# --- Correo ----------------------------------------------------------------------

def _ctx(errores=None):
    utc = datetime(2026, 11, 10, 13, 52, tzinfo=timezone.utc)
    return correo.Contexto(ahora_lima=utc.astimezone(ZoneInfo("America/Lima")), ahora_utc=utc,
                           fecha_consultada="10/11/2026", errores=errores or [])


def test_correo_asunto_y_orden_de_secciones(dia, extra, reglas):
    ayer = [replace(n, clave="ayer-" + n.clave, fecha="09/11/2026") for n in extra]
    informe = armar_informe("completo", dia, ayer, set(), reglas)
    resumen.resumir(informe.items_con_resumen(), {}, {}, date(2026, 11, 10), reglas,
                    cliente=ClienteFalso([]))
    ctx = _ctx(["error de ejemplo"])
    assert correo.asunto(ctx) == "Normas El Peruano — 10/11/2026, 08:52"
    texto, pagina = correo.texto_plano(informe, ctx), correo.html(informe, ctx)
    orden = ["1. PCM, MEF Y DECRETOS LEGISLATIVOS", "2. EDICIÓN EXTRAORDINARIA DE AYER", "3. PRIORIDAD ALTA",
             "4. PRIORIDAD MEDIA", "5. INSTITUCIONES CON MUCHAS NORMAS", "6. PRIORIDAD BAJA"]
    posiciones = [texto.index(t) for t in orden]
    assert posiciones == sorted(posiciones)
    for cuerpo in (texto, pagina):
        assert "Se omitieron 3 normas municipales" in cuerpo
        assert "Normas revisadas: 52" in cuerpo
        assert "10/11/2026 08:52 (Lima) / 10/11/2026 13:52 (UTC)" in cuerpo
        assert "error de ejemplo" in cuerpo
        assert "JURADO NACIONAL DE ELECCIONES — 10/11/2026: 16 normas" in cuerpo
        assert "https://busquedas.elperuano.pe/dispositivo/NL/" in cuerpo
        assert "MUNICIPALIDAD DE LINCE" not in cuerpo
    assert "Edición extraordinaria" in texto              # etiqueta de las extraordinarias de hoy
    assert resumen.NOTA_SOLO_TITULO in texto


def test_correo_escapa_html(reglas):
    n = norma(entidad="SALUD <b>", sumilla="Aprueban <script>alert(1)</script>")
    informe = armar_informe("completo", [n], [], set(), reglas)
    pagina = correo.html(informe, _ctx())
    assert "<script>" not in pagina and "SALUD &lt;b&gt;" in pagina


# --- Normas ordinarias de ayer que no alcanzaron a salir -------------------------

def test_completo_recoge_ordinarias_de_ayer_no_vistas(regionales, reglas):
    ayer = regionales                                  # día completo del 05/11/2026
    ordinarias = [n for n in ayer if not n.extraordinaria]
    extras = [n for n in ayer if n.extraordinaria]
    tardias = [n for n in ordinarias if not es_municipal_excluida(n, reglas)][-4:]
    vistas = {n.clave for n in ayer} - {n.clave for n in tardias}
    hoy = [norma(entidad="SALUD", fecha="06/11/2026", clave="hoy-1")]
    informe = armar_informe("completo", hoy, ayer, vistas, reglas)
    individuales = (informe.institucional + informe.extraordinaria_ayer + informe.alta
                    + informe.media + informe.baja)
    agrupadas = {n.clave for c in informe.concentradas for ns in c.por_tipo.values() for n in ns}
    enviadas = {i.norma.clave for i in individuales} | agrupadas
    assert enviadas == {"hoy-1"} | {n.clave for n in tardias} | {
        n.clave for n in extras if not es_municipal_excluida(n, reglas)}
    assert {i.norma.clave for i in individuales if i.de_ayer} <= {n.clave for n in tardias}
    assert informe.revisadas == len(ayer) + 1
    # Las ordinarias de ayer no entran en la sección de la edición extraordinaria.
    assert all(i.norma.extraordinaria for i in informe.extraordinaria_ayer)


def test_novedades_no_mira_ayer(regionales, reglas):
    informe = armar_informe("novedades", [], regionales, set(), reglas)
    assert not informe.hay_contenido() and informe.revisadas == 0


def test_designaciones_de_entidad_concentrada_conservan_linea_propia(reglas):
    def lote(entidad):
        return [norma(entidad=entidad, titulo=f"RESOLUCIÓN N° {i}-2026",
                      sumilla="Designan Gerente General", clave=f"d{i}") for i in range(7)]
    informe = armar_informe("completo", lote("CONTRALORÍA GENERAL DE LA REPÚBLICA"), [], set(),
                            reglas)
    assert len(informe.concentradas) == 1 and len(informe.baja) == 7
    assert all(i.detalle for i in informe.baja)
    # Un organismo fuera de los tres grupos: sin detalle, lo reemplaza la entrada agrupada.
    otro = armar_informe("completo", lote("INSTITUCIONES EDUCATIVAS"), [], set(), reglas)
    assert len(otro.concentradas) == 1 and otro.baja == []
    assert otro.items_con_resumen() == []


def test_normas_sin_detalle_muestran_la_sumilla_sin_llamar_a_claude(dia, reglas):
    informe = armar_informe("completo", dia, [], set(), reglas)
    texto = correo.texto_plano(informe, _ctx())
    ds = next(i for i in informe.media if "009-2026-IN" in i.norma.titulo)
    assert not ds.detalle and ds.norma.sumilla in texto
    assert correo.AVISO_ALCANCE in texto


# --- Resumen agregado de fiscales del Ministerio Público -------------------------

def test_fiscales_se_agregan_por_accion_y_distrito(regionales, reglas):
    informe = armar_informe("completo", regionales, [], set(), reglas)
    assert len(informe.agregados) == 1
    a = informe.agregados[0]
    assert (a.entidad, a.etiqueta, a.fecha) == ("MINISTERIO PÚBLICO", "Fiscales", "05/11/2026")
    assert len(a.normas) == 31                         # 32 del MP menos "Dejan sin efecto..."
    assert sum(1 for n in a.normas if n.sumilla.startswith("Nombran")) == 23
    assert a.conteo["Nombramientos"]["Callao"] == 3
    assert a.conteo["Nombramientos"]["Lima Centro"] == 4      # incluye la resolución plural
    assert a.conteo["Nombramientos"]["Lima Este"] == 2
    assert a.conteo["Nombramientos"]["La Libertad"] == 1
    assert a.conteo["Nombramientos"]["Selva Central"] == 1
    assert a.conteo["Renuncias aceptadas"] == {
        "Puno": 1, "Arequipa": 1, "Lima Norte": 2, "Selva Central": 1, "Lima": 1}
    assert a.conteo["Designaciones"]["Apurímac"] == 1
    # No se abren sus páginas ni aparecen una por una.
    claves = {n.clave for n in a.normas}
    assert claves.isdisjoint({i.norma.clave for i in informe.items_con_resumen()})
    assert claves.isdisjoint({i.norma.clave for i in informe.baja + informe.media})
    assert a.enlaces_en_concentracion                  # el MP supera el umbral ese día
    texto = correo.texto_plano(informe, _ctx())
    assert "MINISTERIO PÚBLICO — Fiscales (05/11/2026): 31 resoluciones" in texto
    assert "Nombramientos: 23" in texto and "Renuncias aceptadas: 6" in texto


def test_fiscales_en_novedades_solo_cuenta_las_nuevas(regionales, reglas):
    mp = [n for n in regionales if n.entidad == "MINISTERIO PÚBLICO"]
    vistas = {n.clave for n in regionales} - {n.clave for n in mp[:2]}
    informe = armar_informe("novedades", regionales, [], vistas, reglas)
    assert [len(a.normas) for a in informe.agregados] == [2]


def test_decreto_legislativo_va_a_la_primera_seccion_con_detalle(reglas):
    dl = norma(entidad="DECRETOS LEGISLATIVOS", titulo="DECRETO LEGISLATIVO N° 1700",
               sumilla="Decreto Legislativo que modifica el Código Penal", clave="dl1")
    otro = norma(entidad="INTERIOR", titulo="DECRETO LEGISLATIVO N° 1701",
                 sumilla="Decreto Legislativo que fortalece la Policía", clave="dl2")
    ley = norma(entidad="CONGRESO DE LA REPÚBLICA", titulo="LEY N° 32600",
                sumilla="Ley que modifica algo", clave="ley1")
    informe = armar_informe("completo", [dl, otro, ley], [], set(), reglas)
    assert [i.norma.clave for i in informe.institucional] == ["dl1", "dl2"]
    assert all(i.detalle for i in informe.institucional)
    assert [i.norma.clave for i in informe.alta] == ["ley1"]
    assert "1. PCM, MEF Y DECRETOS LEGISLATIVOS" in correo.texto_plano(informe, _ctx())


# --- Franjas horarias y respaldos -------------------------------------------------

@pytest.mark.parametrize("hora,hechas,pendientes,modo", [
    ("08:29", [], [], None),
    ("08:30", [], ["08:30"], "completo"),
    ("09:15", ["08:30"], [], None),                       # respaldo: ya se atendió, no hace nada
    ("09:15", [], ["08:30"], "completo"),                 # respaldo: cubre la franja omitida
    ("12:05", ["08:30"], ["12:00"], "novedades"),
    ("12:05", [], ["08:30", "12:00"], "completo"),        # se perdió la de 8:30: correo completo
    ("17:00", ["08:30", "12:00"], ["15:30"], "novedades"),
    ("17:00", ["08:30", "12:00", "15:30"], [], None),
])
def test_franjas_pendientes_y_modo(hora, hechas, pendientes, modo):
    assert estado.franjas_pendientes(hora, hechas) == pendientes
    assert estado.modo_automatico(pendientes) == modo


def test_franjas_se_reinician_cada_dia(tmp_path):
    ruta = tmp_path / "franjas.json"
    estado.guardar_franjas(date(2026, 11, 11), ["12:00", "08:30", "08:30"], ruta)
    assert estado.cargar_franjas_hechas(date(2026, 11, 11), ruta) == ["08:30", "12:00"]
    assert estado.cargar_franjas_hechas(date(2026, 11, 12), ruta) == []
    assert estado.cargar_franjas_hechas(date(2026, 11, 11), tmp_path / "no-existe.json") == []
