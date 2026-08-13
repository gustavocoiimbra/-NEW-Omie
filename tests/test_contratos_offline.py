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

    # Orfao com valor E data batendo perfeitamente com o contrato 11, mas
    # SEM Ordem de Servico (lancamento manual) -> nao pode ser auto-ligado
    # mesmo com correspondencia perfeita de valor -- so entra como sinal de
    # revisao. Achado real: titulo do FISERV com valor identico ao contrato
    # e dentro da vigencia, mas MANR sem OS, enquanto o resto da cobranca do
    # contrato seguiu faturado normalmente pela via formal sem nunca
    # referenciar esse titulo -- ver PESQUISA_CONTRATOS_OS.md, secao 12.
    titulo_orfao_zeta_sem_os = _titulo(9006, None, 901, "ATRASADO", "10/04/2026", valor=3000.0, n_cod_os=None)

    titulos_raw = [
        titulo_alfa, titulo_orfao_zeta, titulo_orfao_zeta_revisao,
        titulo_orfao_zeta_muito_diferente, titulo_orfao_zeta_cancelado, titulo_orfao_zeta_sem_os,
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
    assert len(zeta["parcelas"]) == 1, "bug: titulo orfao plausivel deveria ter sido religado por heuristica"
    assert zeta["parcelas"][0]["nCodTitulo"] == 9001
    assert zeta["parcelas"][0]["vinculo"] == "heuristico"
    assert zeta["origem_status"] == "parcelas"
    print("OK: titulo orfao com cliente/valor/data batendo com 1 contrato -> religado (vinculo=heuristico)")

    # Nenhum dos orfaos implausiveis (nem o sem OS) pode ter sido religado ->
    # nao deve existir grupo extra e o total de contratos fica so 1 (Alfa) +
    # os 2 do cadastro (Epsilon, Zeta).
    assert len(contratos) == 3, f"orfaos implausiveis nao deveriam criar/entrar em nenhum grupo (contratos={len(contratos)})"
    ids_parcelas_zeta = {p["nCodTitulo"] for p in zeta["parcelas"]}
    assert 9002 not in ids_parcelas_zeta and 9005 not in ids_parcelas_zeta and 9006 not in ids_parcelas_zeta, (
        "bug: titulo fora da vigencia/valor incompativel/sem OS nao pode ser religado"
    )
    print("OK: titulos orfaos implausiveis (fora da vigencia, valor incompativel) permanecem nao religados")
    print("OK: titulo sem Ordem de Servico nao e auto-ligado mesmo com valor e data batendo perfeitamente")

    revisao_por_id = {r["nCodTitulo"]: r for r in zeta["titulos_para_revisao"]}
    assert set(revisao_por_id) == {9002, 9006}, (
        f"bug: titulo 9002 (20% de diferenca, dentro do teto de revisao) e 9006 (sem OS) deviam virar sinal de "
        f"revisao manual, e titulo 9005 (66.7%, alem do teto) NAO devia aparecer (obtido {set(revisao_por_id)})"
    )
    motivo_9002 = revisao_por_id[9002]["motivo"]
    assert "vigência" in motivo_9002 and "valor" in motivo_9002, (
        f"motivo do 9002 devia explicar data E valor incompativeis (obtido {motivo_9002!r})"
    )
    motivo_9006 = revisao_por_id[9006]["motivo"]
    assert "Ordem de Serviço" in motivo_9006, f"motivo do 9006 devia explicar a falta de OS (obtido {motivo_9006!r})"
    print("OK: titulo dentro do teto de plausibilidade vira sinal de titulos_para_revisao, com motivo explicando a incompatibilidade")
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
        # revisao GENERICO em vez de ficar marcado como substituto.
        _titulo(30006, 20, 902, "CANCELADO", "20/05/2026", valor=5000.0, emissao="15/05/2026"),
        _titulo(30007, None, 902, "RECEBIDO", "01/09/2026", valor=5000.0, c_origem="MANR", n_cod_os=None, emissao="01/09/2026"),

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


if __name__ == "__main__":
    main()
