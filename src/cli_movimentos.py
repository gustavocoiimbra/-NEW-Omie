"""CLI: mesma orquestração de `cli.py` (enriquecimento + relatório Excel), mas
buscando os dados via `financas/mf` (ListarMovimentos) em vez de
`financas/pesquisartitulos` (PesquisarLancamentos) — veja `src/movimentos.py`
para as diferenças entre as duas fontes.

Reaproveita `report_builder`/`excel_writer`/`enrichment` sem nenhuma alteração:
`movimentos.buscar_movimentos` já adapta cada item para o mesmo shape
`{"cabecTitulo": ..., "resumo": ...}` que essas funções esperam.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from datetime import datetime

from . import report_builder
from .config import ConfigError, carregar_config
from .enrichment import build_categoria_map, build_cliente_map, build_conta_corrente_map
from .excel_writer import escrever_relatorio
from .movimentos import LIMITE_REGISTROS_POR_PAGINA, buscar_movimentos, contar_movimentos
from .omie_client import OmieAPIError, OmieClient

# A partir deste volume de movimentos, avisamos que a execução deve demorar bastante.
_LIMIAR_AVISO_VOLUME = 1000

logger = logging.getLogger("cli_movimentos")

_DATA_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")


def _validar_data(valor: str) -> str:
    if not _DATA_RE.match(valor):
        raise argparse.ArgumentTypeError(f"data inválida: {valor!r} (use dd/mm/aaaa)")
    try:
        datetime.strptime(valor, "%d/%m/%Y")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"data inválida: {valor!r} ({exc})") from None
    return valor


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="relatorio-financeiro-omie-movimentos",
        description=(
            "Gera relatório financeiro (contas a pagar e a receber) a partir da API da "
            "Omie, usando ListarMovimentos (financas/mf) em vez de PesquisarLancamentos."
        ),
    )
    p.add_argument(
        "--data-inicio", default=None, type=_validar_data,
        help="Data inicial do filtro (dd/mm/aaaa). Se omitida junto com --data-fim, busca TODOS os movimentos já lançados.",
    )
    p.add_argument(
        "--data-fim", default=None, type=_validar_data,
        help="Data final do filtro (dd/mm/aaaa). Se omitida junto com --data-inicio, busca TODOS os movimentos já lançados.",
    )
    p.add_argument(
        "--filtro-data",
        choices=("vencimento", "emissao", "pagamento"),
        default="vencimento",
        help="Campo de data usado no filtro do período (padrão: vencimento)",
    )
    p.add_argument(
        "--natureza",
        choices=("P", "R", "PR"),
        default="PR",
        help="P=somente a pagar, R=somente a receber, PR=ambos numa única busca (padrão)",
    )
    p.add_argument(
        "--status", default=None,
        help=(
            "Filtrar por status do movimento (padrão: todos). O vocabulário de status "
            "do ListarMovimentos difere do PesquisarLancamentos (ex.: \"A VENCER\", "
            "\"PAGO\", \"RECEBIDO\", \"CANCELADO\", \"ATRASADO\") — não é validado "
            "localmente, passe o valor exato aceito pela sua conta."
        ),
    )
    p.add_argument("--output", default=None, help="Caminho do arquivo .xlsx de saída")
    p.add_argument(
        "--registros-por-pagina", type=int, default=LIMITE_REGISTROS_POR_PAGINA,
        help=f"Registros por página nas chamadas à Omie (padrão e limite documentado: {LIMITE_REGISTROS_POR_PAGINA})",
    )
    p.add_argument(
        "--sem-cache-disco", action="store_true",
        help="Desativa o cache local em disco (.cache/) de clientes/categorias/contas correntes",
    )
    p.add_argument(
        "--cache-ttl-horas", type=float, default=24.0,
        help="Validade (TTL) do cache local em disco, em horas (padrão: 24)",
    )
    p.add_argument("--debug", action="store_true", help="Grava as respostas brutas da API em debug_raw/ para inspeção")
    p.add_argument("--env-file", default=None, help="Caminho customizado para o arquivo .env")
    return p.parse_args(argv)


def _tag_periodo(args: argparse.Namespace) -> str:
    if not args.data_inicio and not args.data_fim:
        return "todos_movimentos_" + datetime.now().strftime("%Y%m%d-%H%M%S")
    inicio = args.data_inicio.replace("/", "-") if args.data_inicio else "inicio"
    fim = args.data_fim.replace("/", "-") if args.data_fim else "fim"
    return f"{inicio}_a_{fim}"


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = _parse_args(argv)

    try:
        config = carregar_config(args.env_file)
    except ConfigError as exc:
        print(f"Erro de configuração: {exc}", file=sys.stderr)
        return 1

    output = args.output or os.path.join("output", f"relatorio_financeiro_movimentos_{_tag_periodo(args)}.xlsx")
    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)

    client = OmieClient(
        app_key=config.app_key,
        app_secret=config.app_secret,
        max_req_por_segundo=config.max_req_por_segundo,
        debug_dir="debug_raw" if args.debug else None,
    )

    usar_cache_disco = not args.sem_cache_disco
    ttl_segundos = args.cache_ttl_horas * 3600
    natureza = None if args.natureza == "PR" else args.natureza

    try:
        logger.info("Estimando volume de movimentos antes de buscar...")
        total_estimado = contar_movimentos(
            client, data_inicio=args.data_inicio, data_fim=args.data_fim,
            natureza=natureza, filtro_data=args.filtro_data, status=args.status,
        )
        logger.info("Volume estimado: %d movimentos", total_estimado)

        if total_estimado > _LIMIAR_AVISO_VOLUME:
            logger.warning(
                "%d movimentos estimados — bem acima do usual. A geração do relatório pode "
                "levar bastante tempo em contas com histórico grande. Use Ctrl+C para "
                "cancelar se não era essa a intenção.",
                total_estimado,
            )
        elif not args.data_inicio and not args.data_fim:
            logger.warning("Nenhum filtro de data informado — buscando TODOS os movimentos já lançados na conta.")

        logger.info(
            "Buscando movimentos (%s a %s, filtro por %s, natureza=%s, status=%s)...",
            args.data_inicio or "sem limite inicial", args.data_fim or "sem limite final", args.filtro_data,
            args.natureza, args.status or "TODOS",
        )
        titulos_raw = buscar_movimentos(
            client,
            data_inicio=args.data_inicio,
            data_fim=args.data_fim,
            natureza=natureza,
            filtro_data=args.filtro_data,
            status=args.status,
            registros_por_pagina=args.registros_por_pagina,
        )
        logger.info("Total de movimentos encontrados: %d", len(titulos_raw))

        if not titulos_raw:
            logger.warning("Nenhum movimento encontrado para os filtros informados. Gerando relatório vazio.")

        logger.info("Carregando categorias, contas correntes e clientes/fornecedores para enriquecimento...")
        categoria_map = build_categoria_map(client, usar_cache=usar_cache_disco, ttl_segundos=ttl_segundos)
        cc_map = build_conta_corrente_map(client, usar_cache=usar_cache_disco, ttl_segundos=ttl_segundos)
        cliente_map = build_cliente_map(client, usar_cache=usar_cache_disco, ttl_segundos=ttl_segundos)

        logger.info("Montando linhas do relatório...")
        linhas = report_builder.montar_linhas(titulos_raw, categoria_map, cc_map, cliente_map)

        resumo = report_builder.montar_resumo(linhas)
        df_geral = report_builder.montar_geral(titulos_raw, categoria_map, cc_map, cliente_map)
        df_pagar = _df_por_natureza(linhas, "Pagar")
        df_receber = _df_por_natureza(linhas, "Receber")
        df_status = report_builder.montar_por_status(linhas)
        df_categoria = report_builder.montar_por_categoria(linhas)
        df_cliente = report_builder.montar_por_cliente(linhas)
        df_fluxo = report_builder.montar_fluxo_mensal(linhas)

        logger.info("Gravando relatório em %s ...", output)
        escrever_relatorio(
            output, resumo, df_geral, df_pagar, df_receber, df_status, df_categoria, df_cliente, df_fluxo
        )

    except OmieAPIError as exc:
        print(f"Erro na API da Omie: {exc}", file=sys.stderr)
        return 2

    print(f"\nRelatório gerado com sucesso: {output}")
    for chave, valor in resumo.items():
        print(f"  {chave}: {valor}")
    return 0


def _df_por_natureza(linhas: list[dict], natureza: str):
    import pandas as pd

    df = pd.DataFrame(linhas)
    if df.empty:
        return df
    return df[df["Natureza"] == natureza].drop(columns=["Natureza"]).reset_index(drop=True)


if __name__ == "__main__":
    raise SystemExit(main())
