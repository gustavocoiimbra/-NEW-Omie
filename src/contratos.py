"""Agrupa títulos e previsões (`ListarMovimentos`) por contrato (`nCodCtr`),
para alimentar o painel de visualização de contratos.

Só o `ListarMovimentos` expõe `PREVISAO_CONTRATO` — por isso este módulo
consome a saída de `movimentos.buscar_movimentos`, não `titulos.buscar_titulos`.

`nCodCtr`/`cNumCtr` é o mesmo valor em todas as parcelas de um contrato,
tanto nos títulos já lançados (`CONTA_A_PAGAR`/`CONTA_A_RECEBER`) quanto nas
previsões futuras (`PREVISAO_CONTRATO`) — confirmado empiricamente contra uma
conta real. Títulos sem `nCodCtr` (a maioria — despesas avulsas, notas
fiscais sem contrato formal) não são "contrato" e ficam de fora deste
agrupamento, exceto os poucos que a reconciliação heurística religar (veja
`_match_heuristico`).

Investigação completa (por que nem todo contrato aparece, o papel de
`ListarContratos`/`ListarOS`/`ConsultarContaReceber`) em
`sondas/PESQUISA_CONTRATOS_OS.md`. Duas lacunas confirmadas contra a conta
real motivaram as mudanças deste módulo:

1. Contratos cadastrados (`ListarContratos`) sem nenhum título com `nCodCtr`
   na janela consultada ficavam **totalmente ausentes** do relatório — agora
   `montar_contratos` recebe opcionalmente o cadastro
   (`contratos_cadastro.buscar_contratos_cadastro`) e inclui todo contrato
   cadastrado, mesmo sem nenhuma parcela casada.
2. Alguns títulos realmente faturados vêm sem `nCodCtr` (lançados por
   conciliação bancária manual, sem passar pelo módulo de contrato nem de
   OS) — `_match_heuristico` tenta religá-los ao contrato certo do mesmo
   cliente por proximidade de valor/data, sempre marcando o vínculo como
   `"heuristico"` (nunca mesclado silenciosamente como se fosse confirmado
   pela Omie).

Um título órfão sem candidato inequívoco não é simplesmente descartado: se
ele tem *alguma* característica de contrato (mesmo cliente de um contrato
cadastrado, natureza de receita) mas não passa nos critérios de
`_match_heuristico`, entra em `titulos_para_revisao` do(s) contrato(s)
candidato(s) — um sinal pra usuário avaliar manualmente e decidir se aprova
o vínculo, em vez de ficar invisível. Títulos `CANCELADO` são ignorados em
toda essa reconciliação (auto-ligação e sinal de revisão): um título
cancelado não representa nem um pagamento nem uma pendência real, então não
ajuda o usuário a decidir nada.
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


_TOLERANCIA_VALOR_HEURISTICO = 0.05  # 5% — calibrado contra casos reais, ver PESQUISA_CONTRATOS_OS.md


def _match_heuristico(cabec: dict[str, Any], candidatos: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Tenta religar um título sem `nCodCtr` a um contrato do cadastro do
    mesmo cliente. Só casa quando sobra **exatamente 1** candidato plausível
    depois de filtrar por:

    - natureza "R" (contratos em `servicos/contrato` são sempre de receita —
      um título "P" do mesmo cliente é uma relação diferente, não o contrato);
    - vencimento dentro de `dVigInicial`..`dVigFinal` do contrato;
    - valor a até `_TOLERANCIA_VALOR_HEURISTICO` de distância de `nValTotMes`.

    0 ou 2+ candidatos plausíveis => ambíguo, não casa (fica órfão mesmo).
    Critérios calibrados contra 8 títulos "Atrasado" órfãos reais — só 2
    tinham valor idêntico ao contrato E venciam dentro da vigência; os outros
    6 tinham valor e/ou data claramente incompatíveis (detalhes em
    `sondas/PESQUISA_CONTRATOS_OS.md`, seção 6).
    """
    if cabec.get("cNatureza") != "R":
        return None
    venc = _parse_data(cabec.get("dDtVenc"))
    valor = _num(cabec.get("nValorTitulo"))
    if venc is None or not valor:
        return None

    plausiveis = []
    for c in candidatos:
        inicio = _parse_data(c.get("dVigInicial"))
        fim = _parse_data(c.get("dVigFinal"))
        if inicio and venc < inicio:
            continue
        if fim and venc > fim:
            continue
        val_mes = _num(c.get("nValTotMes"))
        if not val_mes:
            continue
        if abs(valor - val_mes) / val_mes > _TOLERANCIA_VALOR_HEURISTICO:
            continue
        plausiveis.append(c)

    return plausiveis[0] if len(plausiveis) == 1 else None


def _revisao_motivo(cabec: dict[str, Any], candidato: dict[str, Any]) -> str:
    """Explica, em texto simples, por que um título do mesmo cliente de
    `candidato` não foi religado automaticamente por `_match_heuristico` —
    orienta o que o usuário precisa checar antes de aprovar o vínculo."""
    motivos = []
    venc = _parse_data(cabec.get("dDtVenc"))
    inicio = _parse_data(candidato.get("dVigInicial"))
    fim = _parse_data(candidato.get("dVigFinal"))
    if venc and inicio and venc < inicio:
        motivos.append("vencimento antes do início da vigência do contrato")
    if venc and fim and venc > fim:
        motivos.append("vencimento depois do fim da vigência do contrato")
    valor = _num(cabec.get("nValorTitulo"))
    val_mes = _num(candidato.get("nValTotMes"))
    if valor and val_mes:
        diff_pct = abs(valor - val_mes) / val_mes * 100
        if diff_pct > _TOLERANCIA_VALOR_HEURISTICO * 100:
            motivos.append(f"valor {diff_pct:.1f}% diferente do valor mensal cadastrado do contrato")
    if not motivos:
        motivos.append("mais de um contrato do mesmo cliente é compatível com este título — ambíguo")
    return "; ".join(motivos)


def montar_contratos(
    titulos_raw: list[dict[str, Any]],
    categoria_map: dict[str, dict[str, str]],
    cc_map: dict[int, str],
    cliente_map: dict[int, dict[str, Any]],
    contratos_cadastro: dict[int, dict[str, Any]] | None = None,
    hoje: date | None = None,
) -> list[dict[str, Any]]:
    """Agrupa `titulos_raw` (saída de `movimentos.buscar_movimentos`) por
    `nCodCtr` e retorna uma lista de contratos, cada um com suas parcelas
    (passadas e futuras) e um status agregado.

    `contratos_cadastro` (opcional, saída de
    `contratos_cadastro.buscar_contratos_cadastro`) é o catálogo mestre de
    `ListarContratos`. Quando informado:

    - todo contrato cadastrado entra no resultado, mesmo sem nenhum título
      casado (parcelas=[], status vem do cadastro — veja `status_cadastro`);
    - títulos sem `nCodCtr` passam por `_match_heuristico` antes de serem
      descartados, religando ao contrato certo quando não há ambiguidade
      (marcados com `"vinculo": "heuristico"` em vez de `"confirmado"`);
    - títulos sem `nCodCtr` que não passam em `_match_heuristico` mas são do
      mesmo cliente de algum contrato cadastrado entram em
      `"titulos_para_revisao"` desse(s) contrato(s) — não contam em
      `resumo`/`status_contrato`/`valor_recorrente`, é só um sinal pra
      avaliação manual. Títulos `CANCELADO` nunca entram nessa reconciliação
      (nem auto-ligação, nem sinal de revisão).

    Sem `contratos_cadastro`, o comportamento é o original: só contratos com
    ao menos um título trazendo `nCodCtr` aparecem, e `titulos_para_revisao`
    vem sempre vazio.
    """
    hoje = hoje or date.today()
    from .report_builder import _categoria_descricao  # import local: evita ciclo no topo

    contratos_cadastro = contratos_cadastro or {}
    candidatos_por_cliente: dict[Any, list[dict[str, Any]]] = {}
    for cadastro in contratos_cadastro.values():
        candidatos_por_cliente.setdefault(cadastro.get("nCodCli"), []).append(cadastro)

    grupos: dict[Any, list[tuple[dict[str, Any], str]]] = {}
    orfaos: list[dict[str, Any]] = []
    for titulo in titulos_raw:
        cabec = titulo.get("cabecTitulo", {}) or {}
        cod_ctr = cabec.get("nCodCtr")
        if cod_ctr:
            grupos.setdefault(cod_ctr, []).append((titulo, "confirmado"))
        else:
            orfaos.append(titulo)

    titulos_revisao_por_ctr: dict[Any, list[dict[str, Any]]] = {}
    for titulo in orfaos:
        cabec = titulo.get("cabecTitulo", {}) or {}
        if cabec.get("cStatus") == "CANCELADO":
            # Cancelado nao representa pagamento nem pendencia real -- nao
            # ajuda a decidir nada, nem auto-liga nem sinaliza pra revisao.
            continue
        candidatos = candidatos_por_cliente.get(cabec.get("nCodCliente"), [])
        alvo = _match_heuristico(cabec, candidatos)
        if alvo is not None:
            grupos.setdefault(alvo["nCodCtr"], []).append((titulo, "heuristico"))
        elif candidatos:
            # Tem cara de contrato (mesmo cliente de um contrato cadastrado)
            # mas nao passou nos criterios de auto-ligacao -- sinaliza pra
            # revisao manual em vez de descartar em silencio.
            for candidato in candidatos:
                titulos_revisao_por_ctr.setdefault(candidato["nCodCtr"], []).append({
                    "nCodTitulo": cabec.get("nCodTitulo"),
                    "vencimento": cabec.get("dDtVenc"),
                    "valor": _num(cabec.get("nValorTitulo")),
                    "status_omie": cabec.get("cStatus"),
                    "motivo": _revisao_motivo(cabec, candidato),
                })

    # Todo contrato do cadastro entra no resultado, mesmo sem nenhum título
    # casado — antes ficava totalmente ausente do relatório.
    for cod_ctr in contratos_cadastro:
        grupos.setdefault(cod_ctr, [])

    contratos: list[dict[str, Any]] = []
    for cod_ctr, titulos_do_contrato in grupos.items():
        cadastro = contratos_cadastro.get(cod_ctr)
        titulos_do_contrato.sort(
            key=lambda par: _parse_data((par[0].get("cabecTitulo") or {}).get("dDtVenc")) or hoje
        )

        parcelas: list[dict[str, Any]] = []
        for titulo, vinculo in titulos_do_contrato:
            cabec = titulo.get("cabecTitulo", {}) or {}
            parcelas.append(
                {
                    "nCodTitulo": cabec.get("nCodTitulo"),
                    "vencimento": cabec.get("dDtVenc"),
                    "pagamento": cabec.get("dDtPagamento") or None,
                    "valor": _num(cabec.get("nValorTitulo")),
                    "status_omie": cabec.get("cStatus"),
                    "situacao": _situacao_parcela(cabec),
                    "vinculo": vinculo,
                }
            )

        if titulos_do_contrato:
            primeiro_cabec = titulos_do_contrato[0][0].get("cabecTitulo", {}) or {}
            cod_categ = primeiro_cabec.get("cCodCateg", "")
            natureza = primeiro_cabec.get("cNatureza", "")
            cod_cc = primeiro_cabec.get("nCodCC")
            num_ctr = primeiro_cabec.get("cNumCtr") or (cadastro or {}).get("cNumCtr")
        else:
            # Contrato só existe no cadastro — nenhum título casado (nem
            # confirmado, nem heurístico) para inferir esses campos.
            cod_categ = ""
            natureza = ""
            cod_cc = None
            num_ctr = (cadastro or {}).get("cNumCtr")

        # Cliente: prioriza o cadastro do contrato (`nCodCli` de
        # ListarContratos) em vez de "o cliente do primeiro título por
        # vencimento" — caso real confirmado: o título mais antigo de um
        # contrato pode ter sido lançado com o cliente errado (erro de
        # digitação corrigido nas parcelas seguintes, nunca corrigido nessa
        # parcela cancelada) e distorcia o nome exibido do contrato inteiro.
        # Ver sondas/PESQUISA_CONTRATOS_OS.md, seção 5.
        if cadastro and cadastro.get("nCodCli"):
            cod_cliente = cadastro["nCodCli"]
        elif titulos_do_contrato:
            cod_cliente = titulos_do_contrato[0][0].get("cabecTitulo", {}).get("nCodCliente")
        else:
            cod_cliente = None

        cad = cliente_map.get(cod_cliente) or {}

        # Valor recorrente = valor da parcela mais recente não cancelada
        # (previsão se houver, senão a última realizada) — a maioria dos
        # contratos tem valor constante entre parcelas, mas usar a mais
        # recente cobre reajustes. Sem nenhuma parcela, cai para o valor
        # mensal cadastrado no contrato.
        nao_canceladas = [p for p in parcelas if p["situacao"] != "Cancelado"]
        if nao_canceladas:
            valor_recorrente = nao_canceladas[-1]["valor"]
        elif parcelas:
            valor_recorrente = parcelas[-1]["valor"]
        else:
            valor_recorrente = _num((cadastro or {}).get("nValTotMes"))

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

        # `status_cadastro` (de ListarContratos) e o status calculado a partir
        # das parcelas divergem com frequência (contrato "Ativo" no cadastro
        # mas sem nada em aberto/previsto há meses) — nenhum substitui o
        # outro. Sem nenhuma parcela, o cadastro é o único sinal disponível.
        status_cadastro = (cadastro or {}).get("status_cadastro")
        if parcelas or not status_cadastro:
            status_contrato = _status_contrato(parcelas)
            origem_status = "parcelas"
        else:
            status_contrato = status_cadastro
            origem_status = "cadastro"

        contratos.append(
            {
                "nCodCtr": cod_ctr,
                "cNumCtr": num_ctr,
                "cliente": cad.get("nome_fantasia") or cad.get("razao_social") or "",
                "natureza": _STATUS_LABEL_NATUREZA.get(natureza, natureza),
                "categoria": _categoria_descricao(categoria_map, cod_categ) if cod_categ else "",
                "conta_corrente": cc_map.get(cod_cc, "") if cod_cc else "",
                "valor_recorrente": valor_recorrente,
                "status_contrato": status_contrato,
                "status_cadastro": status_cadastro,
                "origem_status": origem_status,
                "proxima_parcela": proxima_parcela,
                "resumo": resumo,
                "parcelas": parcelas,
                "titulos_para_revisao": titulos_revisao_por_ctr.get(cod_ctr, []),
            }
        )

    contratos.sort(key=lambda c: (c["status_contrato"] != "Em atraso", c["cliente"]))
    return contratos
