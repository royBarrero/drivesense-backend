import re
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    field_validator,
)

from app.auth.models import RolUsuario
from app.flotas.models import TipoEmpresa


def _normalizar_email(valor: object) -> object:
    if isinstance(valor, str):
        return valor.strip().lower()
    return valor


# Email en minúsculas y sin espacios, igual en registro y login
EmailNormalizado = Annotated[EmailStr, BeforeValidator(_normalizar_email)]


def _validar_telefono(valor: str) -> str:
    # Celular boliviano: 8 dígitos
    if not re.fullmatch(r"\d{8}", valor):
        raise ValueError("El teléfono debe tener 8 dígitos")
    return valor


Telefono = Annotated[str, AfterValidator(_validar_telefono)]


class DatosUsuarioEntrada(BaseModel):
    """Datos y validaciones comunes al registro de conductor y de administrador."""

    nombre: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    email: EmailNormalizado
    telefono: Telefono
    contrasenia: Annotated[str, Field(min_length=8, max_length=128)]

    @field_validator("contrasenia")
    @classmethod
    def validar_contrasenia(cls, valor: str) -> str:
        if not re.search(r"[A-Za-z]", valor):
            raise ValueError("La contraseña debe contener al menos una letra")
        if not re.search(r"\d", valor):
            raise ValueError("La contraseña debe contener al menos un número")
        return valor


class RegistroConductorEntrada(DatosUsuarioEntrada):
    pass


class EmpresaEntrada(BaseModel):
    nombre: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]
    tipo: TipoEmpresa
    # No es único y puede coincidir con el email del administrador
    email_contacto: EmailNormalizado
    telefono: Telefono


class RegistroEmpresaEntrada(BaseModel):
    empresa: EmpresaEntrada
    administrador: DatosUsuarioEntrada


class EmpresaResumen(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    tipo: TipoEmpresa


class UsuarioSalida(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    email: str
    telefono: str
    rol: RolUsuario
    debe_cambiar_contrasenia: bool
    # Nulo para el conductor individual
    empresa: EmpresaResumen | None


class LoginEntrada(BaseModel):
    email: EmailNormalizado
    # Sin reglas de complejidad: en el login solo se compara con el hash
    contrasenia: Annotated[str, Field(min_length=1, max_length=128)]


class SesionSalida(BaseModel):
    access_token: str
    token_type: str = "bearer"
    usuario: UsuarioSalida
