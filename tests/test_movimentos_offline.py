"""Teste offline (sem rede) para src/movimentos.py: valida que CONTA_A_PAGAR/
CONTA_A_RECEBER/PREVISAO_CONTRATO viram título e CONTA_CORRENTE_PAG/REC são
ignorados; que a soma de "Valor Pago/Recebido" não duplica mesmo com o par
CONTA_A_PAGAR/CONTA_CORRENTE_PAG (mesmo nCodTitulo) presente na amostra bruta
— era o bug original; que o resumo reduzido do PREVISAO_CONTRATO (só
nValLiquido) recebe o fallback nValAberto=nValLiquido; e que o array
`categorias` (rateio) da amostra bruta é ignorado: `report_builder` usa
sempre `cCodCateg` (categoria única), já que `ListarMovimentos` não expõe
rateio de forma confiável (veja nota no topo de `src/movimentos.py`).

Uso:
    python tests/test_movimentos_offline.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import report_builder  # noqa: E402
from src.movimentos import _filtrar_e_adaptar  # noqa: E402

CATEGORIA_MAP = {
    "2.01.03": {"descricao": "Insumos", "categoria_superior": "", "codigo_dre": ""},
    "2.01.05": {"descricao": "Despesas Gerais", "categoria_superior": "", "codigo_dre": ""},
    "2.01.06": {"descricao": "Despesas Administrativas", "categoria_superior": "", "codigo_dre": ""},
    "1.01.01": {"descricao": "Vendas de Produtos", "categoria_superior": "", "codigo_dre": ""},
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
    caminho_amostra = os.path.join(os.path.dirname(__file__), "sample_movimentos.json")
    with open(caminho_amostra, encoding="utf-8") as f:
        resposta_bruta = json.load(f)
    movimentos_raw = resposta_bruta["movimentos"]

    titulos_raw, ignorados = _filtrar_e_adaptar(movimentos_raw)

    assert ignorados == {"CONTA_CORRENTE_PAG": 1}, ignorados
    print("OK: _filtrar_e_adaptar ignora só CONTA_CORRENTE_PAG (PREVISAO_CONTRATO agora é incluído)")

    assert len(titulos_raw) == 3, f"esperado 3 títulos (5001, 5002, 7001), obtido {len(titulos_raw)}"
    codigos = {t["cabecTitulo"]["nCodTitulo"] for t in titulos_raw}
    assert codigos == {5001, 5002, 7001}
    print("OK: CONTA_A_PAGAR/CONTA_A_RECEBER/PREVISAO_CONTRATO viram título")

    titulo_5001 = next(t for t in titulos_raw if t["cabecTitulo"]["nCodTitulo"] == 5001)
    assert "aCodCateg" not in titulo_5001["cabecTitulo"], "categorias (rateio) da amostra bruta não deve ser lida"
    assert titulo_5001["cabecTitulo"]["cCodCateg"] == "2.01.05"
    print("OK: categorias (rateio) da amostra bruta é ignorado — usa cCodCateg (categoria única)")

    titulo_7001 = next(t for t in titulos_raw if t["cabecTitulo"]["nCodTitulo"] == 7001)
    _assert_quase_igual(
        titulo_7001["resumo"]["nValAberto"], 9500.0, "fallback nValAberto=nValLiquido para PREVISAO_CONTRATO"
    )
    print("OK: PREVISAO_CONTRATO recebe fallback nValAberto = nValLiquido")

    # Reaproveita report_builder sem nenhuma alteração — a soma de "Valor
    # Pago/Recebido" não deve duplicar mesmo com o par CONTA_A_PAGAR/
    # CONTA_CORRENTE_PAG (mesmo nCodTitulo) presente na amostra bruta — era o
    # bug original.
    linhas = report_builder.montar_linhas(titulos_raw, CATEGORIA_MAP, CC_MAP, CLIENTE_MAP)
    assert len(linhas) == 3, "5001 + 5002 + 7001 = 3 linhas (uma por título, sem rateio)"

    linha_5001 = next(l for l in linhas if l["Código Título"] == 5001)
    assert linha_5001["Categoria"] == "Despesas Gerais"
    _assert_quase_igual(linha_5001["Valor Título"], 1000.0, "Valor Título (cheio, sem rateio)")

    linha_7001 = next(l for l in linhas if l["Código Título"] == 7001)
    assert linha_7001["Categoria"] == "Vendas de Produtos"
    _assert_quase_igual(linha_7001["Valor Título"], 9999.0, "Valor Título (PREVISAO_CONTRATO)")
    _assert_quase_igual(linha_7001["Valor Aberto"], 9500.0, "Valor Aberto (fallback nValLiquido)")
    _assert_quase_igual(linha_7001["Valor Pago/Recebido"], 0.0, "Valor Pago (ausente -> 0, previsão não foi paga)")

    resumo = report_builder.montar_resumo(linhas)
    _assert_quase_igual(resumo["Total a Pagar (Valor Título)"], 1000.0, "Total a Pagar (Valor Título)")
    _assert_quase_igual(
        resumo["Total a Pagar - Pago"], 1000.0, "Total a Pagar - Pago (não deve duplicar CONTA_CORRENTE_PAG)"
    )
    _assert_quase_igual(
        resumo["Total a Receber (Valor Título)"], 10499.0, "Total a Receber (500 do 5002 + 9999 da previsão)"
    )
    _assert_quase_igual(
        resumo["Total a Receber - Em Aberto"], 10000.0, "Total a Receber - Em Aberto (500 + 9500 via fallback)"
    )
    assert resumo["Qtd Títulos a Pagar"] == 1
    assert resumo["Qtd Títulos a Receber"] == 2
    print("OK: montar_resumo não duplica Valor Pago/Recebido e inclui a previsão")

    df_geral = report_builder.montar_geral(titulos_raw, CATEGORIA_MAP, CC_MAP, CLIENTE_MAP)
    assert len(df_geral) == 3
    print("OK: montar_geral (layout bdContas) também funciona com dados de ListarMovimentos")

    print("\nTodos os testes offline de movimentos passaram.")


if __name__ == "__main__":
    main()
