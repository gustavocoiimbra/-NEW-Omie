"""Teste offline (sem rede) para src/contratos.py: cobre os dois bugs
encontrados em revisão de código e corrigidos em seguida —

1. Contrato com única parcela pendente já vencida ("Atrasado", sem nenhuma
   "Previsto"/"Em aberto") tinha `proxima_parcela=None`, escondendo o pagamento
   mais urgente do contrato (casos reais: MECANIZOU, V4 COMPANY).
2. Contrato cuja única parcela futura já foi emitida mas ainda não venceu
   ("Em aberto", sem nenhuma "Previsto" associada) era classificado como
   "Encerrado" em vez de "Ativo".

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
            "cNumCtr": f"2026/{n_cod_ctr:05d}",
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

    print("\nTodos os testes offline de contratos passaram.")


if __name__ == "__main__":
    main()
