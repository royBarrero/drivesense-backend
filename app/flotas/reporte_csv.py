"""Reporte de desempeño en CSV (HU-27): UTF-8 con BOM para que Excel respete las tildes.

Secciones separadas por una línea en blanco, cada una con su encabezado de columnas.
Números con punto decimal y fechas ISO (para que se puedan procesar).
"""

import csv
import io

from app.flotas.reporte import (
    NOMBRE_CALIFICACION,
    NOMBRE_NIVEL,
    NOMBRE_TIPO_EVENTO,
    DatosEmpresa,
    ReporteConductor,
    ReporteFlota,
)

BOM = "﻿"


def _km(distancia_m: float) -> float:
    return round(distancia_m / 1000, 2)


def _minutos(duracion_s: int) -> float:
    return round(duracion_s / 60, 1)


def _vacio(valor):
    return "" if valor is None else valor


def _segura(celda):
    """Evita que Excel interprete como fórmula un texto escrito por un usuario."""
    if isinstance(celda, str) and celda[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + celda
    return celda


def _escribir(secciones: list[list[list]]) -> str:
    salida = io.StringIO()
    escritor = csv.writer(salida, lineterminator="\r\n")
    for indice, filas in enumerate(secciones):
        if indice:
            escritor.writerow([])
        for fila in filas:
            escritor.writerow([_segura(c) for c in fila])
    return BOM + salida.getvalue()


def _datos_empresa(empresa: DatosEmpresa, generado, periodo: str) -> list[list]:
    return [
        ["Empresa", empresa.nombre],
        ["Tipo de empresa", empresa.tipo],
        ["Correo de contacto", empresa.email_contacto],
        ["Teléfono de contacto", empresa.telefono],
        ["Periodo", periodo],
        ["Generado", generado.strftime("%Y-%m-%d %H:%M")],
    ]


def csv_conductor(reporte: ReporteConductor) -> str:
    return _escribir(
        [
            [["Reporte de desempeño del conductor"]]
            + _datos_empresa(reporte.empresa, reporte.generado, reporte.periodo)
            + [
                ["Conductor", reporte.nombre],
                ["Correo del conductor", reporte.email],
                ["Teléfono del conductor", reporte.telefono],
            ],
            [
                ["Resumen"],
                ["DriveScore promedio", "Calificación", "Nivel de riesgo", "Viajes", "Distancia (km)"],
                [
                    reporte.drivescore,
                    NOMBRE_CALIFICACION[reporte.calificacion],
                    NOMBRE_NIVEL[reporte.nivel_riesgo],
                    len(reporte.viajes),
                    _km(reporte.distancia_m),
                ],
            ],
            [["Puntaje por categoría"], ["Categoría", "Puntaje"]]
            + [[nombre, puntaje] for nombre, puntaje in reporte.categorias.items()],
            [["Eventos por tipo"], ["Tipo", "Cantidad"]]
            + [[NOMBRE_TIPO_EVENTO[t], n] for t, n in reporte.eventos_por_tipo.items()]
            + [["Total", reporte.eventos], ["Eventos por cada 10 km", _vacio(reporte.eventos_por_10km)]],
            [["Viajes"], ["Fecha", "Hora", "Distancia (km)", "Duración (min)", "DriveScore", "Calificación"]]
            + [
                [
                    v.fecha.strftime("%Y-%m-%d"),
                    v.fecha.strftime("%H:%M"),
                    _km(v.distancia_m),
                    _minutos(v.duracion_s),
                    v.drivescore,
                    NOMBRE_CALIFICACION[v.calificacion],
                ]
                for v in reporte.viajes
            ],
        ]
    )


def csv_flota(reporte: ReporteFlota) -> str:
    return _escribir(
        [
            [["Reporte de desempeño de la flota"]]
            + _datos_empresa(reporte.empresa, reporte.generado, reporte.periodo),
            [
                ["Resumen de la flota"],
                [
                    "Conductores",
                    "Conductores con viajes",
                    "DriveScore promedio",
                    "Calificación",
                    "Nivel de riesgo",
                    "Viajes",
                    "Distancia (km)",
                    "Eventos",
                    "Eventos por cada 10 km",
                ],
                [
                    reporte.conductores,
                    reporte.conductores_con_viajes,
                    reporte.drivescore,
                    NOMBRE_CALIFICACION[reporte.calificacion],
                    NOMBRE_NIVEL[reporte.nivel_riesgo],
                    reporte.viajes,
                    _km(reporte.distancia_m),
                    reporte.eventos,
                    _vacio(reporte.eventos_por_10km),
                ],
            ],
            [["Eventos de la flota por tipo"], ["Tipo", "Cantidad"]]
            + [[NOMBRE_TIPO_EVENTO[t], n] for t, n in reporte.eventos_por_tipo.items()],
            [
                ["Conductores"],
                [
                    "Conductor",
                    "Correo",
                    "DriveScore",
                    "Calificación",
                    "Nivel de riesgo",
                    "Viajes",
                    "Distancia (km)",
                    "Eventos",
                ],
            ]
            + [
                [
                    f.nombre,
                    f.email,
                    _vacio(f.drivescore),
                    NOMBRE_CALIFICACION[f.calificacion] if f.calificacion else "",
                    NOMBRE_NIVEL[f.nivel_riesgo] if f.nivel_riesgo else "",
                    f.viajes,
                    _km(f.distancia_m),
                    f.eventos,
                ]
                for f in reporte.filas
            ],
        ]
    )
