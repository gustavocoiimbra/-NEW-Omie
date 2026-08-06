"""Busca de contas a pagar/receber via financas/pesquisartitulos (PesquisarLancamentos)."""
from __future__ import annotations

import logging
from typing import Any, Iterable, Literal

from .omie_client import OmieClient

logger = logging.getLogger("titulos")

MODULO = "financas/pesquisartitulos"
CHAMADA = "PesquisarLancamentos"

FiltroData = Literal["vencimento", "emissao", "pagamento"]

_PREFIXO_FILTRO_DATA = {
    "vencimento": ("dDtVencDe", "dDtVencAte"),
    "emissao": ("dDtEmisDe", "dDtEmisAte"),
    "pagamento": ("dDtPagtoDe", "dDtPagtoAte"),
}

STATUS_VALIDOS = (
    "CANCELADO",
    "RECEBIDO",
    "LIQUIDADO",
    "EMABERTO",
    "PAGTO_PARCIAL",
    "VENCEHOJE",
    "AVENCER",
    "ATRASADO",
)

# Documentado em https://ajuda.omie.com.br/pt-BR/articles/8112984: a API limita
# silenciosamente a 100 registros por página, mesmo se um valor maior for pedido.
LIMITE_REGISTROS_POR_PAGINA = 100


def _validar_status(status: str | None) -> None:
    if status is not None and status not in STATUS_VALIDOS:
        raise ValueError(f"status inválido: {status!r}. Valores aceitos: {STATUS_VALIDOS}")


def _montar_param_base(
    natureza: str,
    data_inicio: str | None,
    data_fim: str | None,
    filtro_data: FiltroData,
    status: str | None,
    registros_por_pagina: int,
) -> dict[str, Any]:
    if registros_por_pagina > LIMITE_REGISTROS_POR_PAGINA:
        logger.warning(
            "registros_por_pagina=%d excede o limite documentado da Omie (%d); "
            "a API deve ignorar o excesso e paginar em blocos de %d mesmo assim.",
            registros_por_pagina, LIMITE_REGISTROS_POR_PAGINA, LIMITE_REGISTROS_POR_PAGINA,
        )

    campo_de, campo_ate = _PREFIXO_FILTRO_DATA[filtro_data]
    param_base: dict[str, Any] = {
        "nRegPorPagina": registros_por_pagina,
        "cOrdenarPor": "CODIGO",
        "cNatureza": natureza,
    }
    if data_inicio:
        param_base[campo_de] = data_inicio
    if data_fim:
        param_base[campo_ate] = data_fim
    if status:
        param_base["cStatus"] = status
    return param_base


def contar_titulos(
    client: OmieClient,
    data_inicio: str | None = None,
    data_fim: str | None = None,
    naturezas: Iterable[Literal["P", "R"]] = ("P", "R"),
    filtro_data: FiltroData = "vencimento",
    status: str | None = None,
) -> dict[str, int]:
    """Sonda barata (1 registro por natureza) para descobrir quantos títulos os
    filtros atuais vão retornar, sem paginar tudo. Útil para avisar o usuário do
    volume antes de disparar uma busca potencialmente grande (ex.: sem filtro de
    data)."""
    _validar_status(status)
    contagem: dict[str, int] = {}
    for natureza in naturezas:
        param = _montar_param_base(natureza, data_inicio, data_fim, filtro_data, status, registros_por_pagina=1)
        param["nPagina"] = 1
        resp = client.call(MODULO, CHAMADA, param)
        contagem[natureza] = resp.get("nTotRegistros", 0) or 0
    return contagem


def buscar_titulos(
    client: OmieClient,
    data_inicio: str | None = None,
    data_fim: str | None = None,
    naturezas: Iterable[Literal["P", "R"]] = ("P", "R"),
    filtro_data: FiltroData = "vencimento",
    status: str | None = None,
    registros_por_pagina: int = LIMITE_REGISTROS_POR_PAGINA,
) -> list[dict[str, Any]]:
    """Retorna a lista de `titulosEncontrados` (contas a pagar e/ou a receber).

    Faz uma busca paginada por natureza (P=pagar, R=receber), pois a Omie exige
    uma natureza por chamada. Cada item retornado é o objeto bruto
    `titulosEncontrados` (contém cabecTitulo, lancamentos, resumo).

    `data_inicio`/`data_fim` são opcionais — nenhum campo do `PesquisarLancamentos`
    é documentado como obrigatório pela Omie. Se ambos forem omitidos, a busca
    traz TODOS os títulos já lançados na conta (sem filtro de período). É possível
    também informar só um dos dois, para um intervalo aberto de um dos lados.
    """
    _validar_status(status)

    todos: list[dict[str, Any]] = []
    for natureza in naturezas:
        param_base = _montar_param_base(natureza, data_inicio, data_fim, filtro_data, status, registros_por_pagina)

        pagina = 1
        while True:
            param = dict(param_base, nPagina=pagina)
            resp = client.call(MODULO, CHAMADA, param)

            encontrados = resp.get("titulosEncontrados") or []
            todos.extend(encontrados)

            total_paginas = resp.get("nTotPaginas", 1) or 1
            total_registros = resp.get("nTotRegistros", len(encontrados))
            logger.info(
                "Natureza=%s página %d/%d — %d títulos nesta página (total no período: %d)",
                natureza, pagina, total_paginas, len(encontrados), total_registros,
            )

            if pagina >= total_paginas or not encontrados:
                break
            pagina += 1

    return todos
