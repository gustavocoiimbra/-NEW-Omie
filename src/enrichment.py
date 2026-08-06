"""Enriquecimento do relatório com dados cadastrais: categorias, contas correntes,
departamentos e clientes/fornecedores.

Cadastros de categorias/contas correntes/clientes mudam raramente, então este
módulo cacheia os resultados em disco (`.cache/`) com TTL, conforme a Omie
recomenda na documentação de limites de consumo ("cacheie consultas no lado
da aplicação para evitar requisições redundantes").
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Iterable

from . import local_cache
from .omie_client import OmieClient

logger = logging.getLogger("enrichment")

TTL_PADRAO_SEGUNDOS = 24 * 3600


def _listar_paginado(
    client: OmieClient,
    modulo: str,
    chamada: str,
    campo_lista: str,
    registros_por_pagina: int = 100,
    param_extra: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Percorre um endpoint de listagem paginado padrão da Omie (geral/*)."""
    itens: list[dict[str, Any]] = []
    pagina = 1
    while True:
        param = {"pagina": pagina, "registros_por_pagina": registros_por_pagina}
        if param_extra:
            param.update(param_extra)
        resp = client.call(modulo, chamada, param)

        lote = resp.get(campo_lista) or []
        itens.extend(lote)

        total_paginas = resp.get("total_de_paginas", 1) or 1
        if pagina >= total_paginas or not lote:
            break
        pagina += 1
    return itens


def build_categoria_map(
    client: OmieClient, usar_cache: bool = True, ttl_segundos: float = TTL_PADRAO_SEGUNDOS
) -> dict[str, str]:
    """Retorna {codigo_categoria: descricao}, usando cache em disco quando disponível."""
    if usar_cache:
        cache = local_cache.carregar_com_ttl("categorias", ttl_segundos)
        if cache is not None:
            logger.info("Categorias carregadas do cache local: %d", len(cache))
            return cache

    categorias = _listar_paginado(client, "geral/categorias", "ListarCategorias", "categoria_cadastro")
    mapa = {c["codigo"]: c.get("descricao", c["codigo"]) for c in categorias if c.get("codigo")}
    logger.info("Categorias carregadas da API: %d", len(mapa))

    if usar_cache:
        local_cache.salvar_com_ttl("categorias", mapa)

    return mapa


def build_conta_corrente_map(
    client: OmieClient, usar_cache: bool = True, ttl_segundos: float = TTL_PADRAO_SEGUNDOS
) -> dict[int, str]:
    """Retorna {nCodCC: descricao}, usando cache em disco quando disponível."""
    if usar_cache:
        cache = local_cache.carregar_com_ttl("contas_correntes", ttl_segundos)
        if cache is not None:
            # Chaves JSON são sempre string; nCodCC é usado como int no restante do código.
            mapa_convertido = {int(k): v for k, v in cache.items()}
            logger.info("Contas correntes carregadas do cache local: %d", len(mapa_convertido))
            return mapa_convertido

    contas = _listar_paginado(client, "geral/contacorrente", "ListarContasCorrentes", "ListarContasCorrentes")
    mapa = {c["nCodCC"]: c.get("descricao", str(c["nCodCC"])) for c in contas if c.get("nCodCC") is not None}
    logger.info("Contas correntes carregadas da API: %d", len(mapa))

    if usar_cache:
        local_cache.salvar_com_ttl("contas_correntes", {str(k): v for k, v in mapa.items()})

    return mapa


class ClienteFornecedorCache:
    """Busca (com cache em memória + disco) os dados cadastrais de clientes/fornecedores.

    Usa ConsultarCliente por código, em vez de baixar a base inteira de clientes,
    para escalar bem mesmo em contas com cadastros muito grandes — apenas os
    códigos que efetivamente aparecem nos títulos do período são consultados.

    Suporta pré-carregamento em paralelo (`prefetch`) — a Omie permite até 4
    requisições simultâneas por IP + App Key + Método, e a latência de rede
    (não o rate limit) costuma ser o gargalo real dessa etapa.
    """

    MODULO = "geral/clientes"
    CHAMADA = "ConsultarCliente"
    NOME_CACHE = "clientes"

    def __init__(
        self,
        client: OmieClient,
        max_workers: int = 4,
        usar_cache_disco: bool = True,
        ttl_segundos: float = TTL_PADRAO_SEGUNDOS,
    ):
        self._client = client
        self._max_workers = max(1, max_workers)
        self._usar_cache_disco = usar_cache_disco
        self._ttl_segundos = ttl_segundos
        self._lock = threading.Lock()
        self._cache: dict[int, dict[str, Any]] = {}
        self._sujo = False
        self._consultas_rede = 0

        if usar_cache_disco:
            self._carregar_disco()

    def _carregar_disco(self) -> None:
        bruto = local_cache.carregar(self.NOME_CACHE)
        agora = time.time()
        validos = 0
        for cod_str, entrada in bruto.items():
            if agora - entrada.get("_ts", 0) <= self._ttl_segundos:
                self._cache[int(cod_str)] = entrada
                validos += 1
        if validos:
            logger.info(
                "Cache local de clientes/fornecedores: %d registros válidos (TTL %.0fh)",
                validos, self._ttl_segundos / 3600,
            )

    def _consultar(self, codigo_cliente: int) -> dict[str, Any]:
        try:
            resp = self._client.call(self.MODULO, self.CHAMADA, {"codigo_cliente_omie": codigo_cliente})
            dados = {
                "razao_social": resp.get("razao_social", ""),
                "nome_fantasia": resp.get("nome_fantasia", ""),
                "cnpj_cpf": resp.get("cnpj_cpf", ""),
            }
        except Exception as exc:  # noqa: BLE001 - não deve interromper o relatório
            logger.warning("Falha ao consultar cliente/fornecedor %s: %s", codigo_cliente, exc)
            dados = {"razao_social": f"[não encontrado: {codigo_cliente}]", "nome_fantasia": "", "cnpj_cpf": ""}

        dados["_ts"] = time.time()
        with self._lock:
            self._consultas_rede += 1
        return dados

    def get(self, codigo_cliente: int | None) -> dict[str, Any]:
        if not codigo_cliente:
            return {"razao_social": "", "nome_fantasia": "", "cnpj_cpf": ""}

        with self._lock:
            cached = self._cache.get(codigo_cliente)
        if cached is not None:
            return cached

        dados = self._consultar(codigo_cliente)
        with self._lock:
            self._cache[codigo_cliente] = dados
            self._sujo = True
        return dados

    def prefetch(self, codigos: Iterable[int | None]) -> None:
        """Consulta em paralelo (até `max_workers` simultâneas) todos os códigos
        ainda não presentes no cache. Chame antes de montar as linhas do relatório
        para evitar consultas sequenciais bloqueantes uma a uma."""
        with self._lock:
            pendentes = sorted({c for c in codigos if c and c not in self._cache})
        if not pendentes:
            return

        logger.info(
            "Consultando %d clientes/fornecedores novos (até %d em paralelo)...",
            len(pendentes), self._max_workers,
        )
        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futuros = {executor.submit(self._consultar, cod): cod for cod in pendentes}
            for futuro in as_completed(futuros):
                cod = futuros[futuro]
                dados = futuro.result()
                with self._lock:
                    self._cache[cod] = dados
                    self._sujo = True

    def persistir(self) -> None:
        """Grava o cache em disco (só reescreve se houve consultas novas nesta execução)."""
        if not self._usar_cache_disco or not self._sujo:
            return
        with self._lock:
            bruto = {str(cod): dados for cod, dados in self._cache.items()}
        local_cache.salvar(self.NOME_CACHE, bruto)
        self._sujo = False

    @property
    def total_em_cache(self) -> int:
        return len(self._cache)

    @property
    def total_consultas_rede(self) -> int:
        return self._consultas_rede
