from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Banco de dados
    DATABASE_URL: str
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str

    # ETL
    WORLD: str = "Behemoth" #Servidor que sera puxado os dados
    ETL_INTERVAL_HOURS: int = 1 #Intervalo de tempo em horas que o ETL ira rodar
    RETENTION_DAYS: int = 60 #Dias em que os dados irao permanecer no BD

    # Controla quantas requisições async rodam em paralelo.
    # 20 é seguro para a Universalis sem risco de rate-limit.
    API_CONCURRENCY: int = 20

    # URL base da API — centralizado aqui para facilitar troca futura
    UNIVERSALIS_BASE_URL: str = "https://universalis.app/api/v2"


# Instância global importada pelos outros módulos:
#   from src.config import settings
settings = Settings()