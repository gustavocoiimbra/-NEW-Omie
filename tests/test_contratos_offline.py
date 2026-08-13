"""Teste offline (sem rede) para src/contratos.py: cobre os dois bugs
encontrados em revisão de código e corrigidos em seguida, mais o uso do
cadastro de contratos (ListarContratos) e a reconciliação heurística de
títulos órfãos investigados em sondas/PESQUISA_CONTRATOS_OS.md —

1. Contrato com única parcela pendente já vencida ("Atrasado", sem nenhuma
   "Previsto"/"Em aberto") tinha `proxima_parcela=None`, escondendo o pagamento
   mais urgente do contrato (casos reais: MECANIZOU, V4 COMPANY).
2. Contrato cuja única parcela futura já foi emitida mas ainda não venceu
   ("Em aberto", sem nenhuma "Previsto" associada) era classificado como
   "Encerrado" em vez de "Ativo".
3. Contrato cadastrado sem nenhum título com nCodCtr na janela consultada
   ficava totalmente ausente do relatório — agora aparece com status vindo
   do cadastro (`status_cadastro`/`cCodSit`).
4. Título faturado sem nCodCtr (ex.: lançado por conciliação bancária
   manual) é religado ao contrato certo do mesmo cliente quando há
   exatamente 1 candidato plausível (mesma natureza, vencimento dentro da
   vigência, valor ~igual ao valor mensal cadastrado) — e permanece órfão
   quando ambíguo ou incompatível.

Uso:
    python tests/test_contratos_offline.py
"""
from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import contratos as ctr_mod  # noqa: E402

CATEGORIA_MAP = {"1.01.99": {"descricao": "Retainer Fee", "categoria_superior": "", "codigo_dre": ""}}
CC_MAP = {1: "Banco X - Conta Corrente"}
CLIENTE_MAP = {
    555: {"razao_social": "Cliente Alfa Ltda", "nome_fantasia": "Alfa", "cnpj_cpf": ""},
    556: {"razao_social": "Cliente Beta Ltda", "nome_fantasia": "Beta", "cnpj_cpf": ""},
    557: {"razao_social": "Cliente Gama Ltda", "nome_fantasia": "Gama", "cnpj_cpf": ""},
    558: {"razao_social": "Cliente Delta Ltda", "nome_fantasia": "Delta", "cnpj_cpf": ""},
}
HOJE = date(2026, 8, 6)


def _titulo(
    n_cod_titulo, n_cod_ctr, cod_cliente, status, venc, pagamento=None, valor=1000.0, n_cod_os=1,
    c_origem="VENR", emissao=None, categoria="1.01.99", impostos=None,
):
    return {
        "cabecTitulo": {
            "nCodTitulo": n_cod_titulo,
            "nCodCtr": n_cod_ctr,
            "cNumCtr": f"2026/{n_cod_ctr:05d}" if n_cod_ctr is not None else None,
            "nCodCliente": cod_cliente,
            "nCodCC": 1,
            "nCodOS": n_cod_os,
            "cOrigem": c_origem,
            "cCodCateg": categoria,
            "cNatureza": "R",
            "cStatus": status,
            "dDtVenc": venc,
            "dDtEmissao": emissao or venc,
            "dDtPagamento": pagamento or "",
            "nValorTitulo": valor,
            **(impostos or {}),
        },
        "resumo": {},
    }


def main() -> None:
    titulos_raw = [
        # Contrato 1 (Alfa): 3 parcelas pagas + 1 ATRASADO, sem Previsto/Em
        # aberto -> deve ficar "Em atraso" com proxima_parcela = a vencida.
        _titulo(1001, 1, 555, "RECEBIDO", "30/04/2026", "30/04/2026"),
        _titulo(1002, 1, 555, "RECEBIDO", "30/05/2026", "30/05/2026"),
        _titulo(1003, 1, 555, "RECEBIDO", "30/06/2026", "30/06/2026"),
        _titulo(1004, 1, 555, "ATRASADO", "30/07/2026", valor=1500.0),

        # Contrato 2 (Beta): 2 parcelas pagas + 1 "A VENCER" (emitida, ainda
        # nao vencida) e NENHUMA PREVISAO_CONTRATO -> deve ficar "Ativo",
        # nao "Encerrado", com proxima_parcela = a parcela em aberto.
        _titulo(2001, 2, 556, "RECEBIDO", "30/06/2026", "30/06/2026"),
        _titulo(2002, 2, 556, "RECEBIDO", "30/07/2026", "30/07/2026"),
        _titulo(2003, 2, 556, "A VENCER", "30/09/2026", valor=2000.0),

        # Contrato 3 (Gama): so parcelas canceladas -> "Cancelado".
        _titulo(3001, 3, 557, "CANCELADO", "30/06/2026"),
        _titulo(3002, 3, 557, "CANCELADO", "30/07/2026"),

        # Contrato 4 (Delta): so parcelas pagas no passado, nada pendente
        # nem previsto -> "Encerrado" (comportamento correto, sem mudanca).
        _titulo(4001, 4, 558, "RECEBIDO", "30/01/2026", "30/01/2026"),
        _titulo(4002, 4, 558, "RECEBIDO", "28/02/2026", "28/02/2026"),
    ]

    contratos = ctr_mod.montar_contratos(titulos_raw, CATEGORIA_MAP, CC_MAP, CLIENTE_MAP, hoje=HOJE)
    por_cliente = {c["cliente"]: c for c in contratos}
    assert len(contratos) == 4

    alfa = por_cliente["Alfa"]
    assert alfa["status_contrato"] == "Em atraso"
    assert alfa["proxima_parcela"] is not None, "bug: proxima_parcela nao pode ficar None quando a unica pendencia esta atrasada"
    assert alfa["proxima_parcela"]["nCodTitulo"] == 1004
    assert alfa["proxima_parcela"]["situacao"] == "Atrasado"
    print("OK: contrato com unica pendencia atrasada -> Em atraso, proxima_parcela preenchida")

    beta = por_cliente["Beta"]
    assert beta["status_contrato"] == "Ativo", (
        f"bug: parcela 'Em aberto' sem 'Previsto' associada nao pode virar 'Encerrado' (veio {beta['status_contrato']!r})"
    )
    assert beta["proxima_parcela"] is not None
    assert beta["proxima_parcela"]["nCodTitulo"] == 2003
    assert beta["proxima_parcela"]["situacao"] == "Em aberto"
    print("OK: contrato com unica parcela futura 'Em aberto' (sem Previsto) -> Ativo, nao Encerrado")

    gama = por_cliente["Gama"]
    assert gama["status_contrato"] == "Cancelado"
    assert gama["proxima_parcela"] is None
    print("OK: contrato 100% cancelado -> Cancelado")

    delta = por_cliente["Delta"]
    assert delta["status_contrato"] == "Encerrado"
    assert delta["proxima_parcela"] is None
    print("OK: contrato sem nada pendente ou previsto -> Encerrado (regressao: comportamento inalterado)")

    _testar_cadastro_e_heuristica()
    _testar_substituto_cancelado()
    _testar_pagamento_atrasado()
    _testar_pagamento_atrasado_melhorias()
    _testar_valor_referencia_contrato()

    print("\nTodos os testes offline de contratos passaram.")


def _testar_cadastro_e_heuristica() -> None:
    cliente_map = {
        555: {"razao_social": "Cliente Alfa Ltda", "nome_fantasia": "Alfa", "cnpj_cpf": ""},
        900: {"razao_social": "Cliente Epsilon Ltda", "nome_fantasia": "Epsilon", "cnpj_cpf": ""},
        901: {"razao_social": "Cliente Zeta Ltda", "nome_fantasia": "Zeta", "cnpj_cpf": ""},
    }

    contratos_cadastro = {
        # Contrato ativo no cadastro, mas nenhum titulo com nCodCtr=10 vai
        # aparecer nos titulos_raw abaixo -> deve aparecer mesmo assim, com
        # status vindo do cadastro.
        10: {
            "nCodCtr": 10, "cNumCtr": "2026/00010", "nCodCli": 900,
            "cCodSit": "10", "status_cadastro": "Ativo",
            "dVigInicial": "01/01/2026", "dVigFinal": "31/12/2026",
            "nDiaFat": 5, "nValTotMes": 5000.0,
        },
        # Contrato da Zeta: titulo orfao (sem nCodCtr) do mesmo cliente, com
        # valor e vencimento batendo com este contrato -> deve ser religado.
        11: {
            "nCodCtr": 11, "cNumCtr": "2026/00011", "nCodCli": 901,
            "cCodSit": "10", "status_cadastro": "Ativo",
            "dVigInicial": "01/01/2026", "dVigFinal": "31/12/2026",
            "nDiaFat": 10, "nValTotMes": 3000.0,
        },
    }

    titulo_alfa = _titulo(1001, 1, 555, "RECEBIDO", "30/04/2026", "30/04/2026")

    # Orfao plausivel: mesmo cliente do contrato 11, valor identico, dentro
    # da vigencia -> deve casar (heuristico). Vencimento a menos de 60 dias
    # de HOJE de proposito, pra nao acionar a busca de pagamento avulso de
    # atrasados (_buscar_pagamento_atrasado, testada em separado) e
    # continuar isolando so o comportamento de auto-ligacao aqui.
    titulo_orfao_zeta = _titulo(9001, None, 901, "ATRASADO", "10/07/2026", valor=3000.0)

    # Orfao dentro da faixa de revisao (mesmo cliente 901, fora da vigencia
    # do contrato 11, valor 20% diferente -> fora da tolerancia de
    # auto-ligacao, mas dentro do teto de plausibilidade pra revisao) -> nao
    # deve casar automaticamente, mas deve virar sinal de revisao manual.
    titulo_orfao_zeta_revisao = _titulo(9002, None, 901, "ATRASADO", "10/03/2027", valor=3600.0)

    # Orfao MUITO implausivel (mesmo cliente 901, valor 66.7% diferente --
    # alem do teto de revisao) -> nao e "quase uma correspondencia", e outra
    # cobranca sem relacao -> nao pode aparecer nem religado nem em revisao
    # (achado real: PODPAH, titulo 100% diferente do valor do contrato
    # sendo sinalizado antes do teto existir -- ver PESQUISA_CONTRATOS_OS.md).
    titulo_orfao_zeta_muito_diferente = _titulo(9005, None, 901, "ATRASADO", "10/03/2026", valor=999.0)

    # Orfao CANCELADO do mesmo cliente -> nao representa pagamento nem
    # pendencia real, nao pode nem religar nem virar sinal de revisao.
    titulo_orfao_zeta_cancelado = _titulo(9003, None, 901, "CANCELADO", "10/06/2026", valor=3000.0)

    # Orfao com valor E data batendo, mas SEM Ordem de Servico (lancamento
    # manual) -> ANTES nao podia ser auto-ligado nem com correspondencia
    # perfeita (achado real do FISERV, PESQUISA_CONTRATOS_OS.md secao 12).
    # Essa exigencia foi removida por pedido explicito (lancamentos raramente
    # tem OS vinculada) -- agora DEVE religar como qualquer outro orfao
    # plausivel. Mantido o nome/comentario historico pra deixar clara a
    # mudanca de comportamento.
    titulo_orfao_zeta_sem_os = _titulo(9006, None, 901, "ATRASADO", "10/04/2026", valor=3000.0, n_cod_os=None)

    # Orfao com valor batendo mas vencimento DEPOIS do fim da vigencia
    # cadastrada do contrato 11 (31/12/2026) -> ANTES nao podia ser
    # auto-ligado por causa do teto de vigencia. Essa exigencia tambem foi
    # removida por pedido explicito (contrato renovado informalmente segue
    # sendo cobrado depois do fim de vigencia formal) -- agora DEVE religar.
    titulo_orfao_zeta_depois_vigencia = _titulo(9007, None, 901, "ATRASADO", "15/02/2027", valor=3000.0)

    titulos_raw = [
        titulo_alfa, titulo_orfao_zeta, titulo_orfao_zeta_revisao,
        titulo_orfao_zeta_muito_diferente, titulo_orfao_zeta_cancelado, titulo_orfao_zeta_sem_os,
        titulo_orfao_zeta_depois_vigencia,
    ]

    contratos = ctr_mod.montar_contratos(
        titulos_raw, CATEGORIA_MAP, CC_MAP, cliente_map, contratos_cadastro, hoje=HOJE
    )
    por_ctr = {c["nCodCtr"]: c for c in contratos}

    assert 10 in por_ctr, "bug: contrato cadastrado sem titulo casado nao pode desaparecer do relatorio"
    epsilon = por_ctr[10]
    assert epsilon["cliente"] == "Epsilon"
    assert epsilon["parcelas"] == []
    assert epsilon["status_contrato"] == "Ativo"
    assert epsilon["status_cadastro"] == "Ativo"
    assert epsilon["origem_status"] == "cadastro"
    assert epsilon["valor_recorrente"] == 5000.0
    print("OK: contrato cadastrado sem nenhum titulo casado aparece no relatorio, status vem do cadastro")

    zeta = por_ctr[11]
    assert zeta["cliente"] == "Zeta"
    # 9001 (dentro da vigencia, com OS), 9006 (sem OS) e 9007 (depois do fim
    # da vigencia) devem TODOS religar agora -- as duas exigencias que
    # bloqueavam 9006/9007 antes foram removidas por pedido explicito.
    ids_parcelas_zeta = {p["nCodTitulo"] for p in zeta["parcelas"]}
    assert ids_parcelas_zeta == {9001, 9006, 9007}, (
        f"bug: 9001, 9006 (sem OS) e 9007 (depois do fim da vigencia) deviam religar por heuristica "
        f"(obtido {ids_parcelas_zeta})"
    )
    assert all(p["vinculo"] == "heuristico" for p in zeta["parcelas"])
    assert zeta["origem_status"] == "parcelas"
    print("OK: titulo orfao com cliente/valor/data batendo com 1 contrato -> religado (vinculo=heuristico)")
    print("OK: titulo sem Ordem de Servico agora religa normalmente (exigencia de nCodOS removida)")
    print("OK: titulo com vencimento depois do fim da vigencia agora religa normalmente (teto de vigencia removido)")

    # 9002 (20% de diferenca de valor) e 9005 (66.7%, alem do teto de
    # revisao) continuam de fora -- a unica coisa que os exclui agora e
    # valor, nao mais vigencia/OS. Contratos no relatorio continua em 3
    # (Alfa + Epsilon do cadastro + Zeta) -- religar mais orfaos num
    # contrato que ja existe nao cria grupo novo.
    assert len(contratos) == 3, f"orfaos implausiveis nao deveriam criar/entrar em nenhum grupo (contratos={len(contratos)})"
    assert 9002 not in ids_parcelas_zeta and 9005 not in ids_parcelas_zeta, (
        "bug: titulo com valor incompativel nao pode ser religado, mesmo com as outras exigencias removidas"
    )
    print("OK: titulo com valor fora da tolerancia continua nao religado (unico criterio que restou, alem de natureza/vigencia-inicial)")

    revisao_por_id = {r["nCodTitulo"]: r for r in zeta["titulos_para_revisao"]}
    assert set(revisao_por_id) == {9002}, (
        f"bug: so o titulo 9002 (20% de diferenca, dentro do teto de revisao) devia virar sinal de revisao manual "
        f"agora -- 9006/9007 religam direto, e 9005 (66.7%, alem do teto) nao devia aparecer (obtido {set(revisao_por_id)})"
    )
    motivo_9002 = revisao_por_id[9002]["motivo"]
    assert "valor" in motivo_9002 and "vigência" not in motivo_9002, (
        f"bug: motivo do 9002 devia explicar so o valor incompativel -- vigencia nao e mais criterio, "
        f"nao devia aparecer no motivo (obtido {motivo_9002!r})"
    )
    print("OK: titulo dentro do teto de plausibilidade vira sinal de titulos_para_revisao, com motivo explicando so o valor (vigencia nao e mais criterio)")
    print("OK: titulo MUITO diferente do valor do contrato (alem do teto de revisao) nao aparece nem religado nem em revisao")

    assert 9003 not in ids_parcelas_zeta, "bug: titulo orfao CANCELADO nao pode ser religado por heuristica"
    assert 9003 not in revisao_por_id, "bug: titulo orfao CANCELADO nao pode virar sinal de revisao (nao ajuda o usuario a decidir nada)"
    print("OK: titulo orfao CANCELADO fica fora tanto da religacao heuristica quanto do sinal de revisao")

    alfa = por_ctr[1]
    assert alfa["parcelas"][0]["vinculo"] == "confirmado"
    assert alfa["titulos_para_revisao"] == []
    print("OK: titulo com nCodCtr direto continua marcado como vinculo=confirmado")


def _testar_substituto_cancelado() -> None:
    """Casos novos: parcela ja vinculada que aparece CANCELADO ganha um
    sinal de substituto quando ha um lancamento manual plausivel do mesmo
    cliente por perto (ver _buscar_substituto_cancelado)."""
    cliente_map = {
        902: {"razao_social": "Cliente Theta Ltda", "nome_fantasia": "Theta", "cnpj_cpf": ""},
        903: {"razao_social": "Cliente Iota Ltda", "nome_fantasia": "Iota", "cnpj_cpf": ""},
    }
    contratos_cadastro = {
        20: {
            "nCodCtr": 20, "cNumCtr": "2026/00020", "nCodCli": 902,
            "cCodSit": "10", "status_cadastro": "Ativo",
            "dVigInicial": "01/01/2026", "dVigFinal": "31/12/2026",
            "nDiaFat": 20, "nValTotMes": 5000.0,
        },
        21: {
            "nCodCtr": 21, "cNumCtr": "2026/00021", "nCodCli": 903,
            "cCodSit": "99", "status_cadastro": "Cancelado",
            "dVigInicial": "01/01/2025", "dVigFinal": "01/01/2026",
            "nDiaFat": 15, "nValTotMes": 2000.0,
        },
    }

    impostos_padrao = {
        "nValorPIS": 32.5, "cRetPIS": "S",
        "nValorCOFINS": 150.0, "cRetCOFINS": "S",
        "nValorCSLL": 50.0, "cRetCSLL": "S",
        "nValorIR": 75.0, "cRetIR": "S",
    }

    titulos_raw = [
        # A) match exato + ja pago + mesma categoria + mesmos impostos ->
        # confianca alta o bastante pra PROMOVER como parcela de verdade
        # (vinculo="substituto"), nao so sinalizar.
        _titulo(30001, 20, 902, "CANCELADO", "20/03/2026", valor=5000.0, emissao="15/03/2026", impostos=impostos_padrao),
        _titulo(30002, None, 902, "RECEBIDO", "22/03/2026", valor=5000.0, c_origem="MANR", n_cod_os=None, emissao="20/03/2026", impostos=impostos_padrao),
        # decoy VENR (sem OS, entao nao casa por heuristico) com valor
        # identico e mesma janela -- nao pode ser escolhido como substituto
        # (regra exige lancamento manual).
        _titulo(30003, None, 902, "RECEBIDO", "22/03/2026", valor=5000.0, c_origem="VENR", n_cod_os=None, emissao="20/03/2026"),

        # A2) match exato + ja pago + impostos batendo, mas CATEGORIA
        # diferente -> nao promove (fica so como sinal de revisao). Valor
        # 4500 (diferente de 5000) de proposito, pra nao colidir com a
        # janela de 30 dias dos candidatos de D (tambem 5000).
        _titulo(30013, 20, 902, "CANCELADO", "20/07/2026", valor=4500.0, emissao="15/07/2026", impostos=impostos_padrao),
        _titulo(30014, None, 902, "RECEBIDO", "18/07/2026", valor=4500.0, c_origem="MANR", n_cod_os=None, emissao="17/07/2026", categoria="2.01.98", impostos=impostos_padrao),

        # A3) match exato + mesma categoria/impostos, mas AINDA NAO PAGO
        # (A VENCER) -> nao promove (fica so como sinal de revisao). Valor
        # 4200 (diferente de 5000/4500) pelo mesmo motivo.
        _titulo(30015, 20, 902, "CANCELADO", "20/08/2026", valor=4200.0, emissao="15/08/2026", impostos=impostos_padrao),
        _titulo(30016, None, 902, "A VENCER", "25/08/2026", valor=4200.0, c_origem="MANR", n_cod_os=None, emissao="16/08/2026", impostos=impostos_padrao),

        # A4) regressao do caso real ADSMAIS: cancelada tem cRetISS AUSENTE
        # (nunca preenchido) e substituto tem cRetISS="N" explicito, com um
        # nValorISS "de referencia" preenchido (750) mesmo assim -- os dois
        # dizem "sem ISS retido", so representam isso de formas diferentes.
        # Comparar o valor bruto sem checar o flag bloquearia a promocao por
        # engano (foi exatamente o bug reportado). Com o flag, promove.
        _titulo(30019, 20, 902, "CANCELADO", "20/09/2026", valor=3900.0, emissao="15/09/2026", impostos=impostos_padrao),
        _titulo(
            30020, None, 902, "RECEBIDO", "22/09/2026", valor=3900.0, c_origem="MANR", n_cod_os=None,
            emissao="20/09/2026", impostos={**impostos_padrao, "cRetISS": "N", "nValorISS": 750.0},
        ),

        # A5) controle negativo: os dois lados tem ISS de fato retido
        # (cRetISS="S"), mas com valores DIFERENTES -> continua bloqueando a
        # promocao (o fix nao pode fazer a checagem de imposto virar um
        # no-op geral).
        _titulo(
            30021, 20, 902, "CANCELADO", "20/10/2026", valor=3600.0, emissao="15/10/2026",
            impostos={**impostos_padrao, "cRetISS": "S", "nValorISS": 100.0},
        ),
        _titulo(
            30022, None, 902, "RECEBIDO", "22/10/2026", valor=3600.0, c_origem="MANR", n_cod_os=None,
            emissao="20/10/2026", impostos={**impostos_padrao, "cRetISS": "S", "nValorISS": 200.0},
        ),

        # B) sem valor exato -> cai na faixa [0.5x, 3x] = [2500, 15000]. Como
        # nao veio de match exato, nunca promove, so sinaliza (mesmo com
        # status pago e categoria/impostos batendo).
        _titulo(30004, 20, 902, "CANCELADO", "20/04/2026", valor=5000.0, emissao="15/04/2026"),
        _titulo(30005, None, 902, "RECEBIDO", "22/04/2026", valor=5300.0, c_origem="MANR", n_cod_os=None, emissao="18/04/2026"),

        # C) candidato exato existe, mas fora da janela de dias (emissao
        # muito distante) -> nao casa como substituto; cai no sinal de
        # revisao GENERICO em vez de ficar marcado como substituto. Valor
        # 5400 (8% diferente de nValTotMes=5000) de proposito -- exato
        # bateria tambem em _match_heuristico (que nao exige mais OS/teto de
        # vigencia) e religaria direto, o que desviaria do que este cenario
        # quer isolar (rejeicao pela janela de dias).
        _titulo(30006, 20, 902, "CANCELADO", "20/05/2026", valor=5000.0, emissao="15/05/2026"),
        _titulo(30007, None, 902, "RECEBIDO", "01/09/2026", valor=5400.0, c_origem="MANR", n_cod_os=None, emissao="01/09/2026"),

        # D) dois candidatos igualmente plausiveis (mesmo valor exato, ambos
        # dentro da janela) -> ambiguo, nenhum e escolhido.
        _titulo(30008, 20, 902, "CANCELADO", "20/06/2026", valor=5000.0, emissao="15/06/2026"),
        _titulo(30009, None, 902, "RECEBIDO", "21/06/2026", valor=5000.0, c_origem="MANR", n_cod_os=None, emissao="16/06/2026"),
        _titulo(30010, None, 902, "RECEBIDO", "22/06/2026", valor=5000.0, c_origem="MANR", n_cod_os=None, emissao="17/06/2026"),

        # E) contrato Iota ja tinha dVigFinal (01/01/2026) ANTES do
        # vencimento da parcela cancelada (15/01/2026) -> nao procura
        # substituto, mesmo com candidato exato disponivel.
        _titulo(30011, 21, 903, "CANCELADO", "15/01/2026", valor=2000.0, emissao="10/01/2026"),
        _titulo(30012, None, 903, "RECEBIDO", "16/01/2026", valor=2000.0, c_origem="MANR", n_cod_os=None, emissao="11/01/2026"),
    ]

    contratos = ctr_mod.montar_contratos(
        titulos_raw, CATEGORIA_MAP, CC_MAP, cliente_map, contratos_cadastro, hoje=date(2026, 8, 6)
    )
    theta = next(c for c in contratos if c["nCodCtr"] == 20)
    iota = next(c for c in contratos if c["nCodCtr"] == 21)

    parcelas_theta = {p["nCodTitulo"]: p for p in theta["parcelas"]}
    revisao_theta = {r["nCodTitulo"]: r for r in theta["titulos_para_revisao"]}

    # A: match exato + pago + categoria/impostos batendo -> PROMOVIDO como
    # parcela de verdade (nao so sinalizado).
    assert 30002 in parcelas_theta, "bug: substituto confiavel devia ter sido promovido pra dentro de 'parcelas'"
    assert parcelas_theta[30002]["vinculo"] == "substituto", (
        f"bug: parcela promovida devia ter vinculo='substituto' (obtido {parcelas_theta[30002]['vinculo']!r})"
    )
    assert parcelas_theta[30002]["situacao"] in ("Pago no prazo", "Pago com atraso"), (
        "bug: substituto promovido devia entrar com situacao de pago (RECEBIDO), nao outra coisa"
    )
    info_30001 = parcelas_theta[30001].get("substituto_sugerido")
    assert info_30001 and info_30001["nCodTitulo"] == 30002 and info_30001["promovido"] is True, (
        f"bug: parcela cancelada devia referenciar o substituto promovido (obtido {info_30001})"
    )
    assert 30002 not in revisao_theta, "bug: substituto ja promovido nao deveria tambem aparecer em titulos_para_revisao"
    # 30003 (decoy VENR, sem OS) ainda pode aparecer em titulos_para_revisao
    # pelo passo GENERICO (mesmo cliente, valor plausivel) -- o que nao pode
    # e ter sido ESCOLHIDO como substituto de 30001.
    if 30003 in revisao_theta:
        assert "30001" not in revisao_theta[30003]["motivo"], (
            "bug: decoy VENR nao pode ser marcado como substituto da parcela cancelada (regra exige lancamento manual)"
        )
    print("OK: substituto exato + pago + categoria/impostos batendo -> promovido como parcela paga de verdade")

    # A2: match exato + pago + impostos batendo, mas CATEGORIA diferente ->
    # NAO promove, fica so como sinal de revisao.
    assert 30014 not in parcelas_theta, "bug: substituto com categoria diferente da parcela cancelada nao pode ser promovido"
    info_30013 = parcelas_theta[30013].get("substituto_sugerido")
    assert info_30013 and info_30013["nCodTitulo"] == 30014 and info_30013["promovido"] is False, (
        f"bug: substituto com categoria incompativel devia ficar so como sinal (nao promovido) (obtido {info_30013})"
    )
    assert 30014 in revisao_theta, "bug: substituto nao promovido ainda devia aparecer em titulos_para_revisao"
    print("OK: match exato com categoria diferente da parcela cancelada nao e promovido, so sinalizado")

    # A3: match exato + categoria/impostos batendo, mas AINDA NAO PAGO (A
    # VENCER) -> NAO promove, fica so como sinal de revisao.
    assert 30016 not in parcelas_theta, "bug: substituto ainda nao pago (A VENCER) nao pode ser promovido como parcela paga"
    info_30015 = parcelas_theta[30015].get("substituto_sugerido")
    assert info_30015 and info_30015["nCodTitulo"] == 30016 and info_30015["promovido"] is False, (
        f"bug: substituto ainda nao pago devia ficar so como sinal (nao promovido) (obtido {info_30015})"
    )
    print("OK: match exato mas substituto ainda nao pago (A VENCER) nao e promovido, so sinalizado")

    # A4: regressao do caso real ADSMAIS -- cRetISS ausente (cancelada) e
    # cRetISS="N" (substituto) sao a MESMA informacao, mesmo com nValorISS
    # bruto diferente (0 vs 750) -- deve promover.
    assert 30020 in parcelas_theta, (
        "bug: cRetISS ausente e cRetISS='N' devem contar como a mesma informacao (nao retido) -- "
        "substituto deveria ter sido promovido"
    )
    assert parcelas_theta[30020]["vinculo"] == "substituto"
    print("OK: cRetISS ausente x cRetISS='N' contam como a mesma informacao -> promove (regressao ADSMAIS)")

    # A5: controle negativo -- os dois lados com ISS de fato retido
    # (cRetISS='S'), mas valores diferentes -> continua bloqueando.
    assert 30022 not in parcelas_theta, "bug: ISS realmente retido com valores diferentes nao pode ser promovido"
    info_30021 = parcelas_theta[30021].get("substituto_sugerido")
    assert info_30021 and info_30021["promovido"] is False, (
        "bug: ISS retido (cRetISS='S') com valores diferentes devia continuar bloqueando a promocao"
    )
    print("OK: ISS efetivamente retido (cRetISS='S') com valores diferentes continua bloqueando a promocao")

    # B: sem valor exato (faixa 0.5x-3x) -> NUNCA promove, mesmo com status
    # pago -- so o match exato pode ser promovido.
    assert 30005 not in parcelas_theta, "bug: substituto achado so pela faixa (sem valor exato) nao pode ser promovido"
    info_30004 = parcelas_theta[30004].get("substituto_sugerido")
    assert info_30004 and info_30004["nCodTitulo"] == 30005 and info_30004["promovido"] is False, (
        "bug: substituto na faixa 0.5x-3x (sem match exato) devia ficar so como sinal, nunca promovido"
    )
    print("OK: sem match exato, substituto dentro da faixa 0.5x-3x do valor e sinalizado mas nunca promovido")

    # C
    assert "substituto_sugerido" not in parcelas_theta[30006], (
        "bug: candidato fora da janela de dias nao pode virar substituto"
    )
    assert 30007 in revisao_theta, "bug: candidato fora da janela devia cair no sinal de revisao generico"
    assert "30006" not in revisao_theta[30007]["motivo"], (
        "bug: candidato fora da janela nao pode ter motivo de substituto especifico (deve ser o motivo generico)"
    )
    print("OK: candidato fora da janela de dias nao vira substituto, mas ainda cai no sinal de revisao generico")

    # D
    assert "substituto_sugerido" not in parcelas_theta[30008], (
        "bug: com 2 candidatos igualmente plausiveis, nenhum deveria ser escolhido como substituto (ambiguo)"
    )
    print("OK: 2 candidatos igualmente plausiveis -> ambiguo, nenhum e escolhido como substituto")

    # E
    parcelas_iota = {p["nCodTitulo"]: p for p in iota["parcelas"]}
    assert "substituto_sugerido" not in parcelas_iota[30011], (
        "bug: nao pode procurar substituto quando o contrato ja tinha encerrado (dVigFinal) antes do vencimento"
    )
    print("OK: nao procura substituto quando o contrato ja tinha encerrado antes do vencimento da parcela cancelada")


def _testar_pagamento_atrasado() -> None:
    """Casos novos: título ATRASADO há mais de 60 dias ganha um sinal de
    possível pagamento avulso quando há um lançamento do mesmo cliente,
    lançado depois do vencimento, cujo valor bate com o LÍQUIDO do atrasado
    (ver _buscar_pagamento_atrasado)."""
    cliente_map = {904: {"razao_social": "Cliente Kappa Ltda", "nome_fantasia": "Kappa", "cnpj_cpf": ""}}
    categoria_map_local = {
        "1.01.99": {"descricao": "Retainer Fee", "categoria_superior": "", "nao_exibir": "N", "transferencia": "N", "totalizadora": "N"},
        "9.99.99": {"descricao": "Transferências", "categoria_superior": "", "nao_exibir": "N", "transferencia": "S", "totalizadora": "N"},
    }
    contratos_cadastro = {
        30: {
            "nCodCtr": 30, "cNumCtr": "2026/00030", "nCodCli": 904,
            "cCodSit": "10", "status_cadastro": "Ativo",
            "dVigInicial": "01/01/2025", "dVigFinal": "31/12/2026",
            "nDiaFat": 1, "nValTotMes": 6000.0,
        },
    }

    # Cada cenario usa um valor DISTINTO -- os titulos "atrasado" de A/C/D/E/F
    # todos vencem em 01/01/2026 e tem candidatos lancados no mesmo mes, entao
    # sem valores distintos o pool de candidatos de um cenario vazaria pro
    # calculo de ambiguidade dos outros (todos sao do mesmo cliente).
    titulos_raw = [
        # A) atrasado ha 217 dias (bem alem dos 60), candidato lancado
        # depois do vencimento, valor bate com o liquido (sem retencao
        # aqui, liquido = bruto) -> deve sinalizar, categoria elegivel DRE.
        _titulo(40001, 30, 904, "ATRASADO", "01/01/2026", valor=6000.0),
        _titulo(40002, None, 904, "RECEBIDO", "15/01/2026", valor=6000.0, c_origem="MANR", n_cod_os=None, emissao="15/01/2026"),

        # B) atrasado ha so 36 dias (<=60) -> nao busca nada, mesmo com
        # candidato perfeito disponivel.
        _titulo(40003, 30, 904, "ATRASADO", "01/07/2026", valor=6100.0),
        _titulo(40004, None, 904, "RECEBIDO", "10/07/2026", valor=6100.0, c_origem="MANR", n_cod_os=None, emissao="10/07/2026"),

        # C) atrasado ha 217 dias, mas candidato foi lancado ANTES do
        # vencimento -> nao pode casar (regra exige "lancado depois").
        _titulo(40005, 30, 904, "ATRASADO", "01/01/2026", valor=6200.0),
        _titulo(40006, None, 904, "RECEBIDO", "20/12/2025", valor=6200.0, c_origem="MANR", n_cod_os=None, emissao="20/12/2025"),

        # D) atrasado ha 217 dias, com retencao REAL (PIS 39 + COFINS 180,
        # liquido = 6300 - 219 = 6081) -> candidato bate no LIQUIDO, nao no
        # bruto -> deve sinalizar (confirma o calculo de valor liquido).
        _titulo(
            40007, 30, 904, "ATRASADO", "01/01/2026", valor=6300.0,
            impostos={"nValorPIS": 39.0, "cRetPIS": "S", "nValorCOFINS": 180.0, "cRetCOFINS": "S"},
        ),
        _titulo(40008, None, 904, "RECEBIDO", "10/01/2026", valor=6081.0, c_origem="MANR", n_cod_os=None, emissao="10/01/2026"),

        # E) atrasado ha 217 dias, categoria de TRANSFERENCIA (nao elegivel
        # pro DRE) -> deve sinalizar, motivo dizendo que NAO e destinada ao DRE.
        _titulo(40009, 30, 904, "ATRASADO", "01/01/2026", valor=6400.0, categoria="9.99.99"),
        _titulo(40010, None, 904, "RECEBIDO", "10/01/2026", valor=6400.0, c_origem="MANR", n_cod_os=None, emissao="10/01/2026"),

        # F) atrasado ha 217 dias, mas 2 candidatos igualmente plausiveis
        # (mesmo valor, ambos lancados depois) -> ambiguo, nenhum e escolhido.
        _titulo(40011, 30, 904, "ATRASADO", "01/01/2026", valor=6500.0),
        _titulo(40012, None, 904, "RECEBIDO", "05/01/2026", valor=6500.0, c_origem="MANR", n_cod_os=None, emissao="05/01/2026"),
        _titulo(40013, None, 904, "RECEBIDO", "10/01/2026", valor=6500.0, c_origem="MANR", n_cod_os=None, emissao="10/01/2026"),
    ]

    contratos = ctr_mod.montar_contratos(
        titulos_raw, categoria_map_local, CC_MAP, cliente_map, contratos_cadastro, hoje=date(2026, 8, 6)
    )
    kappa = next(c for c in contratos if c["nCodCtr"] == 30)
    revisao_kappa = {r["nCodTitulo"]: r for r in kappa["titulos_para_revisao"]}
    parcelas_kappa = {p["nCodTitulo"]: p for p in kappa["parcelas"]}

    # A
    assert 40002 in revisao_kappa, "bug: pagamento avulso apos vencimento com valor liquido batendo devia ser sinalizado"
    motivo_a = revisao_kappa[40002]["motivo"]
    assert "40001" in motivo_a and "60 dias" in motivo_a and "é destinada ao DRE" in motivo_a, (
        f"bug: motivo devia citar o titulo atrasado, os 60 dias, e a categoria ser elegivel DRE (obtido {motivo_a!r})"
    )
    assert parcelas_kappa[40001]["vinculo"] == "confirmado", "bug: titulo atrasado nunca deve ser alterado/promovido por essa busca"
    print("OK: atrasado ha mais de 60 dias com pagamento avulso apos o vencimento -> sinalizado, categoria elegivel DRE")

    # B
    assert 40004 not in revisao_kappa or "40003" not in revisao_kappa.get(40004, {}).get("motivo", ""), (
        "bug: atrasado ha 36 dias (<=60) nao pode acionar a busca de pagamento avulso"
    )
    print("OK: atrasado ha <=60 dias nao aciona a busca de pagamento avulso")

    # C
    assert 40006 not in revisao_kappa or "40005" not in revisao_kappa.get(40006, {}).get("motivo", ""), (
        "bug: candidato lancado ANTES do vencimento do atrasado nao pode ser sugerido como pagamento"
    )
    print("OK: candidato lancado antes do vencimento do atrasado nao e sugerido como pagamento")

    # D
    assert 40008 in revisao_kappa, "bug: pagamento avulso batendo no valor LIQUIDO (descontada a retencao real) devia ser sinalizado"
    assert "40007" in revisao_kappa[40008]["motivo"]
    print("OK: valor liquido (bruto menos retencao efetiva) calculado corretamente pra achar o pagamento avulso")

    # E
    assert 40010 in revisao_kappa, "bug: pagamento avulso de titulo atrasado com categoria de transferencia devia ser sinalizado"
    motivo_e = revisao_kappa[40010]["motivo"]
    assert "não é destinada ao DRE" in motivo_e, (
        f"bug: motivo devia dizer que a categoria de transferencia NAO e destinada ao DRE (obtido {motivo_e!r})"
    )
    print("OK: categoria de transferencia (nao elegivel DRE) reportada corretamente no motivo")

    # F
    for cod in (40012, 40013):
        assert cod not in revisao_kappa or "40011" not in revisao_kappa.get(cod, {}).get("motivo", ""), (
            f"bug: com 2 candidatos igualmente plausiveis (titulo {cod}), nenhum deveria ser escolhido como pagamento avulso"
        )
    print("OK: 2 candidatos igualmente plausiveis -> ambiguo, nenhum e sugerido como pagamento avulso")


def _testar_pagamento_atrasado_melhorias() -> None:
    """Casos novos pra cobrir as melhorias de _buscar_pagamento_atrasado:
    faixa de valor como fallback (0.5x-3x, quando nao ha match exato),
    exigencia de cOrigem="MANR" (nota fiscal formal nao conta, mesmo com
    valor/data batendo), janela de dias mais larga (90, contra os 30 da
    heuristica irma de cancelamento), e anotacao de categoria/impostos
    batendo ou nao no motivo.

    Cada cenario usa cliente/contrato PROPRIO, com sua propria chamada a
    montar_contratos (em vez de dividir o Kappa de _testar_pagamento_atrasado
    entre varios cenarios) -- agora que existe fallback de faixa (0.5x-3x,
    bem larga), cenarios do mesmo cliente com valores so "distintos" podem
    ter faixas que se sobrepoem e um cenario acaba emprestando candidato pro
    outro. Isolar por cliente (e por chamada) elimina esse risco por completo.
    """
    hoje = date(2026, 8, 6)

    # G) sem valor exato -> cai na faixa 0.5x-3x do liquido (7000 * 1.5 =
    # 10500, dentro de [3500, 21000]) -> deve sinalizar com o motivo dizendo
    # que caiu na faixa, nao que bateu exato. Tambem confirma que o campo
    # "pagamento" do sinal reflete a data de pagamento real do candidato
    # (pedido do usuario: mostrar vencimento/pagamento/etc de cada titulo
    # em revisao).
    cliente_g = {905: {"razao_social": "Cliente Lambda Ltda", "nome_fantasia": "Lambda", "cnpj_cpf": ""}}
    cadastro_g = {40: {
        "nCodCtr": 40, "cNumCtr": "2026/00040", "nCodCli": 905, "cCodSit": "10", "status_cadastro": "Ativo",
        "dVigInicial": "01/01/2025", "dVigFinal": "31/12/2026", "nDiaFat": 1, "nValTotMes": 7000.0,
    }}
    titulos_g = [
        _titulo(50001, 40, 905, "ATRASADO", "01/01/2026", valor=7000.0),
        _titulo(50002, None, 905, "RECEBIDO", "20/01/2026", pagamento="22/01/2026", valor=10500.0, c_origem="MANR", n_cod_os=None, emissao="20/01/2026"),
    ]
    lambda_ctr = next(c for c in ctr_mod.montar_contratos(titulos_g, CATEGORIA_MAP, CC_MAP, cliente_g, cadastro_g, hoje=hoje) if c["nCodCtr"] == 40)
    revisao_g = {r["nCodTitulo"]: r for r in lambda_ctr["titulos_para_revisao"]}
    assert 50002 in revisao_g, "bug: candidato dentro da faixa 0.5x-3x do liquido devia ser sinalizado (fallback de faixa)"
    motivo_g = revisao_g[50002]["motivo"]
    assert "faixa" in motivo_g and "bate exatamente" not in motivo_g, (
        f"bug: motivo devia dizer que o valor caiu na faixa, nao que bateu exato (obtido {motivo_g!r})"
    )
    assert revisao_g[50002]["pagamento"] == "22/01/2026", (
        "bug: campo 'pagamento' do titulo em revisao devia refletir a data de pagamento real do candidato"
    )
    # pagamento (22/01) e depois do vencimento do proprio candidato (20/01) -> "Pago com atraso".
    assert revisao_g[50002]["situacao"] == "Pago com atraso"
    print("OK: sem valor exato, candidato dentro da faixa 0.5x-3x do liquido e sinalizado (fallback de faixa), com o campo 'pagamento' refletindo a data real")

    # H) candidato com valor EXATO e data batendo, mas cOrigem="VENR" (nota
    # fiscal formal, nao lancamento manual) -> nao pode ser escolhido como
    # pagamento avulso, mesmo com correspondencia perfeita -- exige MANR,
    # mesma exigencia das outras duas heuristicas. nValTotMes deliberadamente
    # longe do valor do candidato (6000 vs 7100) pra ele nao ser auto-ligado
    # por _match_heuristico antes de chegar na busca de pagamento avulso --
    # senao o teste passaria pelo motivo errado (titulo nem fica orfao).
    cliente_h = {906: {"razao_social": "Cliente Mu Ltda", "nome_fantasia": "Mu", "cnpj_cpf": ""}}
    cadastro_h = {41: {
        "nCodCtr": 41, "cNumCtr": "2026/00041", "nCodCli": 906, "cCodSit": "10", "status_cadastro": "Ativo",
        "dVigInicial": "01/01/2025", "dVigFinal": "31/12/2026", "nDiaFat": 1, "nValTotMes": 6000.0,
    }}
    titulos_h = [
        _titulo(50003, 41, 906, "ATRASADO", "01/01/2026", valor=7100.0),
        _titulo(50004, None, 906, "RECEBIDO", "20/01/2026", valor=7100.0, c_origem="VENR", emissao="20/01/2026"),
    ]
    mu_ctr = next(c for c in ctr_mod.montar_contratos(titulos_h, CATEGORIA_MAP, CC_MAP, cliente_h, cadastro_h, hoje=hoje) if c["nCodCtr"] == 41)
    revisao_h = {r["nCodTitulo"]: r for r in mu_ctr["titulos_para_revisao"]}
    assert 50004 not in revisao_h or "50003" not in revisao_h.get(50004, {}).get("motivo", ""), (
        "bug: candidato VENR (nota fiscal formal) nao pode ser sugerido como pagamento avulso, mesmo com valor/data batendo"
    )
    print("OK: candidato com valor e data batendo mas cOrigem=VENR nao e sugerido como pagamento avulso (exige MANR)")

    # I) candidato lancado 75 dias depois do vencimento -- dentro da janela
    # mais larga (_JANELA_DIAS_PAGAMENTO_ATRASADO=90), alem dos 30 dias da
    # heuristica irma de cancelamento -> deve sinalizar.
    cliente_i = {907: {"razao_social": "Cliente Nu Ltda", "nome_fantasia": "Nu", "cnpj_cpf": ""}}
    cadastro_i = {42: {
        "nCodCtr": 42, "cNumCtr": "2026/00042", "nCodCli": 907, "cCodSit": "10", "status_cadastro": "Ativo",
        "dVigInicial": "01/01/2025", "dVigFinal": "31/12/2026", "nDiaFat": 1, "nValTotMes": 7200.0,
    }}
    titulos_i = [
        _titulo(50005, 42, 907, "ATRASADO", "01/01/2026", valor=7200.0),
        _titulo(50006, None, 907, "RECEBIDO", "17/03/2026", valor=7200.0, c_origem="MANR", n_cod_os=None, emissao="17/03/2026"),
    ]
    nu_ctr = next(c for c in ctr_mod.montar_contratos(titulos_i, CATEGORIA_MAP, CC_MAP, cliente_i, cadastro_i, hoje=hoje) if c["nCodCtr"] == 42)
    revisao_i = {r["nCodTitulo"]: r for r in nu_ctr["titulos_para_revisao"]}
    assert 50006 in revisao_i, "bug: candidato lancado 75 dias depois do vencimento devia ser sinalizado (dentro da janela de 90 dias)"
    print("OK: candidato lancado 75 dias depois do vencimento (alem dos 30 da heuristica irma) e sinalizado -- janela mais larga funcionando")

    # I2) candidato lancado 104 dias depois do vencimento -- alem da janela
    # de 90 dias -> nao pode ser sugerido, mesmo com valor exato.
    cliente_i2 = {908: {"razao_social": "Cliente Xi Ltda", "nome_fantasia": "Xi", "cnpj_cpf": ""}}
    cadastro_i2 = {43: {
        "nCodCtr": 43, "cNumCtr": "2026/00043", "nCodCli": 908, "cCodSit": "10", "status_cadastro": "Ativo",
        "dVigInicial": "01/01/2025", "dVigFinal": "31/12/2026", "nDiaFat": 1, "nValTotMes": 7250.0,
    }}
    titulos_i2 = [
        _titulo(50007, 43, 908, "ATRASADO", "01/01/2026", valor=7250.0),
        _titulo(50008, None, 908, "RECEBIDO", "15/04/2026", valor=7250.0, c_origem="MANR", n_cod_os=None, emissao="15/04/2026"),
    ]
    xi_ctr = next(c for c in ctr_mod.montar_contratos(titulos_i2, CATEGORIA_MAP, CC_MAP, cliente_i2, cadastro_i2, hoje=hoje) if c["nCodCtr"] == 43)
    revisao_i2 = {r["nCodTitulo"]: r for r in xi_ctr["titulos_para_revisao"]}
    assert 50008 not in revisao_i2 or "50007" not in revisao_i2.get(50008, {}).get("motivo", ""), (
        "bug: candidato lancado 104 dias depois do vencimento (alem da janela de 90 dias) nao pode ser sugerido"
    )
    print("OK: candidato lancado 104 dias depois do vencimento (alem da janela de 90 dias) nao e sugerido")

    # J) candidato com valor exato mas CATEGORIA diferente do atrasado, sem
    # retencao de imposto de nenhum dos dois lados -> motivo deve dizer que
    # a categoria NAO bate e que os impostos retidos BATEM (ausencia de
    # retencao dos dois lados e a mesma informacao, nao uma incompatibilidade).
    cliente_j = {909: {"razao_social": "Cliente Omicron Ltda", "nome_fantasia": "Omicron", "cnpj_cpf": ""}}
    cadastro_j = {44: {
        "nCodCtr": 44, "cNumCtr": "2026/00044", "nCodCli": 909, "cCodSit": "10", "status_cadastro": "Ativo",
        "dVigInicial": "01/01/2025", "dVigFinal": "31/12/2026", "nDiaFat": 1, "nValTotMes": 7300.0,
    }}
    categoria_map_j = {
        "1.01.99": {"descricao": "Retainer Fee", "categoria_superior": "", "nao_exibir": "N", "transferencia": "N", "totalizadora": "N"},
        "9.99.99": {"descricao": "Transferências", "categoria_superior": "", "nao_exibir": "N", "transferencia": "S", "totalizadora": "N"},
    }
    titulos_j = [
        _titulo(50009, 44, 909, "ATRASADO", "01/01/2026", valor=7300.0, categoria="1.01.99"),
        _titulo(50010, None, 909, "RECEBIDO", "20/01/2026", valor=7300.0, c_origem="MANR", n_cod_os=None, emissao="20/01/2026", categoria="9.99.99"),
    ]
    omicron_ctr = next(c for c in ctr_mod.montar_contratos(titulos_j, categoria_map_j, CC_MAP, cliente_j, cadastro_j, hoje=hoje) if c["nCodCtr"] == 44)
    revisao_j = {r["nCodTitulo"]: r for r in omicron_ctr["titulos_para_revisao"]}
    assert 50010 in revisao_j, "bug: candidato com valor exato devia ser sinalizado mesmo com categoria diferente"
    motivo_j = revisao_j[50010]["motivo"]
    assert "categoria não bate" in motivo_j, f"bug: motivo devia dizer que a categoria nao bate (obtido {motivo_j!r})"
    assert "impostos retidos batem" in motivo_j, (
        f"bug: motivo devia dizer que os impostos batem (ausencia de retencao dos dois lados e a mesma informacao) (obtido {motivo_j!r})"
    )
    print("OK: motivo anota corretamente categoria (nao bate) e impostos retidos (batem) do candidato vs. do atrasado")


def _testar_valor_referencia_contrato() -> None:
    """Casos novos: o valor de referencia usado por _match_heuristico e
    _candidatos_revisao/_revisao_motivo passa a vir das parcelas CONFIRMADAS
    mais proximas em data do proprio contrato (media das ate 3 mais
    proximas), em vez do nValTotMes estatico do cadastro -- acompanha
    reajuste sem precisar de um classificador (ver _valor_referencia_contrato).
    """
    hoje = date(2026, 8, 6)

    # A/B/C: contrato com reajuste real -- 3 parcelas confirmadas a R$5.000
    # (jan-mar/2026, antes do reajuste) e 3 a R$5.500 (abr-jun/2026, depois).
    # nValTotMes fica parado em 5000 (cadastro nunca atualizado).
    cliente_pi = {910: {"razao_social": "Cliente Pi Ltda", "nome_fantasia": "Pi", "cnpj_cpf": ""}}
    cadastro_pi = {50: {
        "nCodCtr": 50, "cNumCtr": "2026/00050", "nCodCli": 910, "cCodSit": "10", "status_cadastro": "Ativo",
        "dVigInicial": "01/01/2026", "dVigFinal": "31/12/2026", "nDiaFat": 30, "nValTotMes": 5000.0,
    }}
    titulos_pi = [
        _titulo(60001, 50, 910, "RECEBIDO", "30/01/2026", "30/01/2026", valor=5000.0),
        _titulo(60002, 50, 910, "RECEBIDO", "28/02/2026", "28/02/2026", valor=5000.0),
        _titulo(60003, 50, 910, "RECEBIDO", "30/03/2026", "30/03/2026", valor=5000.0),
        _titulo(60004, 50, 910, "RECEBIDO", "30/04/2026", "30/04/2026", valor=5500.0),
        _titulo(60005, 50, 910, "RECEBIDO", "30/05/2026", "30/05/2026", valor=5500.0),
        _titulo(60006, 50, 910, "RECEBIDO", "30/06/2026", "30/06/2026", valor=5500.0),
        # A) orfao do valor NOVO (5500), perto do cluster pos-reajuste -> deve
        # religar via heuristico (vizinhas = as 3 de 5500, 0% de diferenca).
        # Pelo nValTotMes antigo (5000) ficaria a 10% -- fora da tolerancia.
        _titulo(60007, None, 910, "RECEBIDO", "15/07/2026", valor=5500.0, c_origem="MANR", n_cod_os=None),
        # B) orfao do valor ANTIGO (5000), perto do cluster pre-reajuste ->
        # tambem religa via heuristico -- prova que a vizinhanca e por DATA,
        # nao uma media geral do contrato.
        _titulo(60008, None, 910, "RECEBIDO", "15/02/2026", valor=5000.0, c_origem="MANR", n_cod_os=None),
        # C) orfao com valor bem diferente (4200), perto do cluster
        # pos-reajuste -> nao religa (23.6% de diferenca da referencia 5500),
        # mas cai em titulos_para_revisao com o percentual calculado sobre a
        # referencia NOVA -- pelo nValTotMes antigo (5000) daria 16.0%,
        # numero diferente, prova que o motivo usa a referencia nova.
        _titulo(60009, None, 910, "RECEBIDO", "20/07/2026", valor=4200.0, c_origem="MANR", n_cod_os=None),
    ]
    contratos_pi = ctr_mod.montar_contratos(titulos_pi, CATEGORIA_MAP, CC_MAP, cliente_pi, cadastro_pi, hoje=hoje)
    pi = next(c for c in contratos_pi if c["nCodCtr"] == 50)
    parcelas_pi = {p["nCodTitulo"]: p for p in pi["parcelas"]}
    revisao_pi = {r["nCodTitulo"]: r for r in pi["titulos_para_revisao"]}

    assert 60007 in parcelas_pi and parcelas_pi[60007]["vinculo"] == "heuristico", (
        "bug: orfao com o valor NOVO (pos-reajuste) devia religar via heuristico usando a vizinhanca, "
        "nao o nValTotMes desatualizado"
    )
    print("OK: orfao com valor pos-reajuste religa via heuristico usando as parcelas confirmadas vizinhas (nao o nValTotMes desatualizado)")

    assert 60008 in parcelas_pi and parcelas_pi[60008]["vinculo"] == "heuristico", (
        "bug: orfao com o valor ANTIGO (pre-reajuste), perto do cluster antigo, devia religar via heuristico"
    )
    print("OK: orfao com valor pre-reajuste, perto do cluster antigo, tambem religa -- vizinhanca e por data, nao media geral do contrato")

    assert 60009 in revisao_pi, "bug: orfao com valor bem diferente da vizinhanca devia cair em titulos_para_revisao"
    motivo_60009 = revisao_pi[60009]["motivo"]
    assert "23.6%" in motivo_60009, (
        f"bug: motivo devia citar a diferenca calculada sobre a referencia NOVA (23.6%, vizinhanca=5500), "
        f"nao sobre nValTotMes=5000 (que daria 16.0%) (obtido {motivo_60009!r})"
    )
    print("OK: percentual no motivo de revisao usa o valor de referencia por vizinhanca, nao o nValTotMes estatico")

    # D) contrato sem NENHUMA parcela confirmada -> cai no fallback pro
    # nValTotMes (regressao: mesmo comportamento de antes da mudanca).
    cliente_rho = {911: {"razao_social": "Cliente Rho Ltda", "nome_fantasia": "Rho", "cnpj_cpf": ""}}
    cadastro_rho = {51: {
        "nCodCtr": 51, "cNumCtr": "2026/00051", "nCodCli": 911, "cCodSit": "10", "status_cadastro": "Ativo",
        "dVigInicial": "01/01/2026", "dVigFinal": "31/12/2026", "nDiaFat": 10, "nValTotMes": 8000.0,
    }}
    titulos_rho = [
        _titulo(60010, None, 911, "RECEBIDO", "10/03/2026", valor=8000.0, c_origem="MANR", n_cod_os=None),
        _titulo(60011, None, 911, "RECEBIDO", "10/04/2026", valor=8500.0, c_origem="MANR", n_cod_os=None),
    ]
    contratos_rho = ctr_mod.montar_contratos(titulos_rho, CATEGORIA_MAP, CC_MAP, cliente_rho, cadastro_rho, hoje=hoje)
    rho = next(c for c in contratos_rho if c["nCodCtr"] == 51)
    parcelas_rho = {p["nCodTitulo"]: p for p in rho["parcelas"]}
    revisao_rho = {r["nCodTitulo"]: r for r in rho["titulos_para_revisao"]}

    assert 60010 in parcelas_rho and parcelas_rho[60010]["vinculo"] == "heuristico", (
        "bug: sem nenhuma parcela confirmada, orfao com valor exato ao nValTotMes devia religar (fallback)"
    )
    assert 60011 in revisao_rho, (
        "bug: sem nenhuma parcela confirmada, orfao 6.25% diferente do nValTotMes devia cair em revisao (fallback)"
    )
    print("OK: contrato sem nenhuma parcela confirmada cai no fallback pro nValTotMes (regressao ok)")

    # E) contrato com UMA UNICA parcela confirmada, que e justamente o
    # titulo ATRASADO sendo avaliado por _resolver_pendencias -> salvaguarda
    # de minimo 2 parcelas evita colapso (senao a "vizinhanca" seria o
    # proprio valor do atrasado, e o orfao VENR bateria por tautologia --
    # inclusive um que a busca de pagamento avulso ja rejeitaria por nao
    # ser MANR).
    cliente_sigma = {912: {"razao_social": "Cliente Sigma Ltda", "nome_fantasia": "Sigma", "cnpj_cpf": ""}}
    cadastro_sigma = {52: {
        "nCodCtr": 52, "cNumCtr": "2026/00052", "nCodCli": 912, "cCodSit": "10", "status_cadastro": "Ativo",
        "dVigInicial": "01/01/2025", "dVigFinal": "31/12/2026", "nDiaFat": 1, "nValTotMes": 6000.0,
    }}
    titulos_sigma = [
        _titulo(60012, 52, 912, "ATRASADO", "01/01/2026", valor=7000.0),
        _titulo(60013, None, 912, "RECEBIDO", "20/01/2026", valor=7000.0, c_origem="VENR", emissao="20/01/2026"),
    ]
    contratos_sigma = ctr_mod.montar_contratos(titulos_sigma, CATEGORIA_MAP, CC_MAP, cliente_sigma, cadastro_sigma, hoje=hoje)
    sigma = next(c for c in contratos_sigma if c["nCodCtr"] == 52)
    parcelas_sigma_ids = {p["nCodTitulo"] for p in sigma["parcelas"]}
    assert 60013 not in parcelas_sigma_ids, (
        "bug: com so 1 parcela confirmada (a propria atrasada sendo avaliada), a vizinhanca nao pode colapsar "
        "no valor dela mesma -- orfao do mesmo valor nao pode religar por tautologia (salvaguarda de minimo 2)"
    )
    print("OK: contrato com 1 unica parcela confirmada (a propria atrasada) nao colapsa -- cai no fallback nValTotMes, sem religar por tautologia")


if __name__ == "__main__":
    main()
