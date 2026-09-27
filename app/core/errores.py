from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

# Mensajes en español por tipo de error de Pydantic; {..} se rellena con ctx
_MENSAJES = {
    "missing": "Este campo es obligatorio",
    "string_too_short": "Debe tener al menos {min_length} caracteres",
    "string_too_long": "Debe tener como máximo {max_length} caracteres",
    "string_type": "Debe ser un texto",
    "json_invalid": "El cuerpo de la petición no es un JSON válido",
}


def _traducir(error: dict) -> str:
    tipo = error["type"]
    ctx = error.get("ctx") or {}
    if tipo == "value_error":
        # ValueError de nuestros validadores: su mensaje ya está en español.
        # Sin "error" en ctx es el de EmailStr.
        if "error" in ctx:
            return str(ctx["error"])
        return "El correo no tiene un formato válido"
    if tipo == "string_too_short" and ctx.get("min_length") == 1:
        return "Este campo no puede estar vacío"
    if tipo == "enum":
        # Pydantic da "'flota' or 'aseguradora'"
        return f"Debe ser uno de: {ctx.get('expected', '').replace(' or ', ' o ')}"
    plantilla = _MENSAJES.get(tipo)
    if plantilla is None:
        return "Valor inválido"
    return plantilla.format(**ctx)


def _campo(error: dict) -> str | None:
    # En json_invalid, loc trae la posición del error en el texto, no un campo
    if error["type"] == "json_invalid":
        return None
    # loc = ("body", "campo", ...): se omite la ubicación ("body", "query", ...)
    return ".".join(str(parte) for parte in error["loc"][1:]) or None


async def manejar_error_validacion(request: Request, exc: RequestValidationError) -> JSONResponse:
    """422 en español y sin devolver los valores enviados (p. ej. la contraseña)."""
    errores = [
        {"campo": _campo(error), "mensaje": _traducir(error)} for error in exc.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": "Datos inválidos", "errores": errores},
    )
