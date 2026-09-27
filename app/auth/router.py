from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import service
from app.auth.dependencias import UsuarioActual
from app.auth.schemas import (
    LoginEntrada,
    RegistroConductorEntrada,
    RegistroEmpresaEntrada,
    SesionSalida,
    UsuarioSalida,
)
from app.core.database import get_db

router = APIRouter(prefix="/auth", tags=["Autenticación"])


@router.post("/registro", response_model=SesionSalida, status_code=status.HTTP_201_CREATED)
async def registro_conductor(
    datos: RegistroConductorEntrada, db: AsyncSession = Depends(get_db)
):
    """Registra un conductor individual con correo y contraseña (HU-01)."""
    return await service.registrar_conductor(db, datos)


@router.post("/login", response_model=SesionSalida)
async def login_conductor(datos: LoginEntrada, db: AsyncSession = Depends(get_db)):
    """Inicio de sesión de conductor desde la app móvil (HU-02)."""
    return await service.iniciar_sesion_conductor(db, datos)


@router.post(
    "/empresa/registro", response_model=SesionSalida, status_code=status.HTTP_201_CREATED
)
async def registro_empresa(datos: RegistroEmpresaEntrada, db: AsyncSession = Depends(get_db)):
    """Registra una empresa junto con su administrador (HU-03)."""
    return await service.registrar_empresa(db, datos)


@router.post("/empresa/login", response_model=SesionSalida)
async def login_admin(datos: LoginEntrada, db: AsyncSession = Depends(get_db)):
    """Inicio de sesión del administrador de empresa en el panel web (HU-03)."""
    return await service.iniciar_sesion_admin(db, datos)


@router.get("/yo", response_model=UsuarioSalida)
async def usuario_actual(usuario: UsuarioActual):
    """Datos del usuario autenticado; la app lo usa para comprobar que la sesión sigue válida."""
    return usuario
