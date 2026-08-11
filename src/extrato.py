"""Saldo de caixa (real + previsto) via `financas/extrato` (`ListarExtrato`).

Soma o saldo real das contas correntes operacionais configuradas (saldo já
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

def resolver_contas_alvo(
    cc_map: dict[int, str], contas_alvo: tuple[tuple[str, str], ...]
) -> dict[str, int]:
    """Mapeia as contas configuradas em `OMIE_CONTAS_CAIXA` (`.env`, lido por
    `config.carregar_config` como pares `(nome no cadastro, rótulo de
    exibição)`) -> `nCodCC`, por correspondência **exata** com a descrição
    cadastrada em `ListarContasCorrentes`. Os nomes de banco não ficam
    hardcoded no código — só no `.env` de cada instalação.

    Lança `ValueError` se `contas_alvo` estiver vazio (variável não
    configurada) ou se alguma conta não for encontrada no cadastro — falha
    explícita em vez de silenciar uma conta ausente do cálculo de saldo."""
    if not contas_alvo:
        raise ValueError(
            "OMIE_CONTAS_CAIXA não configurado no .env — defina as contas correntes "
            "a considerar no saldo de caixa (veja o exemplo em .env.example)."
        )

    por_nome_cadastro = {desc: cod for cod, desc in cc_map.items()}
    resolvidas: dict[str, int] = {}
    faltando: list[str] = []
    for nome_cadastro, rotulo in contas_alvo:
        if nome_cadastro in por_nome_cadastro:
            resolvidas[rotulo] = por_nome_cadastro[nome_cadastro]
        else:
            faltando.append(nome_cadastro)

    if faltando:
        raise ValueError(
            f"Contas correntes não encontradas no cadastro Omie: {faltando}. "
            "Verifique OMIE_CONTAS_CAIXA no .env contra os nomes cadastrados em ListarContasCorrentes."
        )
    return resolvidas


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
    contas_alvo: tuple[tuple[str, str], ...],
    hoje: date | None = None,
    dias_previsao: int = 90,
) -> dict[str, Any]:
    """Monta o saldo de caixa das contas operacionais configuradas em
    `OMIE_CONTAS_CAIXA` (`.env`): saldo real de hoje (soma do `nSaldoAnterior`
    de cada conta) + lançamentos previstos "a vencer" nos próximos
    `dias_previsao` dias + saldo previsto (real + previstos), com um recorte
    semanal do fluxo projetado.
    """
    hoje = hoje or date.today()
    hoje_str = hoje.strftime("%d/%m/%Y")
    fim_str = (hoje + timedelta(days=dias_previsao)).strftime("%d/%m/%Y")

    contas = resolver_contas_alvo(cc_map, contas_alvo)

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
