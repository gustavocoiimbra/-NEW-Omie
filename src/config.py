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
    # Contas correntes consideradas no saldo de caixa (main_caixa.py), como
    # pares (nome exato no cadastro Omie, rótulo de exibição). Vazio se
    # OMIE_CONTAS_CAIXA não estiver configurado — só main_caixa.py exige isso.
    contas_caixa: tuple[tuple[str, str], ...] = ()


def _parse_contas_caixa(valor: str) -> tuple[tuple[str, str], ...]:
    """Formato de `OMIE_CONTAS_CAIXA`: `"nome no cadastro:rótulo,nome no
    cadastro:rótulo,..."`. O rótulo é opcional — se omitido (sem `:`), usa o
    próprio nome do cadastro como rótulo."""
    pares: list[tuple[str, str]] = []
    for item in valor.split(","):
        item = item.strip()
        if not item:
            continue
        nome, _, rotulo = item.partition(":")
        nome = nome.strip()
        rotulo = rotulo.strip() or nome
        pares.append((nome, rotulo))
    return tuple(pares)


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

    contas_caixa = _parse_contas_caixa(os.getenv("OMIE_CONTAS_CAIXA", ""))

    return Config(app_key=app_key, app_secret=app_secret, max_req_por_segundo=max_req, contas_caixa=contas_caixa)
