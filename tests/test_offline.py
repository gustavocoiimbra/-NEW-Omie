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
from datetime import date

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import report_builder  # noqa: E402
from src.excel_writer import escrever_relatorio  # noqa: E402

CATEGORIA_MAP = {
    "1.01": {"descricao": "Receita Operacional", "categoria_superior": "", "codigo_dre": ""},
    "1.01.01": {"descricao": "Vendas de Produtos", "categoria_superior": "1.01", "codigo_dre": "3.01.01"},
    "2.01": {"descricao": "Despesas Operacionais", "categoria_superior": "", "codigo_dre": ""},
    "2.01.03": {"descricao": "Insumos", "categoria_superior": "2.01", "codigo_dre": "4.01.03"},
    "2.01.05": {"descricao": "Despesas Gerais", "categoria_superior": "2.01", "codigo_dre": ""},
    "2.01.06": {"descricao": "Despesas Administrativas", "categoria_superior": "2.01", "codigo_dre": "4.01.06"},
}
CC_MAP = {1: "Banco X - Conta Corrente", 2: "Banco Y - Conta Corrente"}

CLIENTE_MAP = {
    555: {"razao_social": "Cliente Alfa Ltda", "nome_fantasia": "Alfa", "cnpj_cpf": "12.345.678/0001-90"},
    556: {"razao_social": "Cliente Beta ME", "nome_fantasia": "Beta", "cnpj_cpf": "98.765.432/0001-10"},
    777: {"razao_social": "Fornecedor Gama SA", "nome_fantasia": "Gama", "cnpj_cpf": "11.222.333/0001-44"},
}


def _assert_quase_igual(a: float, b: float, msg: str) -> None:
    assert abs(a - b) < 1e-6, f"{msg}: esperado {b}, obtido {a}"


def main() -> None:
    caminho_amostra = os.path.join(os.path.dirname(__file__), "sample_titulos.json")
    with open(caminho_amostra, encoding="utf-8") as f:
        titulos_raw = json.load(f)

    linhas = report_builder.montar_linhas(titulos_raw, CATEGORIA_MAP, CC_MAP, CLIENTE_MAP)
    # 4 títulos -> 4 linhas (sem rateio: cada título usa só cCodCateg, categoria única).
    assert len(linhas) == 4, f"esperado 4 linhas, obtido {len(linhas)}"
    assert linhas[0]["Cliente/Fornecedor"] == "Alfa"
    assert linhas[0]["Categoria"] == "Vendas de Produtos"
    assert linhas[0]["Conta Corrente"] == "Banco X - Conta Corrente"
    print("OK: montar_linhas")

    # Título 2002 tem aCodCateg (rateio) na amostra bruta, mas isso é ignorado:
    # o valor cheio do título fica sob a categoria única de cCodCateg.
    linha_2002 = next(l for l in linhas if l["Código Título"] == 2002)
    assert linha_2002["Categoria"] == "Despesas Gerais"
    _assert_quase_igual(linha_2002["Valor Título"], 300.0, "Valor Título (cheio, sem rateio)")
    _assert_quase_igual(linha_2002["Valor Aberto"], 320.0, "Valor Aberto (cheio, sem rateio)")
    _assert_quase_igual(linha_2002["Juros"], 20.0, "Juros (cheio, sem rateio)")
    print("OK: aCodCateg da amostra bruta é ignorado (rateio removido)")

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
    assert len(df_categoria) == 3, "esperado 3 categorias distintas (Vendas de Produtos, Insumos, Despesas Gerais)"
    linha_gerais = df_categoria[df_categoria["Categoria"] == "Despesas Gerais"].iloc[0]
    assert linha_gerais["Quantidade"] == 1
    _assert_quase_igual(linha_gerais["Valor Título"], 300.0, "Valor Título agregado (Despesas Gerais)")
    print("OK: montar_por_categoria")

    df_cliente = report_builder.montar_por_cliente(linhas)
    assert len(df_cliente) == 3
    print("OK: montar_por_cliente")

    df_fluxo = report_builder.montar_fluxo_mensal(linhas)
    assert list(df_fluxo["Mês"]) == ["2026-07"]
    print("OK: montar_fluxo_mensal")

    df_geral = report_builder.montar_geral(
        titulos_raw, CATEGORIA_MAP, CC_MAP, CLIENTE_MAP, hoje=date(2026, 7, 12)
    )
    colunas_esperadas = [
        "x", "Tipo", "Grupo", "Categoria", "Observação da Conta",
        "Data de Registro (completa)", "Data de Emissão (completa)", "NC/Nfe",
        "Data de Vencimento (completa)", "Situação do Vencimento",
        "Valor da Conta", "Pago ou Recebido", "A Pagar ou Receber",
        "Conta Corrente", "Cliente ou Fornecedor (Nome Fantasia)",
        "Observação do Pagto ou Recbto", "Data de Pagto",
        "COFINS Retido", "CSLL Retido", "INSS Retido", "IR Retido", "ISS Retido", "PIS Retido",
        "Desconto", "Juros", "DRE", "cod.fcx",
    ]
    assert list(df_geral.columns) == colunas_esperadas, "colunas devem seguir o layout do modelo bdContas"
    assert len(df_geral) == 4, "4 linhas (uma por título, sem rateio), ordenadas por vencimento"

    assert list(df_geral["Data de Vencimento (completa)"]) == [
        date(2026, 7, 5), date(2026, 7, 10), date(2026, 7, 15), date(2026, 7, 20),
    ]
    assert list(df_geral["Tipo"]) == [
        "2. Contas a Pagar", "1. Contas a Receber", "1. Contas a Receber", "2. Contas a Pagar",
    ]
    assert list(df_geral["Grupo"]) == [
        "Despesas Operacionais", "Receita Operacional", "Receita Operacional", "Despesas Operacionais",
    ]
    assert list(df_geral["DRE"]) == ["Não", "Sim", "Sim", "Sim"], (
        "2.01.05 (título 2002) não tem codigo_dre (Não); as demais categorias têm (Sim)"
    )
    assert list(df_geral["Situação do Vencimento"]) == [
        "Vencido até 30 dias", "Vencido até 30 dias", "Recebido", "A vencer até 30 dias",
    ]
    print("OK: montar_geral (colunas, Tipo, Grupo, DRE, Situação do Vencimento)")

    # Retenções (cabecTitulo) do título 2002 (linha 0) ficam com o valor cheio,
    # sem proporção de rateio — o array aCodCateg da amostra bruta é ignorado.
    _assert_quase_igual(df_geral.iloc[0]["IR Retido"], 100.0, "IR Retido (cheio, sem rateio)")
    _assert_quase_igual(df_geral.iloc[0]["PIS Retido"], 30.0, "PIS Retido (cheio, sem rateio)")
    _assert_quase_igual(df_geral.iloc[0]["COFINS Retido"], 50.0, "COFINS Retido (cheio, sem rateio)")
    print("OK: montar_geral (retenções de impostos, sem proporção de rateio)")

    # Datas reais (não texto) — necessário para o formato dd/mm/yyyy funcionar no Excel.
    assert df_geral.iloc[0]["Data de Registro (completa)"] == date(2026, 7, 4)  # título 2002
    assert df_geral.iloc[1]["Data de Registro (completa)"] == date(2026, 6, 28)  # título 1001
    assert pd.isna(df_geral.iloc[2]["Data de Registro (completa)"]), "título sem dDtRegistro deve virar NaT/None"
    assert df_geral.iloc[1]["Cliente ou Fornecedor (Nome Fantasia)"] == "Alfa"

    # Colunas sem fonte de dados na API ficam em branco, mas presentes na estrutura.
    assert set(df_geral["x"]) == {""}
    assert set(df_geral["Observação do Pagto ou Recbto"]) == {""}
    assert set(df_geral["cod.fcx"]) == {""}
    print("OK: montar_geral (datas reais e colunas sem fonte na API)")

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
