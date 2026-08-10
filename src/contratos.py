"""Agrupa títulos e previsões (`ListarMovimentos`) por contrato (`nCodCtr`),
para alimentar o painel de visualização de contratos.

Só o `ListarMovimentos` expõe `PREVISAO_CONTRATO` — por isso este módulo
consome a saída de `movimentos.buscar_movimentos`, não `titulos.buscar_titulos`.

`nCodCtr`/`cNumCtr` é o mesmo valor em todas as parcelas de um contrato,
tanto nos títulos já lançados (`CONTA_A_PAGAR`/`CONTA_A_RECEBER`) quanto nas
previsões futuras (`PREVISAO_CONTRATO`) — confirmado empiricamente contra uma
conta real. Títulos sem `nCodCtr` (a maioria — despesas avulsas, notas
fiscais sem contrato formal) não são "contrato" e ficam de fora deste
agrupamento.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .report_builder import _num, _parse_data

_STATUS_LABEL_NATUREZA = {"P": "Pagar", "R": "Receber"}


def _situacao_parcela(cabec: dict[str, Any]) -> str:
    """Classifica uma parcela (título ou previsão) num dos 5 estados que o
    painel exibe. Usa `cStatus` do `ListarMovimentos` como fonte de verdade
    (já vem calculado pela Omie) em vez de rederivar a partir de datas —
    mais simples e não diverge do que a própria Omie mostra.

    "Pago no prazo" vs. "com atraso" é a única derivação feita aqui: compara
    `dDtPagamento` com `dDtVenc` (a Omie não expõe esse diferencial pronto).
    """
    status = cabec.get("cStatus")
    if status == "PREVISAO":
        return "Previsto"
    if status == "CANCELADO":
        return "Cancelado"
    if status in ("PAGO", "RECEBIDO"):
        venc = _parse_data(cabec.get("dDtVenc"))
        pagamento = _parse_data(cabec.get("dDtPagamento"))
        if venc and pagamento and pagamento > venc:
            return "Pago com atraso"
        return "Pago no prazo"
    if status == "ATRASADO":
        return "Atrasado"
    return "Em aberto"  # "A VENCER", "VENCE HOJE" e quaisquer outros


def _status_contrato(parcelas: list[dict[str, Any]]) -> str:
    """Status agregado do contrato, por prioridade:

    1. "Em atraso" — tem ao menos uma parcela vencida e não paga.
    2. "Ativo" — não está em atraso e tem ao menos uma parcela futura,
       prevista ou já emitida e ainda não vencida (a Omie continua gerando
       cobrança para esse contrato). Nos dados reais, "Em aberto" sempre
       vem acompanhado de "Previsto" enquanto o contrato segue rodando —
       mas checar os dois evita classificar como "Encerrado" um contrato
       cuja última parcela emitida ainda nem venceu.
    3. "Cancelado" — todas as parcelas já lançadas (exclui previsões) estão
       canceladas, e não há parcela futura.
    4. "Encerrado" — nenhuma das situações acima: teve parcelas normais no
       passado, mas não há mais nada previsto (contrato parece ter parado).
    """
    situacoes = [p["situacao"] for p in parcelas]
    if "Atrasado" in situacoes:
        return "Em atraso"
    if "Previsto" in situacoes or "Em aberto" in situacoes:
        return "Ativo"
    if situacoes and all(s == "Cancelado" for s in situacoes):
        return "Cancelado"
    return "Encerrado"


def montar_contratos(
    titulos_raw: list[dict[str, Any]],
    categoria_map: dict[str, dict[str, str]],
    cc_map: dict[int, str],
    cliente_map: dict[int, dict[str, Any]],
    hoje: date | None = None,
) -> list[dict[str, Any]]:
    """Agrupa `titulos_raw` (saída de `movimentos.buscar_movimentos`) por
    `nCodCtr` e retorna uma lista de contratos, cada um com suas parcelas
    (passadas e futuras) e um status agregado. Títulos sem `nCodCtr` são
    ignorados — não representam um contrato formal na Omie.
    """
    hoje = hoje or date.today()
    from .report_builder import _categoria_descricao  # import local: evita ciclo no topo

    grupos: dict[Any, list[dict[str, Any]]] = {}
    for titulo in titulos_raw:
        cabec = titulo.get("cabecTitulo", {}) or {}
        cod_ctr = cabec.get("nCodCtr")
        if not cod_ctr:
            continue
        grupos.setdefault(cod_ctr, []).append(titulo)

    contratos: list[dict[str, Any]] = []
    for cod_ctr, titulos_do_contrato in grupos.items():
        titulos_do_contrato.sort(key=lambda t: _parse_data((t.get("cabecTitulo") or {}).get("dDtVenc")) or hoje)

        parcelas: list[dict[str, Any]] = []
        for titulo in titulos_do_contrato:
            cabec = titulo.get("cabecTitulo", {}) or {}
            parcelas.append(
                {
                    "nCodTitulo": cabec.get("nCodTitulo"),
                    "vencimento": cabec.get("dDtVenc"),
                    "pagamento": cabec.get("dDtPagamento") or None,
                    "valor": _num(cabec.get("nValorTitulo")),
                    "status_omie": cabec.get("cStatus"),
                    "situacao": _situacao_parcela(cabec),
                }
            )

        primeiro_cabec = titulos_do_contrato[0].get("cabecTitulo", {}) or {}
        cod_cliente = primeiro_cabec.get("nCodCliente")
        cad = cliente_map.get(cod_cliente) or {}
        cod_categ = primeiro_cabec.get("cCodCateg", "")
        natureza = primeiro_cabec.get("cNatureza", "")

        # Valor recorrente = valor da parcela mais recente não cancelada
        # (previsão se houver, senão a última realizada) — a maioria dos
        # contratos tem valor constante entre parcelas, mas usar a mais
        # recente cobre reajustes.
        nao_canceladas = [p for p in parcelas if p["situacao"] != "Cancelado"]
        valor_recorrente = nao_canceladas[-1]["valor"] if nao_canceladas else parcelas[-1]["valor"]

        # Inclui "Atrasado": se a única parcela pendente já venceu, ela é o
        # próximo pagamento a acompanhar — deixar `proxima_parcela` em branco
        # nesse caso escondia exatamente os contratos que mais precisam de
        # atenção (ex.: um contrato "Em atraso" com uma única parcela vencida
        # ficava sem nenhuma parcela sinalizada aqui).
        proximas = [p for p in parcelas if p["situacao"] in ("Previsto", "Em aberto", "Atrasado")]
        proxima_parcela = min(proximas, key=lambda p: _parse_data(p["vencimento"]) or hoje) if proximas else None

        resumo = {
            "total_parcelas": len(parcelas),
            "pagas_no_prazo": sum(1 for p in parcelas if p["situacao"] == "Pago no prazo"),
            "pagas_com_atraso": sum(1 for p in parcelas if p["situacao"] == "Pago com atraso"),
            "em_aberto": sum(1 for p in parcelas if p["situacao"] == "Em aberto"),
            "atrasadas": sum(1 for p in parcelas if p["situacao"] == "Atrasado"),
            "previstas": sum(1 for p in parcelas if p["situacao"] == "Previsto"),
            "canceladas": sum(1 for p in parcelas if p["situacao"] == "Cancelado"),
        }

        contratos.append(
            {
                "nCodCtr": cod_ctr,
                "cNumCtr": primeiro_cabec.get("cNumCtr"),
                "cliente": cad.get("nome_fantasia") or cad.get("razao_social") or "",
                "natureza": _STATUS_LABEL_NATUREZA.get(natureza, natureza),
                "categoria": _categoria_descricao(categoria_map, cod_categ),
                "conta_corrente": cc_map.get(primeiro_cabec.get("nCodCC"), ""),
                "valor_recorrente": valor_recorrente,
                "status_contrato": _status_contrato(parcelas),
                "proxima_parcela": proxima_parcela,
                "resumo": resumo,
                "parcelas": parcelas,
            }
        )

    contratos.sort(key=lambda c: (c["status_contrato"] != "Em atraso", c["cliente"]))
    return contratos
