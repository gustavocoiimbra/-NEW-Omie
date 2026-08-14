"""Busca de contas a pagar/receber via financas/mf (ListarMovimentos) — fonte
alternativa a `titulos.py` (PesquisarLancamentos), baseada na consulta de
pagamentos/baixas/lançamentos no Conta Corrente em vez da pesquisa de títulos.

Diferenças confirmadas por sonda contra uma conta real (não documentadas de
forma explícita, então vale registrar aqui):

- `cNatureza` é **opcional**: ao contrário de PesquisarLancamentos (que exige
  uma natureza por chamada), ListarMovimentos retorna Pagar e Receber juntos
  numa única busca paginada quando `cNatureza` é omitido.
- Inclui tanto títulos ainda em aberto (`cLiquidado="N"`) quanto já liquidados
  — não é uma consulta restrita a movimentos já realizados.
- `observacao` não vem preenchida por padrão — corrigido enviando
  `lDadosCad=True` em toda chamada (recupera a observação para a maioria dos
  títulos).
- O vocabulário de `cStatus` também difere (`"A VENCER"`, `"PAGO"` em vez de
  `"AVENCER"`, `"LIQUIDADO"`) — só `"CANCELADO"` é literalmente igual entre os
  dois endpoints.
- Além de títulos, a consulta também traz `PREVISAO_CONTRATO` (previsão de
  faturamento de um contrato — ainda não é um título lançado, `cStatus=
  "PREVISAO"`), incluída no relatório com um tratamento especial — veja
  `_adaptar_movimento`.

O rateio de categorias (`categorias`/`nDistrPercentual`/`nDistrValor`, irmão
de `detalhes`) **não é usado** por este módulo: numa comparação título a
título contra PesquisarLancamentos (mesmo período, 162 títulos, 5 deles
rateados de verdade), `categorias` veio sempre vazio aqui — inclusive com
`lDadosCad=True` e para títulos que PesquisarLancamentos confirma estarem
rateados entre 2-3 categorias. Como esse endpoint não expõe rateio de forma
confiável, o rateio de categorias foi removido também de `report_builder.py`
(usado por `titulos.py`), para as duas fontes produzirem o mesmo resultado
para o mesmo título — sempre uma linha por título, com `cCodCateg` como
categoria única.

O rateio *é* recuperável, só não por aqui: `financas/contapagar` ·
`ConsultarContaPagar`, chamado título a título (`nCodTitulo`), devolve o
array `categorias[]` corretamente preenchido — confirmado contra títulos
reais com rateio (ex.: RECEITA FAZENDA). Não vale o custo de uma chamada
extra por título no pipeline em lote, mas é o caminho a usar se algum dia o
rateio for necessário (ver `sondas/sonda_reconciliacao_bdcontas.py`).
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from .omie_client import OmieClient
from .titulos import LIMITE_REGISTROS_POR_PAGINA

logger = logging.getLogger("movimentos")

MODULO = "financas/mf"
CHAMADA = "ListarMovimentos"

FiltroData = Literal["vencimento", "emissao", "pagamento"]

_PREFIXO_FILTRO_DATA = {
    "vencimento": ("dDtVencDe", "dDtVencAte"),
    "emissao": ("dDtEmisDe", "dDtEmisAte"),
    "pagamento": ("dDtPagtoDe", "dDtPagtoAte"),
}


def _montar_param(
    data_inicio: str | None,
    data_fim: str | None,
    filtro_data: FiltroData,
    natureza: str | None,
    status: str | None,
    registros_por_pagina: int,
    pagina: int,
) -> dict[str, Any]:
    if registros_por_pagina > LIMITE_REGISTROS_POR_PAGINA:
        logger.warning(
            "registros_por_pagina=%d excede o limite documentado da Omie (%d); "
            "a API deve ignorar o excesso e paginar em blocos de %d mesmo assim.",
            registros_por_pagina, LIMITE_REGISTROS_POR_PAGINA, LIMITE_REGISTROS_POR_PAGINA,
        )

    campo_de, campo_ate = _PREFIXO_FILTRO_DATA[filtro_data]
    param: dict[str, Any] = {
        "nPagina": pagina,
        "nRegPorPagina": registros_por_pagina,
        # Sem isso, "observacao" some (None) em ~70% dos títulos mesmo quando
        # preenchida na Omie — confirmado comparando com PesquisarLancamentos,
        # que sempre retorna observacao. Não afeta o rateio de categorias (veja
        # nota no topo do arquivo): "categorias" continua vazio mesmo com
        # lDadosCad=True.
        "lDadosCad": True,
    }
    if data_inicio:
        param[campo_de] = data_inicio
    if data_fim:
        param[campo_ate] = data_fim
    if natureza:
        param["cNatureza"] = natureza
    if status:
        param["cStatus"] = status
    return param


# Cada "movimento" retornado pode ser um de vários tipos de registro dentro da
# mesma consulta — confirmado por sonda contra uma conta real. Outro grupo
# observado, além dos títulos de fato (CONTA_A_PAGAR/CONTA_A_RECEBER, mesma
# granularidade do PesquisarLancamentos) e das previsões (PREVISAO_CONTRATO,
# ver `_adaptar_movimento`):
#   - CONTA_CORRENTE_PAG / CONTA_CORRENTE_REC: o lançamento da baixa no
#     extrato bancário — um SEGUNDO registro para o MESMO nCodTitulo já
#     coberto por CONTA_A_PAGAR/CONTA_A_RECEBER, com resumo.nValPago
#     repetido (sem nValorTitulo/nValAberto). Incluí-los duplica "Valor
#     Pago/Recebido" ao somar por título.
# Por segurança, filtramos por allow-list (não por exclusão): qualquer grupo
# não listado aqui é ignorado, para não arriscar incluir algo não mapeado.
_GRUPOS_TITULO = {"CONTA_A_PAGAR", "CONTA_A_RECEBER", "PREVISAO_CONTRATO"}


def _adaptar_movimento(movimento: dict[str, Any]) -> dict[str, Any]:
    """Reformata um item de `movimentos` no mesmo shape `{cabecTitulo, resumo}`
    que `report_builder` já consome (originalmente pensado para
    PesquisarLancamentos), para reaproveitar toda a lógica de agregação sem
    duplicá-la para esta fonte de dados. O array `categorias` (rateio) é
    ignorado — veja a nota no topo do arquivo.

    PREVISAO_CONTRATO (previsão de faturamento de contrato, ainda não é um
    título lançado) retorna um `resumo` reduzido — só `nValLiquido`, sem
    `nValPago`/`nValAberto`/`cLiquidado`. Sem isso, o título apareceria com
    "Valor Aberto" e "Valor Pago" zerados mesmo tendo um valor previsto, o que
    quebraria a leitura de "Situação do Vencimento"/Resumo. Tratamos o valor
    líquido da previsão como "em aberto" nesse caso — decisão explícita (não
    documentada pela Omie), para manter a reconciliação Valor Título ≈ Pago +
    Aberto também para previsões.
    """
    resumo = dict(movimento.get("resumo") or {})
    if "nValAberto" not in resumo and "nValLiquido" in resumo:
        resumo["nValAberto"] = resumo["nValLiquido"]
    return {"cabecTitulo": dict(movimento.get("detalhes") or {}), "resumo": resumo}


def _filtrar_e_adaptar(movimentos: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Aplica a allow-list de `_GRUPOS_TITULO` e adapta cada item mantido.
    Retorna (títulos adaptados, contagem de registros ignorados por cGrupo)."""
    titulos: list[dict[str, Any]] = []
    ignorados: dict[str, int] = {}
    for m in movimentos:
        grupo = (m.get("detalhes") or {}).get("cGrupo")
        if grupo not in _GRUPOS_TITULO:
            ignorados[grupo] = ignorados.get(grupo, 0) + 1
            continue
        titulos.append(_adaptar_movimento(m))
    return titulos, ignorados


def contar_movimentos(
    client: OmieClient,
    data_inicio: str | None = None,
    data_fim: str | None = None,
    natureza: str | None = None,
    filtro_data: FiltroData = "vencimento",
    status: str | None = None,
) -> int:
    """Sonda barata (1 registro) para descobrir quantos movimentos os filtros
    atuais vão retornar, sem paginar tudo."""
    param = _montar_param(data_inicio, data_fim, filtro_data, natureza, status, registros_por_pagina=1, pagina=1)
    resp = client.call(MODULO, CHAMADA, param)
    return resp.get("nTotRegistros", 0) or 0


def buscar_movimentos(
    client: OmieClient,
    data_inicio: str | None = None,
    data_fim: str | None = None,
    natureza: str | None = None,
    filtro_data: FiltroData = "vencimento",
    status: str | None = None,
    registros_por_pagina: int = LIMITE_REGISTROS_POR_PAGINA,
) -> list[dict[str, Any]]:
    """Retorna os movimentos já adaptados para `{"cabecTitulo": ..., "resumo": ...}`,
    prontos para `report_builder.montar_linhas`/`montar_geral`.

    `natureza` é opcional (`"P"`, `"R"` ou `None`/omitido para os dois juntos) —
    ao contrário de `titulos.buscar_titulos`, uma única busca paginada já cobre
    Pagar e Receber quando `natureza` não é informado.
    """
    todos: list[dict[str, Any]] = []
    ignorados_total: dict[str, int] = {}
    pagina = 1
    while True:
        param = _montar_param(data_inicio, data_fim, filtro_data, natureza, status, registros_por_pagina, pagina)
        resp = client.call(MODULO, CHAMADA, param)

        movimentos = resp.get("movimentos") or []
        titulos, ignorados = _filtrar_e_adaptar(movimentos)
        todos.extend(titulos)
        for grupo, qtd in ignorados.items():
            ignorados_total[grupo] = ignorados_total.get(grupo, 0) + qtd

        total_paginas = resp.get("nTotPaginas", 1) or 1
        total_registros = resp.get("nTotRegistros", len(movimentos))
        logger.info(
            "Página %d/%d — %d movimentos nesta página (total no período: %d)",
            pagina, total_paginas, len(movimentos), total_registros,
        )

        if pagina >= total_paginas or not movimentos:
            break
        pagina += 1

    if ignorados_total:
        logger.info(
            "Ignorados %d registros que não são títulos (cGrupo fora de %s): %s",
            sum(ignorados_total.values()), sorted(_GRUPOS_TITULO), ignorados_total,
        )

    return todos
