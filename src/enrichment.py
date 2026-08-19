"""Enriquecimento do relatório com dados cadastrais: categorias, contas correntes,
departamentos e clientes/fornecedores.

Cadastros de categorias/contas correntes/clientes mudam raramente, então este
módulo cacheia os resultados em disco (`.cache/`) com TTL, conforme a Omie
recomenda na documentação de limites de consumo ("cacheie consultas no lado
da aplicação para evitar requisições redundantes").
"""
from __future__ import annotations

import logging
from typing import Any

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


_CAMPOS_CATEGORIA = (
    "descricao", "categoria_superior", "codigo_dre",
    "conta_inativa", "nao_exibir", "transferencia", "totalizadora",
)


def build_categoria_map(
    client: OmieClient, usar_cache: bool = True, ttl_segundos: float = TTL_PADRAO_SEGUNDOS
) -> dict[str, dict[str, str]]:
    """Retorna {codigo_categoria: {descricao, categoria_superior, codigo_dre,
    conta_inativa, nao_exibir, transferencia, totalizadora}}, usando cache em
    disco quando disponível.

    `categoria_superior`/`codigo_dre` resolvem as colunas "Grupo"/"DRE" no layout
    padrão da aba Geral; `conta_inativa` marca o sufixo "(inativa)" na Categoria;
    `nao_exibir`/`transferencia`/`totalizadora` são usados na regra de "DRE"
    (veja `_dre_flag` em report_builder.py) — validados por amostragem contra um
    relatório nativo real (bdContas): 2257/2258 títulos batem exatamente.
    """
    if usar_cache:
        cache = local_cache.carregar_com_ttl("categorias", ttl_segundos)
        if cache is not None:
            if all(isinstance(v, dict) and "conta_inativa" in v for v in cache.values()):
                logger.info("Categorias carregadas do cache local: %d", len(cache))
                return cache
            logger.info(
                "Cache local de categorias está em formato antigo (versão anterior do "
                "projeto) — reconsultando a API para atualizá-lo."
            )

    categorias = _listar_paginado(client, "geral/categorias", "ListarCategorias", "categoria_cadastro")
    mapa = {
        c["codigo"]: {
            "descricao": c.get("descricao", c["codigo"]),
            "categoria_superior": c.get("categoria_superior") or "",
            "codigo_dre": c.get("codigo_dre") or "",
            "conta_inativa": c.get("conta_inativa") or "N",
            "nao_exibir": c.get("nao_exibir") or "N",
            "transferencia": c.get("transferencia") or "N",
            "totalizadora": c.get("totalizadora") or "N",
        }
        for c in categorias
        if c.get("codigo")
    }
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


def build_cliente_map(
    client: OmieClient,
    usar_cache: bool = True,
    ttl_segundos: float = TTL_PADRAO_SEGUNDOS,
    registros_por_pagina: int = 100,
) -> dict[int, dict[str, str]]:
    """Retorna {codigo_cliente_omie: {razao_social, nome_fantasia, cnpj_cpf,
    inativo}}, usando cache em disco quando disponível.

    Usa ListarClientes (listagem paginada de todo o cadastro) em vez de uma
    chamada ConsultarCliente por código de cliente distinto encontrado nos
    títulos. Para contas com muitos clientes/fornecedores diferentes no
    período, isso reduz drasticamente o número de chamadas à API — de uma
    por cliente distinto para uma a cada `registros_por_pagina` clientes
    cadastrados no total.

    `inativo` ("S"/"N") vem do cadastro mas não é usado para marcar a coluna
    "Cliente ou Fornecedor (Nome Fantasia)" (diferente do sufixo "(inativa)"
    de categoria): não há registro nativo (bdContas) de um cliente inativo
    com título no período para validar se a Omie marca isso de alguma forma
    — fica disponível no mapa para quem precisar, sem fabricar um formato
    não confirmado.
    """
    if usar_cache:
        cache = local_cache.carregar_com_ttl("clientes", ttl_segundos)
        if cache is not None:
            if all(isinstance(v, dict) and "inativo" in v for v in cache.values()):
                # Chaves JSON são sempre string; nCodCliente é usado como int no restante do código.
                mapa_convertido = {int(k): v for k, v in cache.items()}
                logger.info("Clientes/fornecedores carregados do cache local: %d", len(mapa_convertido))
                return mapa_convertido
            logger.info(
                "Cache local de clientes está em formato antigo (sem 'inativo') — "
                "reconsultando a API para atualizá-lo."
            )

    clientes = _listar_paginado(
        client, "geral/clientes", "ListarClientes", "clientes_cadastro", registros_por_pagina
    )
    mapa = {
        c["codigo_cliente_omie"]: {
            "razao_social": c.get("razao_social", ""),
            "nome_fantasia": c.get("nome_fantasia", ""),
            "cnpj_cpf": c.get("cnpj_cpf", ""),
            "inativo": c.get("inativo") or "N",
        }
        for c in clientes
        if c.get("codigo_cliente_omie") is not None
    }
    logger.info("Clientes/fornecedores carregados da API: %d", len(mapa))

    if usar_cache:
        local_cache.salvar_com_ttl("clientes", {str(k): v for k, v in mapa.items()})

    return mapa
