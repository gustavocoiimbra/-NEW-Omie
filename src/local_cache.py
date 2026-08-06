"""Cache local em disco (JSON), usado para evitar refazer chamadas à API da
Omie para dados que raramente mudam (clientes, categorias, contas correntes),
conforme recomendado na documentação de limites de consumo da API.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache")


def _caminho(nome: str) -> str:
    return os.path.join(CACHE_DIR, f"{nome}.json")


def carregar(nome: str) -> dict[str, Any]:
    """Lê o arquivo de cache bruto (sem checar TTL). Retorna {} se ausente/corrompido."""
    caminho = _caminho(nome)
    if not os.path.exists(caminho):
        return {}
    try:
        with open(caminho, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def salvar(nome: str, dados: dict[str, Any]) -> None:
    """Grava o arquivo de cache de forma atômica (escreve em .tmp e renomeia)."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    caminho = _caminho(nome)
    tmp = caminho + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False)
    os.replace(tmp, caminho)


def carregar_com_ttl(nome: str, ttl_segundos: float) -> Any | None:
    """Para caches de "blob único" (ex.: mapa de categorias). Retorna None se
    ausente ou expirado."""
    bruto = carregar(nome)
    if not bruto or "_ts" not in bruto or "dados" not in bruto:
        return None
    if time.time() - bruto["_ts"] > ttl_segundos:
        return None
    return bruto["dados"]


def salvar_com_ttl(nome: str, dados: Any) -> None:
    salvar(nome, {"_ts": time.time(), "dados": dados})
