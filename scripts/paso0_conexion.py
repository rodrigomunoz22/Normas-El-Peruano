"""Paso 0: comprueba que El Peruano responde desde el runner de GitHub.

Hace tres peticiones: (1) todo lo de hoy, (2) la edición extraordinaria de ayer
(tipo=on) y (3) el texto completo de una norma. Termina con error si algo falla.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from normas.fuente import Fuente  # noqa: E402

ahora = datetime.now(ZoneInfo("America/Lima"))
ayer = ahora.date() - timedelta(days=1)
fuente = Fuente()
print(f"Hora de Lima: {ahora:%d/%m/%Y %H:%M}")

hoy = fuente.normas_de_hoy()
print(f"[1] Hoy completo (GET LoadNormasLegales): {len(hoy)} normas, "
      f"{sum(n.extraordinaria for n in hoy)} extraordinarias")

extra = fuente.normas_por_fecha(ayer, solo_extraordinarias=True)
print(f"[2] Ayer {ayer:%d/%m/%Y} con tipo=on (POST Filtro): {len(extra)} normas")

con_pagina = [n for n in hoy + extra if "busquedas.elperuano.pe/dispositivo/" in n.url]
if con_pagina:
    texto = fuente.texto_de_norma(con_pagina[0])
    print(f"[3] Texto completo de {con_pagina[0].titulo}: {len(texto)} caracteres")
    if not texto:
        sys.exit("ERROR: la página de la norma respondió, pero sin el texto.")
else:
    print("[3] No hay normas con enlace para probar el texto completo (se omite).")
print("Paso 0 correcto: el sitio responde desde este servidor.")
