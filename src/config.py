"""Carregamento e validação das configurações da aplicação a partir do .env."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    """Erro de configuração (ex.: credenciais ausentes)."""


@dataclass(frozen=True)
class Config:
    app_key: str
    app_secret: str
    max_req_por_segundo: float


def carregar_config(dotenv_path: str | None = None) -> Config:
    load_dotenv(dotenv_path=dotenv_path)

    app_key = os.getenv("OMIE_APP_KEY", "").strip()
    app_secret = os.getenv("OMIE_APP_SECRET", "").strip()

    if not app_key or not app_secret:
        raise ConfigError(
            "OMIE_APP_KEY e/ou OMIE_APP_SECRET não configurados. "
            "Copie .env.example para .env e preencha as credenciais da API Omie."
        )

    try:
        max_req = float(os.getenv("OMIE_MAX_REQ_POR_SEGUNDO", "3"))
    except ValueError:
        max_req = 3.0

    return Config(app_key=app_key, app_secret=app_secret, max_req_por_segundo=max_req)
