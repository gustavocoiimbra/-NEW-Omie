"""Transforma os títulos brutos da Omie (+ dados de enriquecimento) em linhas de
relatório e agregações prontas para exportação."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterator

import pandas as pd

_NATUREZA_LABEL = {"P": "Pagar", "R": "Receber"}


def _num(valor: Any) -> float:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return 0.0


def _iter_titulos(
    titulos_raw: list[dict[str, Any]],
) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    """Itera (cabecTitulo, resumo) para cada título — uma linha por título, usando
    sempre `cCodCateg` (categoria única). Não lê `aCodCateg`/rateio: o endpoint
    alternativo `ListarMovimentos` não expõe rateio de categorias (confirmado
    comparando título a título com `PesquisarLancamentos` — ver `movimentos.py`),
    então o rateio foi removido também aqui para as duas fontes de dados
    produzirem o mesmo resultado para o mesmo título.

    Compartilhado por `montar_linhas` (schema interno) e `montar_geral` (schema do
    modelo padrão da Omie).
    """
    for titulo in titulos_raw:
        cabec = titulo.get("cabecTitulo", {}) or {}
        resumo = titulo.get("resumo", {}) or {}
        yield cabec, resumo


def _categoria_descricao(categoria_map: dict[str, dict[str, str]], cod_categ: str) -> str:
    info = categoria_map.get(cod_categ)
    if not info:
        return cod_categ
    return info.get("descricao") or cod_categ


def _grupo_descricao(categoria_map: dict[str, dict[str, str]], cod_categ: str) -> str:
    """Resolve a descrição do grupo (categoria_superior) de uma categoria."""
    info = categoria_map.get(cod_categ) or {}
    cod_grupo = info.get("categoria_superior") or ""
    if not cod_grupo:
        return ""
    return _categoria_descricao(categoria_map, cod_grupo)


def _dre_flag(categoria_map: dict[str, dict[str, str]], cod_categ: str) -> str:
    """"Sim" quando a categoria tem código de vínculo no DRE (codigo_dre) cadastrado
    na Omie, "Não" caso contrário."""
    info = categoria_map.get(cod_categ) or {}
    return "Sim" if info.get("codigo_dre") else "Não"


def _parse_data(valor: Any) -> date | None:
    if not valor:
        return None
    try:
        return datetime.strptime(valor, "%d/%m/%Y").date()
    except (TypeError, ValueError):
        return None


def _situacao_vencimento(
    cabec: dict[str, Any], resumo: dict[str, Any], natureza: str, hoje: date
) -> str:
    """Bucket de situação do vencimento (pago/recebido, vencido em faixas de dias,
    a vencer em faixas de dias), no mesmo critério usado pelos relatórios nativos
    da Omie."""
    if cabec.get("cStatus") == "CANCELADO":
        return "Cancelado"
    if resumo.get("cLiquidado") == "S":
        return "Recebido" if natureza == "R" else "Pago"

    venc = _parse_data(cabec.get("dDtVenc"))
    if venc is None:
        return ""

    dias = (venc - hoje).days
    if dias < 0:
        atraso = -dias
        if atraso <= 30:
            return "Vencido até 30 dias"
        if atraso <= 60:
            return "Vencido de 31 a 60 dias"
        if atraso <= 90:
            return "Vencido de 61 a 90 dias"
        return "Vencido mais de 90 dias"
    if dias == 0:
        return "Vence hoje"
    if dias == 1:
        return "Vence amanhã"
    if dias <= 30:
        return "A vencer até 30 dias"
    if dias <= 60:
        return "A vencer de 31 até 60 dias"
    if dias <= 90:
        return "A vencer de 61 até 90 dias"
    return "A vencer mais de 90 dias"


def montar_linhas(
    titulos_raw: list[dict[str, Any]],
    categoria_map: dict[str, dict[str, str]],
    cc_map: dict[int, str],
    cliente_map: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Achata cada título (cabecTitulo + resumo) em uma linha de relatório."""
    linhas: list[dict[str, Any]] = []

    for cabec, resumo in _iter_titulos(titulos_raw):
        natureza = cabec.get("cNatureza", "")
        cod_cliente = cabec.get("nCodCliente")
        cad = cliente_map.get(cod_cliente) or {}
        cod_cc = cabec.get("nCodCC")
        cod_categ = cabec.get("cCodCateg", "")

        linhas.append(
            {
                "Natureza": _NATUREZA_LABEL.get(natureza, natureza),
                "Código Título": cabec.get("nCodTitulo"),
                "Código Integração": cabec.get("cCodIntTitulo"),
                "Número Título": cabec.get("cNumTitulo"),
                "Parcela": cabec.get("cNumParcela"),
                "Cliente/Fornecedor": cad.get("nome_fantasia") or cad.get("razao_social") or "",
                "CNPJ/CPF": cad.get("cnpj_cpf") or cabec.get("cCPFCNPJCliente") or "",
                "Categoria": _categoria_descricao(categoria_map, cod_categ),
                "Conta Corrente": cc_map.get(cod_cc, cod_cc),
                "Documento Fiscal": cabec.get("cNumDocFiscal"),
                "Tipo Documento": cabec.get("cTipo"),
                "Data Emissão": cabec.get("dDtEmissao"),
                "Data Vencimento": cabec.get("dDtVenc"),
                "Data Pagamento": cabec.get("dDtPagamento"),
                "Status": cabec.get("cStatus"),
                "Valor Título": _num(cabec.get("nValorTitulo")),
                "Valor Pago/Recebido": _num(resumo.get("nValPago")),
                "Valor Aberto": _num(resumo.get("nValAberto")),
                "Juros": _num(resumo.get("nJuros")),
                "Multa": _num(resumo.get("nMulta")),
                "Desconto": _num(resumo.get("nDesconto")),
                "Valor Líquido": _num(resumo.get("nValLiquido")),
                "Liquidado": "Sim" if resumo.get("cLiquidado") == "S" else "Não",
                "Observação": cabec.get("observacao"),
            }
        )

    return linhas


def montar_resumo(linhas: list[dict[str, Any]]) -> dict[str, Any]:
    df = pd.DataFrame(linhas)
    if df.empty:
        return {
            "Total a Pagar (Valor Título)": 0.0,
            "Total a Pagar - Pago": 0.0,
            "Total a Pagar - Em Aberto": 0.0,
            "Total a Receber (Valor Título)": 0.0,
            "Total a Receber - Recebido": 0.0,
            "Total a Receber - Em Aberto": 0.0,
            "Saldo Projetado (Receber Aberto - Pagar Aberto)": 0.0,
            "Qtd Títulos a Pagar": 0,
            "Qtd Títulos a Receber": 0,
        }

    pagar = df[df["Natureza"] == "Pagar"]
    receber = df[df["Natureza"] == "Receber"]

    return {
        "Total a Pagar (Valor Título)": pagar["Valor Título"].sum(),
        "Total a Pagar - Pago": pagar["Valor Pago/Recebido"].sum(),
        "Total a Pagar - Em Aberto": pagar["Valor Aberto"].sum(),
        "Total a Receber (Valor Título)": receber["Valor Título"].sum(),
        "Total a Receber - Recebido": receber["Valor Pago/Recebido"].sum(),
        "Total a Receber - Em Aberto": receber["Valor Aberto"].sum(),
        "Saldo Projetado (Receber Aberto - Pagar Aberto)": (
            receber["Valor Aberto"].sum() - pagar["Valor Aberto"].sum()
        ),
        "Qtd Títulos a Pagar": int(pagar["Código Título"].nunique()),
        "Qtd Títulos a Receber": int(receber["Código Título"].nunique()),
    }


_TIPO_LABEL = {"R": "1. Contas a Receber", "P": "2. Contas a Pagar"}

# Colunas e ordem do modelo padrão de exportação da Omie (aba "bdContas"). "x",
# "Observação do Pagto ou Recbto" e "cod.fcx" não têm fonte de dados na API — ficam
# em branco, mas as colunas são mantidas para preservar a estrutura do modelo.
_COLUNAS_GERAL = [
    "x",
    "Tipo",
    "Grupo",
    "Categoria",
    "Observação da Conta",
    "Data de Registro (completa)",
    "Data de Emissão (completa)",
    "NC/Nfe",
    "Data de Vencimento (completa)",
    "Situação do Vencimento",
    "Valor da Conta",
    "Pago ou Recebido",
    "A Pagar ou Receber",
    "Conta Corrente",
    "Cliente ou Fornecedor (Nome Fantasia)",
    "Observação do Pagto ou Recbto",
    "Data de Pagto",
    "COFINS Retido",
    "CSLL Retido",
    "INSS Retido",
    "IR Retido",
    "ISS Retido",
    "PIS Retido",
    "Desconto",
    "Juros",
    "DRE",
    "cod.fcx",
]

_COLUNAS_GERAL_DATA = (
    "Data de Registro (completa)",
    "Data de Emissão (completa)",
    "Data de Vencimento (completa)",
    "Data de Pagto",
)


def montar_geral(
    titulos_raw: list[dict[str, Any]],
    categoria_map: dict[str, dict[str, str]],
    cc_map: dict[int, str],
    cliente_map: dict[int, dict[str, Any]],
    hoje: date | None = None,
) -> pd.DataFrame:
    """Monta a aba "Geral" no layout do modelo padrão de exportação da Omie
    (mesmas colunas, nomes e ordem da aba "bdContas"), uma linha por título,
    ordenada por data de vencimento.
    """
    hoje = hoje or date.today()
    linhas: list[dict[str, Any]] = []

    for cabec, resumo in _iter_titulos(titulos_raw):
        natureza = cabec.get("cNatureza", "")
        cod_cliente = cabec.get("nCodCliente")
        cad = cliente_map.get(cod_cliente) or {}
        cod_cc = cabec.get("nCodCC")
        cod_categ = cabec.get("cCodCateg", "")

        linhas.append(
            {
                "x": "",
                "Tipo": _TIPO_LABEL.get(natureza, natureza),
                "Grupo": _grupo_descricao(categoria_map, cod_categ),
                "Categoria": _categoria_descricao(categoria_map, cod_categ),
                "Observação da Conta": cabec.get("observacao") or "",
                "Data de Registro (completa)": cabec.get("dDtRegistro"),
                "Data de Emissão (completa)": cabec.get("dDtEmissao"),
                "NC/Nfe": cabec.get("cNumDocFiscal"),
                "Data de Vencimento (completa)": cabec.get("dDtVenc"),
                "Situação do Vencimento": _situacao_vencimento(cabec, resumo, natureza, hoje),
                "Valor da Conta": _num(cabec.get("nValorTitulo")),
                "Pago ou Recebido": _num(resumo.get("nValPago")),
                "A Pagar ou Receber": _num(resumo.get("nValAberto")),
                "Conta Corrente": cc_map.get(cod_cc, cod_cc),
                "Cliente ou Fornecedor (Nome Fantasia)": cad.get("nome_fantasia") or cad.get("razao_social") or "",
                "Observação do Pagto ou Recbto": "",
                "Data de Pagto": cabec.get("dDtPagamento"),
                "COFINS Retido": _num(cabec.get("nValorCOFINS")),
                "CSLL Retido": _num(cabec.get("nValorCSLL")),
                "INSS Retido": _num(cabec.get("nValorINSS")),
                "IR Retido": _num(cabec.get("nValorIR")),
                "ISS Retido": _num(cabec.get("nValorISS")),
                "PIS Retido": _num(cabec.get("nValorPIS")),
                "Desconto": _num(resumo.get("nDesconto")),
                "Juros": _num(resumo.get("nJuros")),
                "DRE": _dre_flag(categoria_map, cod_categ),
                "cod.fcx": "",
            }
        )

    df = pd.DataFrame(linhas, columns=_COLUNAS_GERAL)
    if df.empty:
        return df

    ordem = pd.to_datetime(df["Data de Vencimento (completa)"], dayfirst=True, errors="coerce")
    df = (
        df.assign(_ordem=ordem)
        .sort_values(["_ordem", "Tipo"], kind="stable")
        .drop(columns=["_ordem"])
        .reset_index(drop=True)
    )

    for col in _COLUNAS_GERAL_DATA:
        df[col] = pd.to_datetime(df[col], dayfirst=True, errors="coerce").dt.date

    return df


def montar_por_status(linhas: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(linhas)
    if df.empty:
        return pd.DataFrame(columns=["Natureza", "Status", "Quantidade", "Valor Título", "Valor Aberto"])
    agg = (
        df.groupby(["Natureza", "Status"], as_index=False)
        .agg(Quantidade=("Código Título", "nunique"), **{"Valor Título": ("Valor Título", "sum")}, **{"Valor Aberto": ("Valor Aberto", "sum")})
        .sort_values(["Natureza", "Status"])
    )
    return agg


def montar_por_categoria(linhas: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(linhas)
    if df.empty:
        return pd.DataFrame(columns=["Natureza", "Categoria", "Quantidade", "Valor Título", "Valor Aberto", "Valor Pago/Recebido"])
    agg = (
        df.groupby(["Natureza", "Categoria"], as_index=False)
        .agg(
            Quantidade=("Código Título", "nunique"),
            **{"Valor Título": ("Valor Título", "sum")},
            **{"Valor Aberto": ("Valor Aberto", "sum")},
            **{"Valor Pago/Recebido": ("Valor Pago/Recebido", "sum")},
        )
        .sort_values(["Natureza", "Valor Título"], ascending=[True, False])
    )
    return agg


def montar_por_cliente(linhas: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(linhas)
    if df.empty:
        return pd.DataFrame(columns=["Natureza", "Cliente/Fornecedor", "Quantidade", "Valor Título", "Valor Aberto", "Valor Pago/Recebido"])
    agg = (
        df.groupby(["Natureza", "Cliente/Fornecedor"], as_index=False)
        .agg(
            Quantidade=("Código Título", "nunique"),
            **{"Valor Título": ("Valor Título", "sum")},
            **{"Valor Aberto": ("Valor Aberto", "sum")},
            **{"Valor Pago/Recebido": ("Valor Pago/Recebido", "sum")},
        )
        .sort_values(["Natureza", "Valor Título"], ascending=[True, False])
    )
    return agg


def montar_fluxo_mensal(linhas: list[dict[str, Any]]) -> pd.DataFrame:
    """Fluxo de caixa projetado por mês de vencimento (valores em aberto)."""
    df = pd.DataFrame(linhas)
    if df.empty:
        return pd.DataFrame(columns=["Mês", "Pagar (Aberto)", "Receber (Aberto)", "Saldo"])

    df = df.copy()
    df["_venc"] = pd.to_datetime(df["Data Vencimento"], dayfirst=True, errors="coerce")
    df = df.dropna(subset=["_venc"])
    if df.empty:
        return pd.DataFrame(columns=["Mês", "Pagar (Aberto)", "Receber (Aberto)", "Saldo"])

    df["Mês"] = df["_venc"].dt.strftime("%Y-%m")
    pivot = (
        df.groupby(["Mês", "Natureza"])["Valor Aberto"]
        .sum()
        .unstack("Natureza", fill_value=0.0)
        .reset_index()
    )
    for col in ("Pagar", "Receber"):
        if col not in pivot.columns:
            pivot[col] = 0.0
    pivot = pivot.rename(columns={"Pagar": "Pagar (Aberto)", "Receber": "Receber (Aberto)"})
    pivot["Saldo"] = pivot["Receber (Aberto)"] - pivot["Pagar (Aberto)"]
    return pivot.sort_values("Mês")[["Mês", "Pagar (Aberto)", "Receber (Aberto)", "Saldo"]]
