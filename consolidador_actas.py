import re
from datetime import date

import pandas as pd
import streamlit as st

# Formatos de nombre soportados (fecha de 8 dígitos DDMMAAAA o AAAAMMDD, en cualquier posición):
#   ActMto_99773-1018_Anselmo Hernandez Chipaje_22052024
#   ActadeMantenimiento-Ruben Rodriguez Gaitan-10072024
#   ActMtto_Pr_01082025_20238-1051_CelsoMiguelHernandezAvila
#   ActMtto_Pr_20250721_94888-1199   (sin cliente, fecha AAAAMMDD)
PREFIJO = re.compile(r"^Act[a-z]*(?:\s*de\s*Mantenimiento)?", re.IGNORECASE)
FECHA8 = re.compile(r"(?<!\d)(\d{8})(?!\d)")
CODIGO = re.compile(r"(?<!\d)(\d{4,6}-\d{3,4})(?!\d)")
MAYUS_TRAS_MINUS = re.compile(r"(?<=[a-záéíóúñü])(?=[A-ZÁÉÍÓÚÑÜ])")

MESES = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio",
    7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}


def _interpretar_fecha(s: str):
    """Convierte un texto de 8 dígitos a fecha, probando DDMMAAAA y luego AAAAMMDD."""
    candidatos = (
        (int(s[4:8]), int(s[2:4]), int(s[0:2])),  # DDMMAAAA -> (año, mes, día)
        (int(s[0:4]), int(s[4:6]), int(s[6:8])),  # AAAAMMDD -> (año, mes, día)
    )
    for y, mo, d in candidatos:
        if not 2000 <= y <= 2100:
            continue
        try:
            return date(y, mo, d)
        except ValueError:
            continue
    return None


def parsear_nombre(nombre: str):
    """Devuelve dict con codigo, cliente, tipo y fecha; None si no se reconoce la fecha."""
    base = re.sub(r"\.pdf$", "", nombre.strip(), flags=re.IGNORECASE)
    base = re.sub(r"\s*\(\d+\)$", "", base)  # sufijo " (1)" de descargas repetidas

    m_pref = PREFIJO.match(base)
    if not m_pref:
        return None
    resto = base[m_pref.end():]

    # 1) Fecha de 8 dígitos: DDMMAAAA o AAAAMMDD (la primera que sea una fecha real).
    #    Si ambos órdenes fueran válidos a la vez (muy raro), se prefiere DDMMAAAA.
    fecha = None
    for m in FECHA8.finditer(resto):
        fecha = _interpretar_fecha(m.group(1))
        if fecha is not None:
            resto = resto[:m.start()] + "_" + resto[m.end():]
            break
    if fecha is None:
        return None

    # 2) Código (ej. 99773-1018); es opcional
    codigo = ""
    m_cod = CODIGO.search(resto)
    if m_cod:
        codigo = m_cod.group(1)
        resto = resto[:m_cod.start()] + "_" + resto[m_cod.end():]

    # 3) Lo que queda: tokens cortos (<=3 letras, ej. "Pr") = tipo; el resto = cliente
    tokens = [t.strip() for t in re.split(r"[_\-]+", resto) if t.strip()]
    tipo = " ".join(t for t in tokens if len(t) <= 3 and " " not in t)
    partes = []
    for t in tokens:
        if len(t) <= 3 and " " not in t:
            continue
        if " " not in t:
            t = MAYUS_TRAS_MINUS.sub(" ", t)  # CelsoMiguelHernandez -> Celso Miguel Hernandez
        partes.append(t)
    cliente = " ".join(partes) or "(sin cliente)"

    return {"codigo": codigo, "cliente": cliente, "tipo": tipo, "fecha": fecha}


def clasificar(archivos):
    """Separa los archivos en reconocidos (con fecha) y no reconocidos."""
    ok, fallidos = [], []
    for f in archivos:
        datos = parsear_nombre(f.name)
        if datos is None:
            fallidos.append(f.name)
        else:
            ok.append({
                "archivo": f.name, **datos,
                "anio": datos["fecha"].year, "mes": datos["fecha"].month,
            })
    cols = ["archivo", "codigo", "cliente", "tipo", "fecha", "anio", "mes"]
    df = pd.DataFrame(ok, columns=cols)
    if not df.empty:
        df = df.sort_values(["fecha", "cliente"]).reset_index(drop=True)
    return df, fallidos


def main():
    st.set_page_config(page_title="Consolidador de actas", page_icon="📄", layout="wide")
    st.title("📄 Consolidador de actas de mantenimiento")

    archivos = st.file_uploader(
        "Sube las actas en PDF", type=["pdf"], accept_multiple_files=True
    )
    if not archivos:
        st.info("Sube uno o más PDFs. El nombre debe empezar con `Act...` y contener una fecha de 8 dígitos (`DDMMAAAA` o `AAAAMMDD`).")
        return

    df, fallidos = clasificar(archivos)

    c1, c2, c3 = st.columns(3)
    c1.metric("PDFs subidos", len(archivos))
    c2.metric("Con fecha reconocida", len(df))
    c3.metric("Sin fecha reconocida", len(fallidos))

    if fallidos:
        st.warning("Estos archivos no se pudieron leer (falta el prefijo `Act...` o una fecha válida de 8 dígitos):")
        st.write(fallidos)

    if not df.empty:
        vista = df[["archivo", "codigo", "cliente", "tipo", "fecha", "anio", "mes"]].copy()
        vista["fecha"] = vista["fecha"].map(lambda d: d.strftime("%d/%m/%Y"))
        vista["mes"] = vista["mes"].map(MESES)
        vista.columns = ["Archivo", "Código", "Cliente", "Tipo", "Fecha", "Año", "Mes"]
        st.subheader("Actas detectadas")
        st.dataframe(vista, width="stretch", hide_index=True)


if __name__ == "__main__":
    main()
