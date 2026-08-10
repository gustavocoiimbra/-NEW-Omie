"""Saldo de caixa (real + previsto) via `financas/extrato` (`ListarExtrato`).

Soma o saldo real das contas correntes operacionais da Centria (saldo já
conciliado no fechamento do dia anterior) com os lançamentos previstos ("a
vencer") do extrato bancário, agregados por semana, para uma projeção simples
de fluxo de caixa.

Achados empíricos contra uma conta real (não documentados pela Omie):

- `nSaldoAnterior` no retorno de `ListarExtrato` é o saldo já conciliado no
  fechamento do dia **anterior** a `dPeriodoInicial` — consultando com
  `dPeriodoInicial=hoje`, esse campo é exatamente "o saldo real de hoje".
- `listaMovimentos` inclui linhas que não são lançamentos de verdade
  (`cDesCliente` "SALDO"/"SALDO ANTERIOR", marcadores de saldo diário sem
  `cSituacao`) — só as com `cCodLancamento`/`cSituacao` preenchidos são
  transações reais.
- `cSituacao="Previsto"` cobre tanto o que ainda vai vencer quanto o que já
  venceu e segue em aberto: um título com `cStatus=ATRASADO` no
  `ListarMovimentos` aparece aqui como `cSituacao="Previsto"` com
  `dDataLancamento` no passado. Por isso, "previsto a vencer" exige filtrar
  também por `dDataLancamento >= hoje` — só `cSituacao="Previsto"` não basta
  para excluir os títulos em atraso.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from .omie_client import OmieClient

logger = logging.getLogger("extrato")

MODULO = "financas/extrato"
CHAMADA = "ListarExtrato"

# Nomes de exibição das 4 contas operacionais consideradas no saldo de caixa
# da Centria — resolvidos por substring (case-insensitive) contra a descrição
# cadastrada em ListarContasCorrentes. Contas de caixinha/adiantamento/Omie.CASH
# ficam de fora propositalmente.
CONTAS_ALVO = ("Banco Bradesco", "Banco Bradesco Aplicação", "Banco Itaú", "Banco Itaú Aplicação")


def resolver_contas_alvo(cc_map: dict[int, str]) -> dict[str, int]:
    """Mapeia os 4 nomes de `CONTAS_ALVO` -> `nCodCC`, a partir do cadastro
    retornado por `enrichment.build_conta_corrente_map`. Lança `ValueError`
    se alguma das 4 não for encontrada (falha explícita em vez de silenciar
    uma conta ausente do cálculo de saldo)."""
    resolvidas: dict[str, int] = {}
    for cod, desc in cc_map.items():
        d = desc.lower()
        eh_aplicacao = "aplica" in d
        if "bradesco" in d:
            resolvidas["Banco Bradesco Aplicação" if eh_aplicacao else "Banco Bradesco"] = cod
        elif "itau" in d or "itaú" in d:
            resolvidas["Banco Itaú Aplicação" if eh_aplicacao else "Banco Itaú"] = cod

    faltando = set(CONTAS_ALVO) - resolvidas.keys()
    if faltando:
        raise ValueError(
            f"Contas correntes não encontradas no cadastro Omie: {sorted(faltando)}. "
            "Verifique se os nomes cadastrados em ListarContasCorrentes mudaram."
        )
    return {nome: resolvidas[nome] for nome in CONTAS_ALVO}


def _parse_data(valor: str | None) -> date | None:
    if not valor:
        return None
    try:
        return datetime.strptime(valor, "%d/%m/%Y").date()
    except ValueError:
        return None


def buscar_extrato(client: OmieClient, n_cod_cc: int, data_inicio: str, data_fim: str) -> dict[str, Any]:
    """Chama `ListarExtrato` para uma conta corrente. Retorna a resposta bruta
    (inclui `nSaldoAnterior`, `nSaldoAtual` e `listaMovimentos`)."""
    return client.call(MODULO, CHAMADA, {
        "nCodCC": n_cod_cc,
        "dPeriodoInicial": data_inicio,
        "dPeriodoFinal": data_fim,
    })


def _lancamentos_previstos_da_conta(resp: dict[str, Any], nome_conta: str, hoje: date) -> list[dict[str, Any]]:
    """Filtra `listaMovimentos` para só os lançamentos previstos "a vencer":
    `cSituacao="Previsto"` e vencimento ainda não passado (exclui atrasados
    e marcadores de saldo diário)."""
    lancamentos = []
    for mov in resp.get("listaMovimentos") or []:
        if mov.get("cSituacao") != "Previsto":
            continue
        venc = _parse_data(mov.get("dDataLancamento"))
        if venc is None or venc < hoje:
            continue
        lancamentos.append({
            "conta": nome_conta,
            "data": mov["dDataLancamento"],
            "natureza": mov.get("cNatureza"),
            "cliente_fornecedor": mov.get("cRazCliente") or mov.get("cDesCliente") or "",
            "categoria": mov.get("cDesCategoria") or "",
            "valor": float(mov.get("nValorDocumento") or 0.0),
        })
    return lancamentos


def montar_saldo_caixa(
    client: OmieClient,
    cc_map: dict[int, str],
    hoje: date | None = None,
    dias_previsao: int = 90,
) -> dict[str, Any]:
    """Monta o saldo de caixa das 4 contas operacionais da Centria: saldo real
    de hoje (soma do `nSaldoAnterior` de cada conta) + lançamentos previstos
    "a vencer" nos próximos `dias_previsao` dias + saldo previsto (real +
    previstos), com um recorte semanal do fluxo projetado.
    """
    hoje = hoje or date.today()
    hoje_str = hoje.strftime("%d/%m/%Y")
    fim_str = (hoje + timedelta(days=dias_previsao)).strftime("%d/%m/%Y")

    contas = resolver_contas_alvo(cc_map)

    saldo_por_conta: dict[str, float] = {}
    lancamentos: list[dict[str, Any]] = []

    for nome, cod in contas.items():
        logger.info("Consultando extrato de %s (nCodCC=%s)...", nome, cod)
        resp = buscar_extrato(client, cod, hoje_str, fim_str)
        saldo_por_conta[nome] = float(resp.get("nSaldoAnterior") or 0.0)
        lancamentos.extend(_lancamentos_previstos_da_conta(resp, nome, hoje))

    lancamentos.sort(key=lambda l: _parse_data(l["data"]) or hoje)

    saldo_real_total = round(sum(saldo_por_conta.values()), 2)
    total_previsto = round(sum(l["valor"] for l in lancamentos), 2)

    return {
        "hoje": hoje.isoformat(),
        "dias_previsao": dias_previsao,
        "saldo_real_por_conta": saldo_por_conta,
        "saldo_real_total": saldo_real_total,
        "lancamentos_previstos": lancamentos,
        "total_previsto": total_previsto,
        "saldo_previsto_total": round(saldo_real_total + total_previsto, 2),
        "fluxo_semanal": montar_fluxo_semanal(saldo_real_total, lancamentos, hoje, dias_previsao),
    }


def montar_fluxo_semanal(
    saldo_real: float, lancamentos: list[dict[str, Any]], hoje: date, dias_previsao: int
) -> list[dict[str, Any]]:
    """Agrupa os lançamentos previstos por semana (segunda a domingo, a partir
    da semana de `hoje`) cobrindo todo o horizonte pedido — inclusive semanas
    sem nenhum lançamento, para o saldo acumulado não "pular" períodos.

    Para cada semana: entradas/saídas/líquido previstos naquela semana e o
    saldo previsto acumulado até o fim dela (`saldo_real` + soma de todos os
    lançamentos previstos desde hoje até ali) — é a "soma do saldo real +
    lançamentos previstos naquela semana" pedida.
    """
    por_semana: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for lanc in lancamentos:
        data = _parse_data(lanc["data"]) or hoje
        inicio_semana = data - timedelta(days=data.weekday())
        por_semana[inicio_semana].append(lanc)

    inicio_semana_atual = hoje - timedelta(days=hoje.weekday())
    fim_horizonte = hoje + timedelta(days=dias_previsao)

    resultado = []
    acumulado = saldo_real
    semana = inicio_semana_atual
    while semana <= fim_horizonte:
        itens = por_semana.get(semana, [])
        entradas = sum(l["valor"] for l in itens if l["valor"] >= 0)
        saidas = sum(l["valor"] for l in itens if l["valor"] < 0)
        liquido = entradas + saidas
        acumulado += liquido
        resultado.append({
            "inicio": semana.strftime("%d/%m/%Y"),
            "fim": (semana + timedelta(days=6)).strftime("%d/%m/%Y"),
            "entradas_previstas": round(entradas, 2),
            "saidas_previstas": round(saidas, 2),
            "liquido_previsto": round(liquido, 2),
            "saldo_previsto_acumulado": round(acumulado, 2),
        })
        semana += timedelta(days=7)

    return resultado
