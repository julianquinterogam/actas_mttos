import io
import re
from datetime import date

import pandas as pd
import streamlit as st
from pypdf import PdfReader, PdfWriter

# Formatos de nombre soportados (fecha de 8 dígitos DDMMAAAA o AAAAMMDD, en cualquier posición):
#   ActMto_99773-1018_Anselmo Hernandez Chipaje_22052024
#   ActadeMantenimiento-Ruben Rodriguez Gaitan-10072024
#   ActMtto_Pr_01082025_20238-1051_CelsoMiguelHernandezAvila
#   ActMtto_Pr_20250721_94888-1199   (sin cliente, fecha AAAAMMDD)
#   FO-25-1709555_06112025           (sin prefijo Act; el código es "FO-25-1709555")
PREFIJO = re.compile(r"^Act[a-z]*(?:\s*de\s*Mantenimiento)?", re.IGNORECASE)
PREFIJO_FO = re.compile(r"^FO-\d+-\d+", re.IGNORECASE)
SIN_CLIENTE = "(sin cliente)"
FECHA8 = re.compile(r"(?<!\d)(\d{8})(?!\d)")
CODIGO = re.compile(r"(?<!\d)(\d{4,6}-\d{3,4})(?!\d)")
MAYUS_TRAS_MINUS = re.compile(r"(?<=[a-záéíóúñü])(?=[A-ZÁÉÍÓÚÑÜ])")

MESES = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio",
    7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}


# ---------------------------------------------------------------- lectura de nombres
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

    codigo = ""
    m_fo = PREFIJO_FO.match(base)
    if m_fo:  # formato FO-25-1709555_06112025: el propio prefijo es el código
        codigo = m_fo.group(0).upper()
        resto = base[m_fo.end():]
    else:
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
    m_cod = None if codigo else CODIGO.search(resto)
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
    cliente = " ".join(partes) or SIN_CLIENTE

    return {"codigo": codigo, "cliente": cliente, "tipo": tipo, "fecha": fecha}


def clasificar(archivos):
    """Separa los archivos en reconocidos (con fecha) y no reconocidos."""
    ok, fallidos = [], []
    for i, f in enumerate(archivos):
        datos = parsear_nombre(f.name)
        if datos is None:
            fallidos.append(f.name)
        else:
            ok.append({
                "idx": i, "archivo": f.name, **datos,
                "anio": datos["fecha"].year, "mes": datos["fecha"].month,
            })
    cols = ["idx", "archivo", "codigo", "cliente", "tipo", "fecha", "anio", "mes"]
    df = pd.DataFrame(ok, columns=cols)
    if not df.empty:
        df = df.sort_values(["fecha", "cliente", "archivo"]).reset_index(drop=True)
    return df, fallidos


# ---------------------------------------------------------------- unión de PDFs
@st.cache_data(show_spinner="Uniendo PDFs...")
def unir_pdfs(items):
    """items: tupla de (título_marcador, bytes_pdf), ya en el orden final.

    Devuelve (pdf_bytes | None, total_páginas, errores[(título, motivo)]).
    """
    writer = PdfWriter()
    errores = []
    for titulo, datos in items:
        try:
            reader = PdfReader(io.BytesIO(datos))
            if reader.is_encrypted and not reader.decrypt(""):
                raise ValueError("está protegido con contraseña")
            if len(reader.pages) == 0:
                raise ValueError("no tiene páginas")
            writer.append(reader, outline_item=titulo)  # marcador por acta
        except Exception as e:  # PDF dañado, protegido, etc.
            errores.append((titulo, str(e) or type(e).__name__))
    if len(writer.pages) == 0:
        return None, 0, errores
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue(), len(writer.pages), errores


def n_actas(n: int) -> str:
    return f"{n} acta" if n == 1 else f"{n} actas"


def titulo_marcador(fila) -> str:
    if fila.cliente == SIN_CLIENTE:  # sin nombre: se identifica por el código
        return f"{fila.fecha:%d/%m/%Y} - Código {fila.codigo or fila.archivo}"
    extra = f" ({fila.codigo})" if fila.codigo else ""
    return f"{fila.fecha:%d/%m/%Y} - {fila.cliente}{extra}"


def formatear_vista(df: pd.DataFrame) -> pd.DataFrame:
    vista = df[["archivo", "codigo", "cliente", "tipo", "fecha", "anio", "mes"]].copy()
    vista["fecha"] = vista["fecha"].map(lambda d: d.strftime("%d/%m/%Y"))
    vista["mes"] = vista["mes"].map(MESES)
    vista.columns = ["Archivo", "Código", "Cliente", "Tipo", "Fecha", "Año", "Mes"]
    return vista


# ---------------------------------------------------------------- interfaz
def main():
    st.set_page_config(page_title="Consolidado Actas de Mantenimientos", page_icon="📄", layout="wide")
    st.title("📄 Consolidado Actas de Mantenimientos")

    archivos = st.file_uploader(
        "Sube las actas en PDF", type=["pdf"], accept_multiple_files=True
    )
    if not archivos:
        st.info("Sube uno o más PDFs. El nombre debe empezar con `Act...` o `FO-..-...` y contener una fecha de 8 dígitos (`DDMMAAAA` o `AAAAMMDD`).")
        return

    df, fallidos = clasificar(archivos)

    c1, c2, c3 = st.columns(3)
    c1.metric("PDFs subidos", len(archivos))
    c2.metric("Con fecha reconocida", len(df))
    c3.metric("Sin fecha reconocida", len(fallidos))

    if fallidos:
        st.warning("Estos archivos no se pudieron leer (falta el prefijo `Act...` / `FO-..-...` o una fecha válida de 8 dígitos):")
        st.write(fallidos)

    if df.empty:
        return

    with st.expander(f"Ver todas las actas detectadas ({len(df)})"):
        st.dataframe(formatear_vista(df), width="stretch", hide_index=True)

    # ---- Filtro por año y mes
    st.subheader("Filtrar y descargar")
    anios = sorted(df["anio"].unique(), reverse=True)
    col_a, col_m = st.columns(2)
    anio = col_a.selectbox("Año", anios)

    meses_del_anio = sorted(df.loc[df["anio"] == anio, "mes"].unique())
    conteo = df[df["anio"] == anio]["mes"].value_counts()
    mes = col_m.selectbox(
        "Mes",
        meses_del_anio,
        index=len(meses_del_anio) - 1,  # por defecto, el más reciente
        format_func=lambda m: f"{MESES[m]} ({n_actas(conteo[m])})",
    )

    sel = df[(df["anio"] == anio) & (df["mes"] == mes)]
    st.write(f"**{n_actas(len(sel))} de {MESES[mes]} {anio}** (ordenadas por fecha)")
    st.dataframe(formatear_vista(sel), width="stretch", hide_index=True)

    # ---- Unir y descargar
    items = tuple(
        (titulo_marcador(fila), archivos[fila.idx].getvalue())
        for fila in sel.itertuples()
    )
    pdf_bytes, paginas, errores = unir_pdfs(items)

    if errores:
        st.error("Estos PDFs no se pudieron incluir en el archivo unido:")
        for titulo, motivo in errores:
            st.write(f"- **{titulo}**: {motivo}")

    if pdf_bytes is None:
        st.error("No se pudo generar el PDF unido con la selección actual.")
        return

    st.caption(f"El PDF unido tendrá {n_actas(len(items) - len(errores))} y {paginas} {'página' if paginas == 1 else 'páginas'}, con un marcador por acta.")
    st.download_button(
        label=f"⬇️ Descargar PDF de {MESES[mes]} {anio}",
        data=pdf_bytes,
        file_name=f"Actas_Mantenimiento_{MESES[mes]}_{anio}.pdf",
        mime="application/pdf",
        type="primary",
    )


if __name__ == "__main__":
    main()
