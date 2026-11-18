from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración de la aplicación, leída desde el archivo .env"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str

    jwt_secret: str
    jwt_algoritmo: str = "HS256"
    jwt_expiracion_minutos: int = 10080  # 7 días

    # Orígenes separados por comas, p. ej. "http://localhost:5173,https://panel.drivesense.com"
    cors_origenes: str = ""

    # Temporal: correo de la cuenta admin compartida del modo pruebas; vacío = desactivado
    pruebas_correo: str = ""

    # IA de las recomendaciones (HU-18, HU-19). Clave vacía = solo plantillas, sin llamar a la API
    anthropic_api_key: str = ""
    ia_modelo: str = "claude-haiku-5-5"
    # Tope de espera de la IA al finalizar un viaje; la app espera 10 s la respuesta completa
    ia_timeout_s: float = 7.0

    @property
    def lista_cors_origenes(self) -> list[str]:
        return [origen.strip() for origen in self.cors_origenes.split(",") if origen.strip()]


settings = Settings()