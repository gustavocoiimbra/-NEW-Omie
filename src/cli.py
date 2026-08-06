"""CLI: orquestra busca de títulos na Omie, enriquecimento e geração do relatório Excel."""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from datetime import datetime

from . import report_builder
from .config import ConfigError, carregar_config
from .enrichment import ClienteFornecedorCache, build_categoria_map, build_conta_corrente_map
from .excel_writer import escrever_relatorio
from .omie_client import OmieAPIError, OmieClient
from .titulos import LIMITE_REGISTROS_POR_PAGINA, STATUS_VALIDOS, buscar_titulos, contar_titulos

# A partir deste volume de títulos, avisamos que a execução deve demorar bastante
# (etapa de ConsultarCliente escala com o nº de clientes/fornecedores distintos).
_LIMIAR_AVISO_VOLUME = 1000

logger = logging.getLogger("cli")

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
        prog="relatorio-financeiro-omie",
        description="Gera relatório financeiro (contas a pagar e a receber) a partir da API da Omie.",
    )
    p.add_argument(
        "--data-inicio", default=None, type=_validar_data,
        help="Data inicial do filtro (dd/mm/aaaa). Se omitida junto com --data-fim, busca TODOS os títulos já lançados.",
    )
    p.add_argument(
        "--data-fim", default=None, type=_validar_data,
        help="Data final do filtro (dd/mm/aaaa). Se omitida junto com --data-inicio, busca TODOS os títulos já lançados.",
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
        help="P=somente a pagar, R=somente a receber, PR=ambos (padrão)",
    )
    p.add_argument("--status", choices=STATUS_VALIDOS, default=None, help="Filtrar por status do título (padrão: todos)")
    p.add_argument("--output", default=None, help="Caminho do arquivo .xlsx de saída")
    p.add_argument(
        "--registros-por-pagina", type=int, default=LIMITE_REGISTROS_POR_PAGINA,
        help=f"Registros por página nas chamadas à Omie (padrão e limite documentado: {LIMITE_REGISTROS_POR_PAGINA})",
    )
    p.add_argument(
        "--max-workers-clientes", type=int, default=4,
        help="Máximo de consultas ConsultarCliente em paralelo (padrão: 4, o limite de concorrência da Omie por método)",
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


def _naturezas(codigo: str) -> tuple[str, ...]:
    return ("P", "R") if codigo == "PR" else (codigo,)


def _tag_periodo(args: argparse.Namespace) -> str:
    if not args.data_inicio and not args.data_fim:
        return "todos_titulos_" + datetime.now().strftime("%Y%m%d-%H%M%S")
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

    output = args.output or os.path.join("output", f"relatorio_financeiro_{_tag_periodo(args)}.xlsx")
    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)

    client = OmieClient(
        app_key=config.app_key,
        app_secret=config.app_secret,
        max_req_por_segundo=config.max_req_por_segundo,
        debug_dir="debug_raw" if args.debug else None,
    )

    usar_cache_disco = not args.sem_cache_disco
    ttl_segundos = args.cache_ttl_horas * 3600

    try:
        naturezas = _naturezas(args.natureza)
        logger.info("Estimando volume de títulos antes de buscar...")
        contagem = contar_titulos(
            client, data_inicio=args.data_inicio, data_fim=args.data_fim,
            naturezas=naturezas, filtro_data=args.filtro_data, status=args.status,
        )
        total_estimado = sum(contagem.values())
        logger.info("Volume estimado: %s (total: %d)", contagem, total_estimado)

        if total_estimado > _LIMIAR_AVISO_VOLUME:
            logger.warning(
                "%d títulos estimados — bem acima do usual. A etapa de enriquecimento consulta "
                "cada cliente/fornecedor distinto (com cache e até %d em paralelo, mas ainda assim "
                "pode levar bastante tempo em contas com histórico grande). Use Ctrl+C para cancelar "
                "se não era essa a intenção.",
                total_estimado, args.max_workers_clientes,
            )
        elif not args.data_inicio and not args.data_fim:
            logger.warning("Nenhum filtro de data informado — buscando TODOS os títulos já lançados na conta.")

        logger.info(
            "Buscando títulos (%s a %s, filtro por %s, natureza=%s, status=%s)...",
            args.data_inicio or "sem limite inicial", args.data_fim or "sem limite final", args.filtro_data,
            args.natureza, args.status or "TODOS",
        )
        titulos_raw = buscar_titulos(
            client,
            data_inicio=args.data_inicio,
            data_fim=args.data_fim,
            naturezas=naturezas,
            filtro_data=args.filtro_data,
            status=args.status,
            registros_por_pagina=args.registros_por_pagina,
        )
        logger.info("Total de títulos encontrados: %d", len(titulos_raw))

        if not titulos_raw:
            logger.warning("Nenhum título encontrado para os filtros informados. Gerando relatório vazio.")

        logger.info("Carregando categorias e contas correntes para enriquecimento...")
        categoria_map = build_categoria_map(client, usar_cache=usar_cache_disco, ttl_segundos=ttl_segundos)
        cc_map = build_conta_corrente_map(client, usar_cache=usar_cache_disco, ttl_segundos=ttl_segundos)
        cliente_cache = ClienteFornecedorCache(
            client, max_workers=args.max_workers_clientes,
            usar_cache_disco=usar_cache_disco, ttl_segundos=ttl_segundos,
        )

        codigos_clientes = {
            (t.get("cabecTitulo") or {}).get("nCodCliente") for t in titulos_raw
        }
        cliente_cache.prefetch(codigos_clientes)

        logger.info("Montando linhas do relatório...")
        linhas = report_builder.montar_linhas(titulos_raw, categoria_map, cc_map, cliente_cache)
        cliente_cache.persistir()
        logger.info(
            "Clientes/fornecedores: %d distintos no relatório (%d consultados via API nesta execução, "
            "restante do cache local)",
            cliente_cache.total_em_cache, cliente_cache.total_consultas_rede,
        )

        resumo = report_builder.montar_resumo(linhas)
        df_geral = report_builder.montar_geral(linhas)
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
