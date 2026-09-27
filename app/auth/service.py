from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import RolUsuario, Usuario
from app.auth.schemas import (
    DatosUsuarioEntrada,
    LoginEntrada,
    RegistroConductorEntrada,
    RegistroEmpresaEntrada,
    SesionSalida,
    UsuarioSalida,
)
from app.core.security import (
    HASH_FICTICIO,
    crear_token_acceso,
    hashear_contrasenia,
    verificar_contrasenia,
)
from app.flotas.models import Empresa

EMAIL_YA_REGISTRADO = "Este correo ya está registrado"

# Mensaje cuando la cuenta intenta entrar por el canal de otro rol
_ROL_INCORRECTO = {
    RolUsuario.conductor: "Esta cuenta es de administrador. Ingresa desde el panel web",
    RolUsuario.admin_empresa: "Esta cuenta es de conductor. Ingresa desde la app móvil",
}


def _crear_sesion(usuario: Usuario) -> SesionSalida:
    return SesionSalida(
        access_token=crear_token_acceso(usuario.id, usuario.rol.value),
        usuario=UsuarioSalida.model_validate(usuario),
    )


async def _crear_usuario(
    db: AsyncSession, datos: DatosUsuarioEntrada, rol: RolUsuario, empresa: Empresa | None
) -> SesionSalida:
    """Crea el usuario (y su empresa, si es nueva) en un único commit y le inicia sesión."""
    existente = await db.scalar(select(Usuario.id).where(Usuario.email == datos.email))
    if existente is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=EMAIL_YA_REGISTRADO)

    usuario = Usuario(
        nombre=datos.nombre,
        email=datos.email,
        telefono=datos.telefono,
        contrasenia_hash=hashear_contrasenia(datos.contrasenia),
        rol=rol,
        empresa=empresa,
        activo=True,
        debe_cambiar_contrasenia=False,
    )
    db.add(usuario)
    try:
        await db.commit()
    except IntegrityError:
        # Otra petición registró el mismo email entre la consulta y el commit;
        # el rollback también descarta la empresa nueva
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail=EMAIL_YA_REGISTRADO)

    return _crear_sesion(usuario)


async def registrar_conductor(db: AsyncSession, datos: RegistroConductorEntrada) -> SesionSalida:
    """Crea un conductor individual (sin empresa) y le inicia sesión."""
    return await _crear_usuario(db, datos, RolUsuario.conductor, empresa=None)


async def registrar_empresa(db: AsyncSession, datos: RegistroEmpresaEntrada) -> SesionSalida:
    """Crea la empresa y su administrador en la misma transacción y le inicia sesión."""
    empresa = Empresa(
        nombre=datos.empresa.nombre,
        tipo=datos.empresa.tipo,
        email_contacto=datos.empresa.email_contacto,
        telefono=datos.empresa.telefono,
        activo=True,
    )
    return await _crear_usuario(db, datos.administrador, RolUsuario.admin_empresa, empresa)


async def _iniciar_sesion(
    db: AsyncSession, datos: LoginEntrada, rol_permitido: RolUsuario
) -> SesionSalida:
    """El estado de la cuenta (inactiva, otro rol) solo se revela si la contraseña es correcta."""
    usuario = await db.scalar(select(Usuario).where(Usuario.email == datos.email))
    # Si el email no existe se verifica igual contra un hash ficticio (mismo tiempo de respuesta)
    hash_guardado = usuario.contrasenia_hash if usuario else HASH_FICTICIO
    contrasenia_valida = verificar_contrasenia(datos.contrasenia, hash_guardado)

    if usuario is None or not contrasenia_valida:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Correo o contraseña incorrectos")
    if not usuario.activo:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Esta cuenta está desactivada")
    if usuario.rol != rol_permitido:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=_ROL_INCORRECTO[rol_permitido])

    return _crear_sesion(usuario)


async def iniciar_sesion_conductor(db: AsyncSession, datos: LoginEntrada) -> SesionSalida:
    """Login de la app móvil (solo conductores)."""
    return await _iniciar_sesion(db, datos, RolUsuario.conductor)


async def iniciar_sesion_admin(db: AsyncSession, datos: LoginEntrada) -> SesionSalida:
    """Login del panel web (solo administradores de empresa)."""
    return await _iniciar_sesion(db, datos, RolUsuario.admin_empresa)
