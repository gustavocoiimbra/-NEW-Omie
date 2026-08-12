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


def _titulo(n_cod_titulo, n_cod_ctr, cod_cliente, status, venc, pagamento=None, valor=1000.0):
    return {
        "cabecTitulo": {
            "nCodTitulo": n_cod_titulo,
            "nCodCtr": n_cod_ctr,
            "cNumCtr": f"2026/{n_cod_ctr:05d}" if n_cod_ctr is not None else None,
            "nCodCliente": cod_cliente,
            "nCodCC": 1,
            "cCodCateg": "1.01.99",
            "cNatureza": "R",
            "cStatus": status,
            "dDtVenc": venc,
            "dDtPagamento": pagamento or "",
            "nValorTitulo": valor,
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
    # da vigencia -> deve casar (heuristico).
    titulo_orfao_zeta = _titulo(9001, None, 901, "ATRASADO", "10/03/2026", valor=3000.0)

    # Orfao implausivel (mesmo cliente 901, mas fora da vigencia do contrato
    # 11 e valor bem diferente) -> nao deve casar com nada, mas tem cara de
    # contrato (mesmo cliente) -> deve virar sinal de revisao manual.
    titulo_orfao_zeta_fora = _titulo(9002, None, 901, "ATRASADO", "10/03/2027", valor=999.0)

    # Orfao CANCELADO do mesmo cliente -> nao representa pagamento nem
    # pendencia real, nao pode nem religar nem virar sinal de revisao.
    titulo_orfao_zeta_cancelado = _titulo(9003, None, 901, "CANCELADO", "10/06/2026", valor=3000.0)

    titulos_raw = [titulo_alfa, titulo_orfao_zeta, titulo_orfao_zeta_fora, titulo_orfao_zeta_cancelado]

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

    # O titulo implausivel nao pode ter sido religado a lugar nenhum -> nao
    # deve existir grupo extra e o total de contratos fica so 1 (Alfa) + os
    # 2 do cadastro (Epsilon, Zeta).
    assert len(contratos) == 3, f"orfao implausivel nao deveria criar/entrar em nenhum grupo (contratos={len(contratos)})"
    ids_parcelas_zeta = {p["nCodTitulo"] for p in zeta["parcelas"]}
    assert 9002 not in ids_parcelas_zeta, "bug: titulo fora da vigencia/valor incompativel nao pode ser religado"
    print("OK: titulo orfao implausivel (fora da vigencia, valor incompativel) permanece nao religado")

    ids_revisao_zeta = {r["nCodTitulo"] for r in zeta["titulos_para_revisao"]}
    assert ids_revisao_zeta == {9002}, (
        f"bug: titulo orfao implausivel do mesmo cliente devia virar sinal de revisao manual (obtido {ids_revisao_zeta})"
    )
    motivo = zeta["titulos_para_revisao"][0]["motivo"]
    assert "vigência" in motivo and "valor" in motivo, f"motivo devia explicar data E valor incompativeis (obtido {motivo!r})"
    print("OK: titulo orfao implausivel vira sinal de titulos_para_revisao, com motivo explicando a incompatibilidade")

    assert 9003 not in ids_parcelas_zeta, "bug: titulo orfao CANCELADO nao pode ser religado por heuristica"
    assert 9003 not in ids_revisao_zeta, "bug: titulo orfao CANCELADO nao pode virar sinal de revisao (nao ajuda o usuario a decidir nada)"
    print("OK: titulo orfao CANCELADO fica fora tanto da religacao heuristica quanto do sinal de revisao")

    alfa = por_ctr[1]
    assert alfa["parcelas"][0]["vinculo"] == "confirmado"
    assert alfa["titulos_para_revisao"] == []
    print("OK: titulo com nCodCtr direto continua marcado como vinculo=confirmado")


if __name__ == "__main__":
    main()
