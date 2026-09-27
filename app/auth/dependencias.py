from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Usuario
from app.core.database import get_db
from app.core.security import decodificar_token

# auto_error=False para responder nosotros el 401 (en español)
_esquema_bearer = HTTPBearer(auto_error=False)


def _no_autorizado(mensaje: str) -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        detail=mensaje,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def obtener_usuario_actual(
    credenciales: Annotated[HTTPAuthorizationCredentials | None, Depends(_esquema_bearer)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Usuario:
    """Devuelve el usuario del token `Authorization: Bearer`; 401 si no es válido.

    Sirve para cualquier rol: las restricciones por rol van en cada endpoint.
    """
    if credenciales is None:
        raise _no_autorizado("No autenticado")

    try:
        payload = decodificar_token(credenciales.credentials)
        usuario_id = int(payload["sub"])
    except (jwt.InvalidTokenError, ValueError):
        raise _no_autorizado("Sesión inválida o expirada")

    usuario = await db.get(Usuario, usuario_id)
    if usuario is None or not usuario.activo:
        raise _no_autorizado("Sesión inválida o expirada")
    return usuario


UsuarioActual = Annotated[Usuario, Depends(obtener_usuario_actual)]
