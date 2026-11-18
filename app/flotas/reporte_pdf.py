"""Reporte de desempeño en PDF (HU-27), con los colores y el estilo de la app.

Usa fpdf2 con Helvetica (cubre las tildes del español); el texto se pasa por `_t`, que
reemplaza lo que no entre en Latin-1 (p. ej. emojis en un nombre).
"""

from datetime import datetime

from fpdf import FPDF

from app.flotas.reporte import (
    NOMBRE_CALIFICACION,
    NOMBRE_NIVEL,
    NOMBRE_TIPO_EVENTO,
    ReporteConductor,
    ReporteFlota,
)
from app.flotas.schemas import NivelRiesgo
from app.puntaje.service import Calificacion

# Tokens de docs/diseno.md del panel
OSCURO = (0x1E, 0x25, 0x2D)
ACENTO = (0x34, 0xD3, 0x99)
TEXTO = (0x1E, 0x25, 0x2D)
TEXTO_SEC = (0x5F, 0x6B, 0x76)
FONDO = (0xEF, 0xEB, 0xE1)
SUPERFICIE_ALT = (0xF9, 0xF7, 0xF2)
BORDE = (0xE2, 0xDC, 0xCF)
CARRIL = (0xE8, 0xE2, 0xD4)
PRIMARIO = (0x04, 0x78, 0x57)
BLANCO = (255, 255, 255)

# Calificación: (texto, tinte)
COLOR_CALIFICACION = {
    Calificacion.excelente: ((0x04, 0x78, 0x57), (0xE7, 0xF8, 0xF2)),
    Calificacion.muy_bueno: ((0x03, 0x69, 0xA1), (0xE2, 0xF4, 0xFC)),
    Calificacion.regular: ((0xB4, 0x53, 0x09), (0xFE, 0xF5, 0xE7)),
    Calificacion.riesgoso: ((0xB9, 0x1C, 0x1C), (0xFD, 0xEC, 0xEC)),
}
COLOR_NIVEL = {
    NivelRiesgo.alto: COLOR_CALIFICACION[Calificacion.riesgoso],
    NivelRiesgo.medio: COLOR_CALIFICACION[Calificacion.regular],
    NivelRiesgo.bajo: COLOR_CALIFICACION[Calificacion.excelente],
}
# Barras de progreso: >= 90 verde, 70-89 ámbar, < 70 rojo
VERDE, AMBAR, ROJO = (0x10, 0xB9, 0x81), (0xF5, 0x9E, 0x0B), (0xEF, 0x44, 0x44)

MARGEN = 15
ANCHO = 210 - 2 * MARGEN
ALTO_FILA = 8


def _t(texto) -> str:
    return str(texto).encode("latin-1", "replace").decode("latin-1")


def _num(valor: float, decimales: int = 1) -> str:
    """Coma decimal, como el panel (es-BO)."""
    return f"{valor:.{decimales}f}".replace(".", ",")


def _km(distancia_m: float) -> str:
    return f"{_num(distancia_m / 1000)} km"


def _duracion(segundos: int) -> str:
    horas, resto = divmod(segundos, 3600)
    minutos = resto // 60
    return f"{horas} h {minutos:02d} min" if horas else f"{minutos} min"


def _fecha_hora(fecha: datetime) -> str:
    return fecha.strftime("%d/%m/%Y %H:%M")


def _color_barra(puntaje: int) -> tuple[int, int, int]:
    return VERDE if puntaje >= 90 else AMBAR if puntaje >= 70 else ROJO


class _Reporte(FPDF):
    def __init__(self, titulo: str, empresa: str, generado: datetime, periodo: str):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.titulo, self.empresa, self.generado, self.periodo = titulo, empresa, generado, periodo
        self.set_margins(MARGEN, 26, MARGEN)
        self.set_auto_page_break(True, 20)
        self.set_title(_t(f"{titulo} - {empresa}"))
        self.set_author("DriveSense")
        self.alias_nb_pages()
        self.add_page()

    def header(self):
        self.set_fill_color(*OSCURO)
        self.rect(0, 0, 210, 18, style="F")
        self.set_xy(MARGEN, 5)
        self.set_text_color(*ACENTO)
        self.set_font("Helvetica", "B", 13)
        self.cell(60, 8, "DriveSense")
        self.set_text_color(243, 244, 246)
        self.set_font("Helvetica", "", 9)
        self.cell(ANCHO - 60, 8, _t(self.empresa), align="R")
        self.set_y(26)

    def footer(self):
        self.set_y(-14)
        self.set_draw_color(*BORDE)
        self.line(MARGEN, self.get_y(), 210 - MARGEN, self.get_y())
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*TEXTO_SEC)
        self.cell(
            ANCHO - 30,
            8,
            _t(f"{self.empresa} - Generado el {_fecha_hora(self.generado)} - {self.periodo}"),
        )
        self.cell(30, 8, f"Página {self.page_no()} de {{nb}}", align="R")

    # --- Bloques -------------------------------------------------------------

    def titulo_principal(self, subtitulo: str):
        self.set_font("Helvetica", "B", 20)
        self.set_text_color(*TEXTO)
        self.cell(0, 10, _t(self.titulo), new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "B", 13)
        self.set_text_color(*PRIMARIO)
        self.cell(0, 7, _t(subtitulo), new_x="LMARGIN", new_y="NEXT")
        self.ln(3)

    def seccion(self, texto: str):
        if self.will_page_break(30):
            self.add_page()
        self.ln(4)
        self.set_font("Helvetica", "B", 12)
        self.set_text_color(*TEXTO)
        self.cell(0, 7, _t(texto), new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*ACENTO)
        self.set_line_width(0.6)
        self.line(MARGEN, self.get_y(), MARGEN + 14, self.get_y())
        self.set_line_width(0.2)
        self.ln(3)

    def tarjeta_datos(self, filas: list[tuple[str, str]]):
        alto = len(filas) * 6.5 + 4
        y = self.get_y()
        self.set_fill_color(*SUPERFICIE_ALT)
        self.set_draw_color(*BORDE)
        self.rect(MARGEN, y, ANCHO, alto, style="DF", round_corners=True, corner_radius=3)
        self.set_xy(MARGEN + 4, y + 2)
        for etiqueta, valor in filas:
            self.set_x(MARGEN + 4)
            self.set_font("Helvetica", "B", 8)
            self.set_text_color(*TEXTO_SEC)
            self.cell(42, 6.5, _t(etiqueta.upper()))
            self.set_font("Helvetica", "", 10)
            self.set_text_color(*TEXTO)
            self.cell(ANCHO - 50, 6.5, _t(valor), new_x="LMARGIN", new_y="NEXT")
        self.set_y(y + alto)

    def kpis(self, tarjetas: list[tuple[str, str, tuple | None]]):
        """Fila de tarjetas (etiqueta, valor, color del valor)."""
        ancho = (ANCHO - 4 * (len(tarjetas) - 1)) / len(tarjetas)
        y = self.get_y() + 2
        for i, (etiqueta, valor, color) in enumerate(tarjetas):
            x = MARGEN + i * (ancho + 4)
            self.set_fill_color(*BLANCO)
            self.set_draw_color(*BORDE)
            self.rect(x, y, ancho, 22, style="DF", round_corners=True, corner_radius=3)
            self.set_xy(x, y + 3)
            self.set_font("Helvetica", "B", 7)
            self.set_text_color(*TEXTO_SEC)
            self.cell(ancho, 4, _t(etiqueta.upper()), align="C")
            self.set_xy(x, y + 9)
            self.set_font("Helvetica", "B", 15)
            self.set_text_color(*(color or TEXTO))
            self.cell(ancho, 10, _t(valor), align="C")
        self.set_y(y + 24)

    def encabezado_tabla(self, columnas: list[tuple[str, float, str]]):
        self.set_fill_color(*OSCURO)
        self.set_text_color(243, 244, 246)
        self.set_draw_color(*OSCURO)
        self.set_font("Helvetica", "B", 8)
        for nombre, ancho, alineacion in columnas:
            self.cell(ancho, 8, _t(nombre.upper()), border=1, fill=True, align=alineacion)
        self.ln()

    def tabla(
        self,
        columnas: list[tuple[str, float, str]],
        filas: list[list],
        estilos: dict[tuple[int, int], tuple[tuple, tuple]] | None = None,
    ):
        """`estilos[(fila, columna)] = (color de texto, fondo)` para chips de calificación."""
        estilos = estilos or {}
        self.encabezado_tabla(columnas)
        self.set_draw_color(*BORDE)
        for i, fila in enumerate(filas):
            if self.will_page_break(ALTO_FILA):
                self.add_page()
                self.encabezado_tabla(columnas)
                self.set_draw_color(*BORDE)
            base = SUPERFICIE_ALT if i % 2 else BLANCO
            for j, (valor, (_, ancho, alineacion)) in enumerate(zip(fila, columnas)):
                texto, fondo = estilos.get((i, j), (TEXTO, base))
                self.set_fill_color(*fondo)
                self.set_text_color(*texto)
                self.set_font("Helvetica", "B" if (i, j) in estilos else "", 9)
                self.cell(ancho, ALTO_FILA, _t(valor), border=1, fill=True, align=alineacion)
            self.ln()

    def barras(self, datos: dict[str, int]):
        for nombre, puntaje in datos.items():
            y = self.get_y()
            self.set_font("Helvetica", "", 10)
            self.set_text_color(*TEXTO)
            self.cell(40, 7, _t(nombre))
            self.set_fill_color(*CARRIL)
            self.rect(MARGEN + 42, y + 2.2, 100, 2.8, style="F", round_corners=True, corner_radius=1.4)
            self.set_fill_color(*_color_barra(puntaje))
            self.rect(
                MARGEN + 42,
                y + 2.2,
                max(2.8, puntaje),
                2.8,
                style="F",
                round_corners=True,
                corner_radius=1.4,
            )
            self.set_xy(MARGEN + 146, y)
            self.set_font("Helvetica", "B", 10)
            self.cell(20, 7, str(puntaje), new_x="LMARGIN", new_y="NEXT")


def _color_calificacion(calificacion: Calificacion | None) -> tuple | None:
    return COLOR_CALIFICACION[calificacion][0] if calificacion else None


def _resumen_eventos(pdf: _Reporte, por_tipo: dict, total: int, por_10km: float | None):
    pdf.seccion("Eventos de riesgo")
    pdf.tabla(
        [("Tipo de evento", 110, "L"), ("Cantidad", 70, "R")],
        [[NOMBRE_TIPO_EVENTO[t], str(n)] for t, n in por_tipo.items()]
        + [["Total", str(total)]],
    )
    pdf.ln(2)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*TEXTO_SEC)
    pdf.cell(
        0,
        6,
        _t(f"Eventos por cada 10 km: {_num(por_10km) if por_10km is not None else '-'}"),
        new_x="LMARGIN",
        new_y="NEXT",
    )


def pdf_conductor(r: ReporteConductor) -> bytes:
    pdf = _Reporte("Reporte de desempeño", r.empresa.nombre, r.generado, r.periodo)
    pdf.titulo_principal(r.nombre)
    pdf.tarjeta_datos(
        [
            ("Empresa", r.empresa.nombre),
            ("Conductor", r.nombre),
            ("Correo", r.email),
            ("Teléfono", r.telefono),
            ("Periodo", r.periodo),
            ("Generado", _fecha_hora(r.generado)),
        ]
    )
    pdf.ln(2)
    pdf.kpis(
        [
            ("DriveScore", str(r.drivescore), _color_calificacion(r.calificacion)),
            ("Calificación", NOMBRE_CALIFICACION[r.calificacion], _color_calificacion(r.calificacion)),
            ("Nivel de riesgo", NOMBRE_NIVEL[r.nivel_riesgo], COLOR_NIVEL[r.nivel_riesgo][0]),
            ("Viajes", str(len(r.viajes)), None),
            ("Distancia", _km(r.distancia_m), None),
        ]
    )
    pdf.seccion("Puntaje por categoría")
    pdf.barras(r.categorias)
    _resumen_eventos(pdf, r.eventos_por_tipo, r.eventos, r.eventos_por_10km)

    pdf.seccion("Viajes del periodo")
    columnas = [
        ("Fecha", 50, "L"),
        ("Distancia", 35, "R"),
        ("Duración", 35, "R"),
        ("DriveScore", 25, "C"),
        ("Calificación", 35, "C"),
    ]
    filas, estilos = [], {}
    for i, v in enumerate(r.viajes):
        filas.append(
            [
                _fecha_hora(v.fecha),
                _km(v.distancia_m),
                _duracion(v.duracion_s),
                str(v.drivescore),
                NOMBRE_CALIFICACION[v.calificacion],
            ]
        )
        texto, fondo = COLOR_CALIFICACION[v.calificacion]
        estilos[(i, 3)] = (texto, fondo)
        estilos[(i, 4)] = (texto, fondo)
    pdf.tabla(columnas, filas, estilos)
    return bytes(pdf.output())


def pdf_flota(r: ReporteFlota) -> bytes:
    pdf = _Reporte("Reporte de desempeño de la flota", r.empresa.nombre, r.generado, r.periodo)
    pdf.titulo_principal(r.empresa.nombre)
    pdf.tarjeta_datos(
        [
            ("Empresa", r.empresa.nombre),
            ("Tipo", r.empresa.tipo),
            ("Contacto", f"{r.empresa.email_contacto} - {r.empresa.telefono}"),
            ("Periodo", r.periodo),
            ("Generado", _fecha_hora(r.generado)),
        ]
    )
    pdf.ln(2)
    pdf.kpis(
        [
            ("DriveScore", str(r.drivescore), _color_calificacion(r.calificacion)),
            ("Calificación", NOMBRE_CALIFICACION[r.calificacion], _color_calificacion(r.calificacion)),
            ("Nivel de riesgo", NOMBRE_NIVEL[r.nivel_riesgo], COLOR_NIVEL[r.nivel_riesgo][0]),
            ("Conductores", f"{r.conductores_con_viajes} de {r.conductores}", None),
        ]
    )
    pdf.kpis(
        [
            ("Viajes", str(r.viajes), None),
            ("Distancia", _km(r.distancia_m), None),
            ("Eventos", str(r.eventos), None),
            ("Eventos por 10 km", _num(r.eventos_por_10km) if r.eventos_por_10km is not None else "-", None),
        ]
    )
    _resumen_eventos(pdf, r.eventos_por_tipo, r.eventos, r.eventos_por_10km)

    pdf.seccion("Conductores")
    columnas = [
        ("Conductor", 54, "L"),
        ("DriveScore", 22, "C"),
        ("Calificación", 28, "C"),
        ("Riesgo", 20, "C"),
        ("Viajes", 16, "R"),
        ("Distancia", 24, "R"),
        ("Eventos", 16, "R"),
    ]
    filas, estilos = [], {}
    for i, f in enumerate(r.filas):
        filas.append(
            [
                f.nombre,
                str(f.drivescore) if f.drivescore is not None else "-",
                NOMBRE_CALIFICACION[f.calificacion] if f.calificacion else "-",
                NOMBRE_NIVEL[f.nivel_riesgo] if f.nivel_riesgo else "-",
                str(f.viajes),
                _km(f.distancia_m) if f.viajes else "-",
                str(f.eventos) if f.viajes else "-",
            ]
        )
        if f.calificacion:
            estilos[(i, 1)] = estilos[(i, 2)] = COLOR_CALIFICACION[f.calificacion]
            estilos[(i, 3)] = COLOR_NIVEL[f.nivel_riesgo]
    pdf.tabla(columnas, filas, estilos)
    return bytes(pdf.output())
