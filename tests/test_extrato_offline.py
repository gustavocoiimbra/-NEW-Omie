"""Teste offline (sem rede) para src/extrato.py.

Cobre o comportamento não óbvio confirmado contra uma conta real: o
`ListarExtrato` marca como `cSituacao="Previsto"` tanto o que ainda vai
vencer quanto o que já venceu e segue em aberto (atrasado) — por isso
"previsto a vencer" tem que filtrar também por data, não só por `cSituacao`.

Uso:
    python tests/test_extrato_offline.py
"""
from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import extrato as extrato_mod  # noqa: E402

HOJE = date(2026, 8, 10)

CC_MAP = {
    1: "Bradesco",
    2: "Bradesco - CONTA APLICAÇÃO",
    3: "Itaú Unibanco",
    4: "Itaú Unibanco - CONTA APLICAÇÃO",
    5: "Caixinha",  # nao deve ser resolvida como conta alvo
}


def _mov(situacao, data, valor, cliente="Fornecedor X", categoria="Categoria X"):
    return {
        "cSituacao": situacao,
        "dDataLancamento": data,
        "nValorDocumento": valor,
        "cNatureza": "R" if valor >= 0 else "P",
        "cRazCliente": cliente,
        "cDesCategoria": categoria,
    }


# Respostas cruas de ListarExtrato por nCodCC, no mesmo shape que a Omie
# retorna: marcadores SALDO/SALDO ANTERIOR (sem cSituacao) + transacoes reais.
RESPOSTAS = {
    1: {
        "nCodCC": 1, "cDescricao": "Bradesco",
        "nSaldoAnterior": 1000.0, "nSaldoAtual": 1000.0,
        "listaMovimentos": [
            {"cDesCliente": "SALDO ANTERIOR", "dDataLancamento": "09/08/2026", "nSaldo": 1000.0, "nValorDocumento": 0},
            _mov("Conciliado", "05/08/2026", 500.0),  # ja pago -> nao entra
            _mov("Previsto", "01/08/2026", -300.0),  # Previsto mas com data PASSADA -> atrasado, deve ser excluido
            _mov("Previsto", "10/08/2026", 200.0),  # vence hoje -> conta (>= hoje)
            _mov("Previsto", "17/08/2026", -150.0),  # semana seguinte
        ],
    },
    2: {
        "nCodCC": 2, "cDescricao": "Bradesco - CONTA APLICAÇÃO",
        "nSaldoAnterior": 5000.0, "nSaldoAtual": 5000.0,
        "listaMovimentos": [],
    },
    3: {
        "nCodCC": 3, "cDescricao": "Itaú Unibanco",
        "nSaldoAnterior": 2000.0, "nSaldoAtual": 2000.0,
        "listaMovimentos": [
            _mov("Previsto", "24/08/2026", 1000.0),  # 2 semanas a frente
        ],
    },
    4: {
        "nCodCC": 4, "cDescricao": "Itaú Unibanco - CONTA APLICAÇÃO",
        "nSaldoAnterior": 500.0, "nSaldoAtual": 500.0,
        "listaMovimentos": [],
    },
}


class _ClienteFalso:
    def call(self, modulo, chamada, param):
        assert modulo == extrato_mod.MODULO
        assert chamada == extrato_mod.CHAMADA
        return RESPOSTAS[param["nCodCC"]]


def _assert_quase_igual(a: float, b: float, msg: str) -> None:
    assert abs(a - b) < 1e-6, f"{msg}: esperado {b}, obtido {a}"


def main() -> None:
    contas = extrato_mod.resolver_contas_alvo(CC_MAP)
    assert contas == {
        "Banco Bradesco": 1, "Banco Bradesco Aplicação": 2, "Banco Itaú": 3, "Banco Itaú Aplicação": 4,
    }
    print("OK: resolver_contas_alvo mapeia as 4 contas pelo nome cadastrado")

    try:
        extrato_mod.resolver_contas_alvo({1: "Bradesco"})
        raise AssertionError("deveria ter levantado ValueError com contas faltando")
    except ValueError as exc:
        assert "Itaú" in str(exc)
    print("OK: resolver_contas_alvo levanta erro claro quando falta alguma conta")

    saldo = extrato_mod.montar_saldo_caixa(_ClienteFalso(), CC_MAP, hoje=HOJE, dias_previsao=30)

    _assert_quase_igual(saldo["saldo_real_total"], 1000.0 + 5000.0 + 2000.0 + 500.0, "saldo_real_total")
    print("OK: saldo_real_total soma nSaldoAnterior das 4 contas")

    lancs = saldo["lancamentos_previstos"]
    valores = sorted(l["valor"] for l in lancs)
    assert valores == sorted([200.0, -150.0, 1000.0]), (
        f"bug: previsto com data passada (atrasado) nao pode entrar na soma de 'a vencer' — obtido {valores}"
    )
    assert all(l["data"] != "01/08/2026" for l in lancs), "a parcela Previsto de 01/08 (antes de hoje) deveria ter sido excluida"
    assert all(l["data"] != "05/08/2026" for l in lancs), "a parcela Conciliado (ja paga) nao deveria aparecer"
    print("OK: lancamentos_previstos exclui pago (Conciliado) e atrasado (Previsto com data < hoje)")

    _assert_quase_igual(saldo["total_previsto"], 200.0 - 150.0 + 1000.0, "total_previsto")
    _assert_quase_igual(saldo["saldo_previsto_total"], saldo["saldo_real_total"] + saldo["total_previsto"], "saldo_previsto_total")
    print("OK: total_previsto e saldo_previsto_total (real + previsto)")

    fluxo = saldo["fluxo_semanal"]
    assert fluxo[0]["inicio"] == "10/08/2026", "primeira semana deve comecar na segunda-feira da semana de 'hoje'"
    _assert_quase_igual(fluxo[0]["entradas_previstas"], 200.0, "entradas semana 1")
    _assert_quase_igual(fluxo[0]["liquido_previsto"], 200.0, "liquido semana 1")
    _assert_quase_igual(fluxo[0]["saldo_previsto_acumulado"], saldo["saldo_real_total"] + 200.0, "saldo acumulado semana 1")

    _assert_quase_igual(fluxo[1]["liquido_previsto"], -150.0, "liquido semana 2 (17/08)")
    _assert_quase_igual(fluxo[1]["saldo_previsto_acumulado"], saldo["saldo_real_total"] + 200.0 - 150.0, "saldo acumulado semana 2")

    # semana de 24/08 tem o lancamento de 1000 do Itau
    semana_3 = next(s for s in fluxo if s["inicio"] == "24/08/2026")
    _assert_quase_igual(semana_3["saldo_previsto_acumulado"], saldo["saldo_real_total"] + 200.0 - 150.0 + 1000.0, "saldo acumulado semana 3")

    # semana sem nenhum lancamento (ex.: 31/08) deve manter o saldo acumulado, nao pular
    semana_vazia = next(s for s in fluxo if s["inicio"] == "31/08/2026")
    _assert_quase_igual(semana_vazia["liquido_previsto"], 0.0, "semana vazia sem lancamento")
    _assert_quase_igual(semana_vazia["saldo_previsto_acumulado"], semana_3["saldo_previsto_acumulado"], "saldo acumulado carrega para semana vazia")
    print("OK: fluxo_semanal acumula corretamente semana a semana, inclusive semanas sem lancamento")

    print("\nTodos os testes offline de extrato passaram.")


if __name__ == "__main__":
    main()
