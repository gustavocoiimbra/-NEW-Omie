"""Geração do arquivo Excel (.xlsx) final do relatório financeiro, com abas
formatadas (cabeçalho, moeda, autofiltro, congelamento de painel)."""
from __future__ import annotations

from typing import Any

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

_HEADER_FILL = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
_HEADER_FONT = Font(color="FFFFFF", bold=True)
_MOEDA_FMT = "R$ #,##0.00"
_PERCENTUAL_FMT = '0.00"%"'

_COLUNAS_MOEDA_PADRAO = {
    "Valor Título", "Valor Título (Total)", "Valor Pago/Recebido", "Valor Aberto", "Juros", "Multa",
    "Desconto", "Valor Líquido", "Pagar (Aberto)", "Receber (Aberto)", "Saldo",
}
_COLUNAS_PERCENTUAL = {"% Categoria"}


def _formatar_planilha(ws: Worksheet, df: pd.DataFrame) -> None:
    if df.empty:
        return

    for col_idx, nome_col in enumerate(df.columns, start=1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")

    is_moeda = [nome_col in _COLUNAS_MOEDA_PADRAO for nome_col in df.columns]
    is_percentual = [nome_col in _COLUNAS_PERCENTUAL for nome_col in df.columns]
    for row_idx in range(2, len(df) + 2):
        for col_idx, (moeda, percentual) in enumerate(zip(is_moeda, is_percentual), start=1):
            if moeda:
                ws.cell(row=row_idx, column=col_idx).number_format = _MOEDA_FMT
            elif percentual:
                ws.cell(row=row_idx, column=col_idx).number_format = _PERCENTUAL_FMT

    for col_idx, nome_col in enumerate(df.columns, start=1):
        try:
            maior_valor = df.iloc[:, col_idx - 1].astype(str).map(len).max()
        except (ValueError, TypeError):
            maior_valor = 10
        largura = min(max(len(str(nome_col)), int(maior_valor or 0)) + 3, 60)
        ws.column_dimensions[get_column_letter(col_idx)].width = largura

    ws.freeze_panes = "A2"
    if len(df) > 0:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(df.columns))}{len(df) + 1}"


def _escrever_resumo(ws: Worksheet, resumo: dict[str, Any]) -> None:
    ws.append(["Indicador", "Valor"])
    for col_idx in (1, 2):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT

    campos_moeda = {k for k in resumo if "Qtd" not in k}
    for label, valor in resumo.items():
        ws.append([label, valor])
        row = ws.max_row
        ws.cell(row=row, column=1).font = Font(bold=True)
        if label in campos_moeda:
            ws.cell(row=row, column=2).number_format = _MOEDA_FMT

    ws.column_dimensions["A"].width = 48
    ws.column_dimensions["B"].width = 22
    ws.freeze_panes = "A2"


def escrever_relatorio(
    caminho_saida: str,
    resumo: dict[str, Any],
    df_geral: pd.DataFrame,
    df_pagar: pd.DataFrame,
    df_receber: pd.DataFrame,
    df_status: pd.DataFrame,
    df_categoria: pd.DataFrame,
    df_cliente: pd.DataFrame,
    df_fluxo: pd.DataFrame,
) -> None:
    with pd.ExcelWriter(caminho_saida, engine="openpyxl") as writer:
        # Aba "Resumo" é escrita manualmente (chave/valor), as demais via pandas.
        pd.DataFrame().to_excel(writer, sheet_name="Resumo", index=False)
        df_geral.to_excel(writer, sheet_name="Geral", index=False)
        df_pagar.to_excel(writer, sheet_name="Contas a Pagar", index=False)
        df_receber.to_excel(writer, sheet_name="Contas a Receber", index=False)
        df_status.to_excel(writer, sheet_name="Por Status", index=False)
        df_categoria.to_excel(writer, sheet_name="Por Categoria", index=False)
        df_cliente.to_excel(writer, sheet_name="Por Cliente-Fornecedor", index=False)
        df_fluxo.to_excel(writer, sheet_name="Fluxo Mensal", index=False)

        wb = writer.book
        _escrever_resumo(wb["Resumo"], resumo)
        _formatar_planilha(wb["Geral"], df_geral)
        _formatar_planilha(wb["Contas a Pagar"], df_pagar)
        _formatar_planilha(wb["Contas a Receber"], df_receber)
        _formatar_planilha(wb["Por Status"], df_status)
        _formatar_planilha(wb["Por Categoria"], df_categoria)
        _formatar_planilha(wb["Por Cliente-Fornecedor"], df_cliente)
        _formatar_planilha(wb["Fluxo Mensal"], df_fluxo)
