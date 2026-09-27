from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash

from app.core.config import settings

# Argon2 con los parámetros recomendados por pwdlib
_hasher = PasswordHash.recommended()


def hashear_contrasenia(contrasenia: str) -> str:
    """Devuelve el hash de la contraseña (nunca se guarda en texto plano)."""
    return _hasher.hash(contrasenia)


def verificar_contrasenia(contrasenia: str, contrasenia_hash: str) -> bool:
    """Comprueba si la contraseña coincide con el hash guardado."""
    return _hasher.verify(contrasenia, contrasenia_hash)


# Se verifica contra este hash cuando el email no existe, para que el tiempo
# de respuesta del login no revele qué correos están registrados
HASH_FICTICIO = hashear_contrasenia("contrasenia-ficticia")


def crear_token_acceso(usuario_id: int, rol: str) -> str:
    """Genera un JWT firmado con el id del usuario (sub) y su rol."""
    ahora = datetime.now(timezone.utc)
    payload = {
        "sub": str(usuario_id),
        "rol": rol,
        "iat": ahora,
        "exp": ahora + timedelta(minutes=settings.jwt_expiracion_minutos),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algoritmo)


def decodificar_token(token: str) -> dict:
    """Valida firma y expiración del JWT y devuelve su payload.

    Lanza jwt.InvalidTokenError (incluye token expirado) si no es válido.
    """
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algoritmo],
        options={"require": ["exp", "sub"]},
    )
