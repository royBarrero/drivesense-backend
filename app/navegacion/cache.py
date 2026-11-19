"""Caché en memoria de las consultas a OpenRouteService (HU-30).

Cada proceso de uvicorn tiene la suya (con 2 workers, dos cachés independientes): basta para
no repetir la misma consulta en poco tiempo y cuidar la cuota gratuita. Solo se guardan las
respuestas correctas; los errores no.
"""

import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from typing import Generic, TypeVar

V = TypeVar("V")

MAXIMO_ENTRADAS = 512


class CacheTTL(Generic[V]):
    """Diccionario LRU con vencimiento por entrada. El reloj se inyecta en las pruebas."""

    def __init__(
        self,
        ttl_s: float,
        maximo: int = MAXIMO_ENTRADAS,
        reloj: Callable[[], float] = time.monotonic,
    ):
        self._ttl_s = ttl_s
        self._maximo = maximo
        self._reloj = reloj
        self._datos: OrderedDict[Hashable, tuple[float, V]] = OrderedDict()

    def obtener(self, clave: Hashable) -> V | None:
        entrada = self._datos.get(clave)
        if entrada is None:
            return None
        vence, valor = entrada
        if self._reloj() >= vence:
            del self._datos[clave]
            return None
        self._datos.move_to_end(clave)
        return valor

    def guardar(self, clave: Hashable, valor: V) -> None:
        self._datos[clave] = (self._reloj() + self._ttl_s, valor)
        self._datos.move_to_end(clave)
        while len(self._datos) > self._maximo:
            self._datos.popitem(last=False)

    def limpiar(self) -> None:
        self._datos.clear()

    def __len__(self) -> int:
        return len(self._datos)
