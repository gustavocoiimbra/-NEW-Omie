"""Transforma os títulos brutos da Omie (+ dados de enriquecimento) em linhas de
relatório e agregações prontas para exportação."""
from __future__ import annotations

from typing import Any

import pandas as pd

from .enrichment import ClienteFornecedorCache

_NATUREZA_LABEL = {"P": "Pagar", "R": "Receber"}


def _num(valor: Any) -> float:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return 0.0


def _fator_rateio(item: dict[str, Any], valor_titulo: float) -> float:
    """Fração (0-1) do título atribuída a este item de rateio de categoria."""
    perc = item.get("nPerc")
    if perc not in (None, ""):
        return _num(perc) / 100.0
    valor_item = _num(item.get("nValor"))
    if valor_titulo:
        return valor_item / valor_titulo
    return 0.0


def montar_linhas(
    titulos_raw: list[dict[str, Any]],
    categoria_map: dict[str, str],
    cc_map: dict[int, str],
    cliente_cache: ClienteFornecedorCache,
) -> list[dict[str, Any]]:
    """Achata cada título (cabecTitulo + resumo) em uma ou mais linhas de relatório.

    A Omie permite ratear um título entre várias categorias (`aCodCateg`). Quando
    isso ocorre, o título gera uma linha por categoria, com os valores monetários
    (título, aberto, pago, juros, multa, desconto, líquido) proporcionais ao
    percentual de rateio de cada categoria — assim, somar por categoria reflete
    corretamente o quanto do título pertence a cada uma.
    """
    linhas: list[dict[str, Any]] = []

    for titulo in titulos_raw:
        cabec = titulo.get("cabecTitulo", {}) or {}
        resumo = titulo.get("resumo", {}) or {}

        natureza = cabec.get("cNatureza", "")
        cod_cliente = cabec.get("nCodCliente")
        cad = cliente_cache.get(cod_cliente)
        cod_cc = cabec.get("nCodCC")
        valor_titulo = _num(cabec.get("nValorTitulo"))

        rateio = cabec.get("aCodCateg") or [
            {"cCodCateg": cabec.get("cCodCateg", ""), "nPerc": 100.0, "nValor": valor_titulo}
        ]

        for item in rateio:
            cod_categ = item.get("cCodCateg", "")
            fator = _fator_rateio(item, valor_titulo)

            linhas.append(
                {
                    "Natureza": _NATUREZA_LABEL.get(natureza, natureza),
                    "Código Título": cabec.get("nCodTitulo"),
                    "Código Integração": cabec.get("cCodIntTitulo"),
                    "Número Título": cabec.get("cNumTitulo"),
                    "Parcela": cabec.get("cNumParcela"),
                    "Cliente/Fornecedor": cad.get("razao_social") or "",
                    "CNPJ/CPF": cad.get("cnpj_cpf") or cabec.get("cCPFCNPJCliente") or "",
                    "Categoria": categoria_map.get(cod_categ, cod_categ),
                    "% Categoria": _num(item.get("nPerc")),
                    "Conta Corrente": cc_map.get(cod_cc, cod_cc),
                    "Documento Fiscal": cabec.get("cNumDocFiscal"),
                    "Tipo Documento": cabec.get("cTipo"),
                    "Data Emissão": cabec.get("dDtEmissao"),
                    "Data Vencimento": cabec.get("dDtVenc"),
                    "Data Pagamento": cabec.get("dDtPagamento"),
                    "Status": cabec.get("cStatus"),
                    "Valor Título": _num(item.get("nValor")),
                    "Valor Título (Total)": valor_titulo,
                    "Valor Pago/Recebido": _num(resumo.get("nValPago")) * fator,
                    "Valor Aberto": _num(resumo.get("nValAberto")) * fator,
                    "Juros": _num(resumo.get("nJuros")) * fator,
                    "Multa": _num(resumo.get("nMulta")) * fator,
                    "Desconto": _num(resumo.get("nDesconto")) * fator,
                    "Valor Líquido": _num(resumo.get("nValLiquido")) * fator,
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


def montar_geral(linhas: list[dict[str, Any]]) -> pd.DataFrame:
    """Todas as contas a pagar e a receber juntas, ordenadas por data de vencimento."""
    df = pd.DataFrame(linhas)
    if df.empty:
        return df
    ordem = pd.to_datetime(df["Data Vencimento"], dayfirst=True, errors="coerce")
    return (
        df.assign(_ordem=ordem)
        .sort_values(["_ordem", "Natureza"], kind="stable")
        .drop(columns=["_ordem"])
        .reset_index(drop=True)
    )


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
