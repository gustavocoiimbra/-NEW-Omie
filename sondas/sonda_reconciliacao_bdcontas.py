# -*- coding: utf-8 -*-
"""Sonda exploratória: reproduz o "replicado" bdContas via API, combinando os
3 achados confirmados contra a conta real na investigação de reconciliação
bdContas-vs-API (`sondas/pesquisa.ipynb`):

1. Filtrar `CANCELADO` — a planilha nativa nunca traz esse status (ver
   `sondas/pesquisa.ipynb`, seção 8).
2. Recuperar o rateio de categoria por título via `ConsultarContaPagar`
   (`financas/contapagar`, campo `categorias[]`) — `ListarMovimentos` não
   expõe rateio (ver nota em `src/movimentos.py`); usado aqui só pra
   RECEITA FAZENDA, o caso com rateio confirmado nesta investigação.
3. Recuperar lançamentos da conta "Caixinha" via `ListarExtrato`
   (`financas/extrato`) pedindo **mês a mês** — uma janela larga devolve
   vazio pra essa conta (achado documentado em `src/extrato.py`, perto de
   `buscar_extrato`).

Depois roda a mesma reconciliação de 3 fases usada em `pesquisa.ipynb` contra
a planilha nativa, pra medir quanto sobra de divergência com as 3 correções
aplicadas juntas — objetivo é medir, não corrigir `main_movimentos.py`/
`main_dashboard.py` (nenhum dos dois usa esta lógica; ela existe só aqui).

Uso:
    python sondas/sonda_reconciliacao_bdcontas.py

Não modifica nada na Omie: só chamadas de leitura (Listar*/Consultar*). Grava
o resultado em `sondas/output/replicado_bdcontas.csv` (pasta fora do
controle de versão — veja `sondas/.gitignore`). Espera `centria_atualizado.xlsx`
na raiz do repo (mesmo arquivo usado por `pesquisa.ipynb`).
"""
from __future__ import annotations

import calendar
import html
import os
import re
import sys
import unicodedata
from datetime import date

import pandas as pd

_RAIZ_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _RAIZ_REPO)

from src.config import carregar_config  # noqa: E402
from src.omie_client import OmieClient  # noqa: E402
from src.movimentos import buscar_movimentos  # noqa: E402
from src.enrichment import build_categoria_map, build_conta_corrente_map, build_cliente_map  # noqa: E402
from src.extrato import buscar_extrato  # noqa: E402
from src import report_builder  # noqa: E402

COLUNAS_BDCONTAS = [
    "x", "Tipo", "Grupo", "Categoria", "Observação da Conta",
    "Data de Registro (completa)", "Data de Emissão (completa)", "NC/Nfe",
    "Data de Vencimento (completa)", "Situação do Vencimento", "Valor da Conta",
    "Pago ou Recebido", "A Pagar ou Receber", "Conta Corrente",
    "Cliente ou Fornecedor (Nome Fantasia)", "Observação do Pagto ou Recbto",
    "Data de Pagto", "COFINS Retido", "CSLL Retido", "INSS Retido", "IR Retido",
    "ISS Retido", "PIS Retido", "Desconto", "Juros", "DRE", "cod.fcx",
]

print("=== 1. Config + planilha nativa ===")
config = carregar_config()
client = OmieClient(app_key=config.app_key, app_secret=config.app_secret, max_req_por_segundo=config.max_req_por_segundo)

df_native = pd.read_excel(os.path.join(_RAIZ_REPO, "centria_atualizado.xlsx"), sheet_name="bdContas", header=2)
df_native.columns = COLUNAS_BDCONTAS
df_native["_origem"] = "nativo"
print(f"{len(df_native)} lançamentos na planilha nativa")

print("\n=== 2. Base API: ListarMovimentos, sem CANCELADO (achado 1) ===")
titulos_raw_todos = buscar_movimentos(client)
titulos_raw = [t for t in titulos_raw_todos if (t.get("cabecTitulo") or {}).get("cStatus") != "CANCELADO"]
print(f"{len(titulos_raw_todos)} títulos brutos -> {len(titulos_raw)} sem cancelados")

categoria_map = build_categoria_map(client)
cc_map = build_conta_corrente_map(client)
cliente_map = build_cliente_map(client)

print("\n=== 3. Rateio da RECEITA FAZENDA via ConsultarContaPagar (achado 2) ===")
cod_receita_fazenda = None
for cod, info in cliente_map.items():
    if (info.get("nome_fantasia") or "").strip() == "RECEITA FAZENDA":
        cod_receita_fazenda = cod
        break
print(f"nCodCliente RECEITA FAZENDA: {cod_receita_fazenda}")

titulos_rf_pagar = [
    t for t in titulos_raw
    if (t.get("cabecTitulo") or {}).get("nCodCliente") == cod_receita_fazenda
    and (t.get("cabecTitulo") or {}).get("cNatureza") == "P"
]
print(f"{len(titulos_rf_pagar)} títulos Contas a Pagar da RECEITA FAZENDA a checar")

rateio_por_titulo = {}  # nCodTitulo -> lista de {codigo_categoria, valor}
for t in titulos_rf_pagar:
    n_cod = (t.get("cabecTitulo") or {}).get("nCodTitulo")
    try:
        resp = client.call("financas/contapagar", "ConsultarContaPagar", {"codigo_lancamento_omie": n_cod})
    except Exception as e:
        print(f"  aviso: falhou ConsultarContaPagar pra {n_cod}: {e}")
        continue
    cats = resp.get("categorias") or []
    if len(cats) > 1:
        rateio_por_titulo[n_cod] = cats

print(f"Títulos com rateio confirmado (2+ categorias): {len(rateio_por_titulo)}")
for n_cod, cats in rateio_por_titulo.items():
    print(f"  {n_cod}: {[(c['codigo_categoria'], c['valor']) for c in cats]}")

print("\n=== 4. Caixinha via ListarExtrato, janela mensal (achado 3) ===")


def meses_entre(inicio: date, fim: date):
    y, m = inicio.year, inicio.month
    while (y, m) <= (fim.year, fim.month):
        ultimo_dia = calendar.monthrange(y, m)[1]
        yield date(y, m, 1), date(y, m, ultimo_dia)
        m += 1
        if m > 12:
            m = 1
            y += 1


cods_caixinha = [cod for cod, desc in cc_map.items() if "caixinha" in str(desc).lower()]
lancamentos_caixinha = []
hoje = date.today()
for cod in cods_caixinha:
    for ini, fim in meses_entre(date(2024, 6, 1), hoje):
        resp = buscar_extrato(client, cod, ini.strftime("%d/%m/%Y"), fim.strftime("%d/%m/%Y"))
        for m in resp.get("listaMovimentos") or []:
            if m.get("nCodLancamento"):
                m["_nCodCC"] = cod
                lancamentos_caixinha.append(m)
print(f"{len(lancamentos_caixinha)} lançamentos reais recuperados da Caixinha via ListarExtrato")


print("\n=== 5. Montando o 'replicado' no layout bdContas ===")


def _strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _norm_text(v):
    if pd.isna(v):
        return ""
    s = html.unescape(str(v)).strip().upper()
    s = _strip_accents(s)
    return re.sub(r"\s+", " ", s)


linhas = []
for titulo in titulos_raw:
    cabec = titulo.get("cabecTitulo", {}) or {}
    resumo = titulo.get("resumo", {}) or {}
    n_cod = cabec.get("nCodTitulo")
    natureza = cabec.get("cNatureza", "")
    cod_cliente = cabec.get("nCodCliente")
    cad = cliente_map.get(cod_cliente) or {}
    cod_cc = cabec.get("nCodCC")

    partes_rateio = rateio_por_titulo.get(n_cod)
    if partes_rateio:
        # Expande em uma linha por categoria do rateio, valor proporcional.
        for parte in partes_rateio:
            cod_categ = parte["codigo_categoria"]
            linhas.append({
                "x": "", "Tipo": report_builder._TIPO_LABEL.get(natureza, natureza),
                "Grupo": report_builder._grupo_descricao(categoria_map, cod_categ),
                "Categoria": report_builder._categoria_com_sufixo(categoria_map, cod_categ),
                "Observação da Conta": report_builder._observacao(cabec),
                "Data de Registro (completa)": cabec.get("dDtRegistro"),
                "Data de Emissão (completa)": cabec.get("dDtEmissao"),
                "NC/Nfe": cabec.get("cNumDocFiscal"),
                "Data de Vencimento (completa)": cabec.get("dDtVenc"),
                "Situação do Vencimento": report_builder._situacao_vencimento(cabec, resumo, natureza, hoje),
                "Valor da Conta": parte["valor"],
                "Pago ou Recebido": parte["valor"] if cabec.get("cStatus") in ("PAGO", "RECEBIDO") else 0.0,
                "A Pagar ou Receber": 0.0 if cabec.get("cStatus") in ("PAGO", "RECEBIDO") else parte["valor"],
                "Conta Corrente": cc_map.get(cod_cc, cod_cc),
                "Cliente ou Fornecedor (Nome Fantasia)": cad.get("nome_fantasia") or cad.get("razao_social") or "",
                "Observação do Pagto ou Recbto": "", "Data de Pagto": cabec.get("dDtPagamento"),
                "COFINS Retido": 0.0, "CSLL Retido": 0.0, "INSS Retido": 0.0, "IR Retido": 0.0,
                "ISS Retido": 0.0, "PIS Retido": 0.0, "Desconto": 0.0, "Juros": 0.0,
                "DRE": report_builder._dre_flag(categoria_map, cod_categ, cabec.get("cStatus")),
                "cod.fcx": "",
            })
        continue

    cod_categ = cabec.get("cCodCateg", "")
    linhas.append({
        "x": "", "Tipo": report_builder._TIPO_LABEL.get(natureza, natureza),
        "Grupo": report_builder._grupo_descricao(categoria_map, cod_categ),
        "Categoria": report_builder._categoria_com_sufixo(categoria_map, cod_categ),
        "Observação da Conta": report_builder._observacao(cabec),
        "Data de Registro (completa)": cabec.get("dDtRegistro"),
        "Data de Emissão (completa)": cabec.get("dDtEmissao"),
        "NC/Nfe": cabec.get("cNumDocFiscal"),
        "Data de Vencimento (completa)": cabec.get("dDtVenc"),
        "Situação do Vencimento": report_builder._situacao_vencimento(cabec, resumo, natureza, hoje),
        "Valor da Conta": report_builder._num(cabec.get("nValorTitulo")),
        "Pago ou Recebido": report_builder._num(resumo.get("nValPago")),
        "A Pagar ou Receber": report_builder._num(resumo.get("nValAberto")),
        "Conta Corrente": cc_map.get(cod_cc, cod_cc),
        "Cliente ou Fornecedor (Nome Fantasia)": cad.get("nome_fantasia") or cad.get("razao_social") or "",
        "Observação do Pagto ou Recbto": "", "Data de Pagto": cabec.get("dDtPagamento"),
        "COFINS Retido": report_builder._num_retido(cabec, "nValorCOFINS", "cRetCOFINS"),
        "CSLL Retido": report_builder._num_retido(cabec, "nValorCSLL", "cRetCSLL"),
        "INSS Retido": report_builder._num_retido(cabec, "nValorINSS", "cRetINSS"),
        "IR Retido": report_builder._num_retido(cabec, "nValorIR", "cRetIR"),
        "ISS Retido": report_builder._num_retido(cabec, "nValorISS", "cRetISS"),
        "PIS Retido": report_builder._num_retido(cabec, "nValorPIS", "cRetPIS"),
        "Desconto": report_builder._num(resumo.get("nDesconto")),
        "Juros": report_builder._num(resumo.get("nJuros")),
        "DRE": report_builder._dre_flag(categoria_map, cod_categ, cabec.get("cStatus")),
        "cod.fcx": "",
    })

# Lançamentos de Caixinha (extrato) -- não são títulos, montados com os campos
# que o extrato de fato tem.
for m in lancamentos_caixinha:
    natureza = m.get("cNatureza", "")
    valor = float(m.get("nValorDocumento") or 0.0)
    linhas.append({
        "x": "", "Tipo": report_builder._TIPO_LABEL.get(natureza, natureza),
        "Grupo": "", "Categoria": m.get("cDesCategoria", ""),
        "Observação da Conta": m.get("cOrigem", ""),
        "Data de Registro (completa)": m.get("dDataLancamento"),
        "Data de Emissão (completa)": m.get("dDataLancamento"),
        "NC/Nfe": m.get("cNumero"),
        "Data de Vencimento (completa)": m.get("dDataLancamento"),
        "Situação do Vencimento": m.get("cSituacao", ""),
        "Valor da Conta": valor,
        "Pago ou Recebido": 0.0, "A Pagar ou Receber": valor,
        "Conta Corrente": cc_map.get(m.get("_nCodCC"), m.get("_nCodCC")),
        "Cliente ou Fornecedor (Nome Fantasia)": m.get("cRazCliente") or m.get("cDesCliente") or "",
        "Observação do Pagto ou Recbto": "", "Data de Pagto": None,
        "COFINS Retido": 0.0, "CSLL Retido": 0.0, "INSS Retido": 0.0, "IR Retido": 0.0,
        "ISS Retido": 0.0, "PIS Retido": 0.0, "Desconto": 0.0, "Juros": 0.0,
        "DRE": "", "cod.fcx": "",
    })

df_replicado = pd.DataFrame(linhas, columns=COLUNAS_BDCONTAS)
for col in ["Data de Registro (completa)", "Data de Emissão (completa)", "Data de Vencimento (completa)", "Data de Pagto"]:
    df_replicado[col] = pd.to_datetime(df_replicado[col], dayfirst=True, errors="coerce")
df_replicado["_origem"] = "api"
print(f"{len(df_replicado)} linhas no replicado final")


def normalizar(df):
    df = df.copy()
    df["_valor_abs"] = df["Valor da Conta"].abs().round(2)
    df["_venc"] = pd.to_datetime(df["Data de Vencimento (completa)"], errors="coerce").dt.date
    df["_tipo"] = df["Tipo"].map(_norm_text)
    df["_cliente"] = df["Cliente ou Fornecedor (Nome Fantasia)"].map(_norm_text)
    df["_conta"] = df["Conta Corrente"].map(_norm_text)
    df["_categoria"] = df["Categoria"].map(_norm_text).str.replace(r"\s*\(INATIVA\)\s*$", "", regex=True)
    nc = df["NC/Nfe"].map(_norm_text)
    df["_nc"] = nc.where(~nc.isin(["", "N/D", "NAN", "NONE"]))
    df["_row_id"] = df.index
    return df


def casar_multiconjunto(nativo, api, chave):
    n = nativo.copy()
    a = api.copy()
    n["_rank"] = n.groupby(chave).cumcount()
    a["_rank"] = a.groupby(chave).cumcount()
    pares = n.merge(a, on=chave + ["_rank"], suffixes=("_nativo", "_api"))
    ids_nativo = set(pares["_row_id_nativo"])
    ids_api = set(pares["_row_id_api"])
    return pares, nativo[~nativo["_row_id"].isin(ids_nativo)], api[~api["_row_id"].isin(ids_api)]


print("\n=== 6. Reconciliação final (mesma lógica de 3 fases de pesquisa.ipynb) ===")
dfn = normalizar(df_native)
dfa = normalizar(df_replicado)

n_com_nc = dfn[dfn["_nc"].notna()]
a_com_nc = dfa[dfa["_nc"].notna()]
pares1, n1_sobra, a1_sobra = casar_multiconjunto(n_com_nc, a_com_nc, ["_tipo", "_nc", "_valor_abs", "_venc"])
n_pool2 = pd.concat([n1_sobra, dfn[dfn["_nc"].isna()]])
a_pool2 = pd.concat([a1_sobra, dfa[dfa["_nc"].isna()]])
pares2, n2_sobra, a2_sobra = casar_multiconjunto(n_pool2, a_pool2, ["_tipo", "_valor_abs", "_venc", "_conta", "_cliente"])
pares3, n3_sobra, a3_sobra = casar_multiconjunto(n2_sobra, a2_sobra, ["_tipo", "_valor_abs", "_venc", "_conta"])

print(f"Fase 1: {len(pares1)} | Fase 2: {len(pares2)} | Fase 3: {len(pares3)}")
print(f"Só na planilha nativa: {len(n3_sobra)} de {len(dfn)} ({len(n3_sobra)/len(dfn):.1%})")
print(f"Só no replicado (API): {len(a3_sobra)} de {len(dfa)} ({len(a3_sobra)/len(dfa):.1%})")

pasta_saida = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
os.makedirs(pasta_saida, exist_ok=True)
caminho_saida = os.path.join(pasta_saida, "replicado_bdcontas.csv")
df_replicado.to_csv(caminho_saida, index=False, encoding="utf-8-sig")
print(f"\nGravado {caminho_saida}")
