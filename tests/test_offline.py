"""Teste offline (sem credenciais/rede): valida a montagem do relatório e a
geração do Excel a partir de uma amostra fixa de títulos, no formato exato
retornado pela API da Omie (PesquisarLancamentos).

Uso:
    python tests/test_offline.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import report_builder  # noqa: E402
from src.excel_writer import escrever_relatorio  # noqa: E402

CATEGORIA_MAP = {
    "1.01.01": "Vendas de Produtos",
    "2.01.03": "Insumos",
    "2.01.05": "Despesas Gerais",
    "2.01.06": "Despesas Administrativas",
}
CC_MAP = {1: "Banco X - Conta Corrente", 2: "Banco Y - Conta Corrente"}

_CLIENTES = {
    555: {"razao_social": "Cliente Alfa Ltda", "nome_fantasia": "Alfa", "cnpj_cpf": "12.345.678/0001-90"},
    556: {"razao_social": "Cliente Beta ME", "nome_fantasia": "Beta", "cnpj_cpf": "98.765.432/0001-10"},
    777: {"razao_social": "Fornecedor Gama SA", "nome_fantasia": "Gama", "cnpj_cpf": "11.222.333/0001-44"},
}


class ClienteCacheFalso:
    def get(self, codigo):
        return _CLIENTES.get(codigo, {"razao_social": "", "nome_fantasia": "", "cnpj_cpf": ""})


def _assert_quase_igual(a: float, b: float, msg: str) -> None:
    assert abs(a - b) < 1e-6, f"{msg}: esperado {b}, obtido {a}"


def main() -> None:
    caminho_amostra = os.path.join(os.path.dirname(__file__), "sample_titulos.json")
    with open(caminho_amostra, encoding="utf-8") as f:
        titulos_raw = json.load(f)

    linhas = report_builder.montar_linhas(titulos_raw, CATEGORIA_MAP, CC_MAP, ClienteCacheFalso())
    # 4 títulos, mas o título 2002 tem rateio em 2 categorias -> 5 linhas no total.
    assert len(linhas) == 5, f"esperado 5 linhas, obtido {len(linhas)}"
    assert linhas[0]["Cliente/Fornecedor"] == "Cliente Alfa Ltda"
    assert linhas[0]["Categoria"] == "Vendas de Produtos"
    assert linhas[0]["Conta Corrente"] == "Banco X - Conta Corrente"
    print("OK: montar_linhas")

    linhas_rateio = [l for l in linhas if l["Código Título"] == 2002]
    assert len(linhas_rateio) == 2, "título 2002 deveria gerar 2 linhas (rateio de categoria)"
    linhas_rateio = sorted(linhas_rateio, key=lambda l: l["Categoria"])

    l_admin, l_gerais = linhas_rateio
    assert l_admin["Categoria"] == "Despesas Administrativas"
    _assert_quase_igual(l_admin["% Categoria"], 40.0, "% Categoria (Despesas Administrativas)")
    _assert_quase_igual(l_admin["Valor Título"], 120.0, "Valor Título rateado (Despesas Administrativas)")
    _assert_quase_igual(l_admin["Valor Título (Total)"], 300.0, "Valor Título (Total)")
    _assert_quase_igual(l_admin["Valor Aberto"], 320.0 * 0.4, "Valor Aberto rateado (Despesas Administrativas)")
    _assert_quase_igual(l_admin["Juros"], 20.0 * 0.4, "Juros rateado (Despesas Administrativas)")

    assert l_gerais["Categoria"] == "Despesas Gerais"
    _assert_quase_igual(l_gerais["% Categoria"], 60.0, "% Categoria (Despesas Gerais)")
    _assert_quase_igual(l_gerais["Valor Título"], 180.0, "Valor Título rateado (Despesas Gerais)")
    _assert_quase_igual(l_gerais["Valor Aberto"], 320.0 * 0.6, "Valor Aberto rateado (Despesas Gerais)")
    _assert_quase_igual(l_gerais["Juros"], 20.0 * 0.6, "Juros rateado (Despesas Gerais)")

    # A soma das linhas rateadas deve reconciliar exatamente com os totais originais do título.
    _assert_quase_igual(
        sum(l["Valor Título"] for l in linhas_rateio), 300.0, "soma Valor Título rateado == Valor Título original"
    )
    _assert_quase_igual(
        sum(l["Valor Aberto"] for l in linhas_rateio), 320.0, "soma Valor Aberto rateado == Valor Aberto original"
    )
    print("OK: rateio de categoria (explosão do título 2002 em 2 linhas)")

    resumo = report_builder.montar_resumo(linhas)
    _assert_quase_igual(resumo["Total a Receber (Valor Título)"], 2300.0, "Total a Receber (Valor Título)")
    _assert_quase_igual(resumo["Total a Receber - Em Aberto"], 1500.0, "Total a Receber - Em Aberto")
    _assert_quase_igual(resumo["Total a Pagar (Valor Título)"], 1250.0, "Total a Pagar (Valor Título)")
    _assert_quase_igual(resumo["Total a Pagar - Em Aberto"], 1270.0, "Total a Pagar - Em Aberto")
    _assert_quase_igual(
        resumo["Saldo Projetado (Receber Aberto - Pagar Aberto)"], 1500.0 - 1270.0, "Saldo Projetado"
    )
    assert resumo["Qtd Títulos a Pagar"] == 2
    assert resumo["Qtd Títulos a Receber"] == 2
    print("OK: montar_resumo")

    df_status = report_builder.montar_por_status(linhas)
    assert set(df_status["Status"]) == {"EMABERTO", "LIQUIDADO", "AVENCER", "ATRASADO"}
    print("OK: montar_por_status")

    df_categoria = report_builder.montar_por_categoria(linhas)
    assert len(df_categoria) == 4, "esperado 4 categorias distintas (rateio adiciona Despesas Administrativas)"
    linha_admin = df_categoria[df_categoria["Categoria"] == "Despesas Administrativas"].iloc[0]
    assert linha_admin["Quantidade"] == 1, "rateio não deve inflar a contagem de títulos distintos"
    _assert_quase_igual(linha_admin["Valor Título"], 120.0, "Valor Título agregado (Despesas Administrativas)")
    print("OK: montar_por_categoria")

    df_cliente = report_builder.montar_por_cliente(linhas)
    assert len(df_cliente) == 3
    print("OK: montar_por_cliente")

    df_fluxo = report_builder.montar_fluxo_mensal(linhas)
    assert list(df_fluxo["Mês"]) == ["2026-07"]
    print("OK: montar_fluxo_mensal")

    df_geral = report_builder.montar_geral(linhas)
    assert len(df_geral) == 5, "5 linhas (título 2002 rateado em 2), ordenadas por vencimento"
    assert list(df_geral["Data Vencimento"]) == [
        "05/07/2026", "05/07/2026", "10/07/2026", "15/07/2026", "20/07/2026",
    ]
    print("OK: montar_geral")

    df_pagar = _filtrar(linhas, "Pagar")
    df_receber = _filtrar(linhas, "Receber")

    with tempfile.TemporaryDirectory() as tmp:
        saida = os.path.join(tmp, "relatorio_teste.xlsx")
        escrever_relatorio(
            saida, resumo, df_geral, df_pagar, df_receber, df_status, df_categoria, df_cliente, df_fluxo
        )
        assert os.path.exists(saida) and os.path.getsize(saida) > 0
    print("OK: escrever_relatorio (arquivo .xlsx gerado)")

    print("\nTodos os testes offline passaram.")


def _filtrar(linhas, natureza):
    import pandas as pd

    df = pd.DataFrame(linhas)
    return df[df["Natureza"] == natureza].drop(columns=["Natureza"]).reset_index(drop=True)


if __name__ == "__main__":
    main()
