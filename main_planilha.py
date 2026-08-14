"""Ponto de entrada: mantém uma planilha ÚNICA e ESTÁVEL (mesmo caminho a
cada execução) com o snapshot mais atual das movimentações financeiras
(títulos + previsões de contrato), no layout "Geral"/bdContas — para usar
como fonte viva em vez de reexportar manualmente da tela da Omie.

Diferente de main.py/main_movimentos.py (relatório completo, multi-aba, nome
carimbado por período/timestamp), este script:
- grava só a aba de movimentações (equivalente à aba "Geral"/bdContas) — sem
  Resumo/Por Status/Por Categoria/Fluxo Mensal/etc.;
- por padrão sempre grava no MESMO arquivo (`output/movimentacoes_atualizado.xlsx`),
  **sobrescrevendo o conteúdo anterior por completo** a cada execução
  (snapshot, não incremental — qualquer edição manual feita direto na
  planilha é perdida na próxima execução). Rode de novo quando quiser os
  dados mais recentes.

Fonte: `ListarMovimentos` (`financas/mf`) via `src/movimentos.py` — mesma
usada por `main_movimentos.py`/`main_dashboard.py`, inclui previsões de
contrato (`PREVISAO_CONTRATO`) que `PesquisarLancamentos` não traz.

Uso:
    python main_planilha.py
    python main_planilha.py --data-inicio 01/01/2026 --data-fim 31/12/2026
    python main_planilha.py --output minha_planilha.xlsx
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from datetime import datetime

from src.config import ConfigError, carregar_config
from src.enrichment import build_categoria_map, build_cliente_map, build_conta_corrente_map
from src.excel_writer import escrever_planilha_movimentacoes
from src.movimentos import buscar_movimentos, contar_movimentos
from src.omie_client import OmieAPIError, OmieClient
from src import report_builder

logger = logging.getLogger("planilha")

_DATA_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
_LIMIAR_AVISO_VOLUME = 1000


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
        prog="planilha-movimentacoes-omie",
        description="Atualiza (sobrescreve) uma planilha fixa com o snapshot atual das movimentações financeiras da Omie.",
    )
    p.add_argument(
        "--data-inicio", default=None, type=_validar_data,
        help="Início da janela de vencimento buscada (dd/mm/aaaa). Padrão: sem filtro (histórico completo).",
    )
    p.add_argument(
        "--data-fim", default=None, type=_validar_data,
        help="Fim da janela de vencimento buscada (dd/mm/aaaa). Padrão: sem filtro (histórico completo).",
    )
    p.add_argument(
        "--output", default=os.path.join("output", "movimentacoes_atualizado.xlsx"),
        help="Caminho da planilha (sempre sobrescrita por completo a cada execução)",
    )
    p.add_argument("--sem-cache-disco", action="store_true", help="Desativa o cache local em disco (.cache/)")
    p.add_argument("--cache-ttl-horas", type=float, default=24.0, help="Validade (TTL) do cache local, em horas")
    p.add_argument("--env-file", default=None, help="Caminho customizado para o arquivo .env")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    args = _parse_args(argv)

    if bool(args.data_inicio) != bool(args.data_fim):
        print(
            "Erro: --data-inicio e --data-fim precisam ser usados juntos (ou nenhum dos dois).",
            file=sys.stderr,
        )
        return 1

    try:
        config = carregar_config(args.env_file)
    except ConfigError as exc:
        print(f"Erro de configuração: {exc}", file=sys.stderr)
        return 1

    client = OmieClient(
        app_key=config.app_key, app_secret=config.app_secret, max_req_por_segundo=config.max_req_por_segundo,
    )
    usar_cache_disco = not args.sem_cache_disco
    ttl_segundos = args.cache_ttl_horas * 3600

    try:
        logger.info("Estimando volume antes de buscar...")
        total_estimado = contar_movimentos(client, data_inicio=args.data_inicio, data_fim=args.data_fim)
        logger.info("Volume estimado: %d movimentos", total_estimado)
        if total_estimado > _LIMIAR_AVISO_VOLUME:
            logger.warning("%d movimentos estimados — a busca pode demorar bastante.", total_estimado)
        elif not args.data_inicio and not args.data_fim:
            logger.info("Nenhum filtro de data informado — buscando TODOS os movimentos já lançados na conta.")

        logger.info(
            "Buscando movimentações (%s)...",
            f"{args.data_inicio} a {args.data_fim}" if args.data_inicio else "sem filtro de data — histórico completo",
        )
        titulos_raw = buscar_movimentos(client, data_inicio=args.data_inicio, data_fim=args.data_fim)
        logger.info("Total de movimentações encontradas: %d", len(titulos_raw))

        logger.info("Carregando categorias, contas correntes e clientes/fornecedores...")
        categoria_map = build_categoria_map(client, usar_cache=usar_cache_disco, ttl_segundos=ttl_segundos)
        cc_map = build_conta_corrente_map(client, usar_cache=usar_cache_disco, ttl_segundos=ttl_segundos)
        cliente_map = build_cliente_map(client, usar_cache=usar_cache_disco, ttl_segundos=ttl_segundos)

        df_geral = report_builder.montar_geral(titulos_raw, categoria_map, cc_map, cliente_map)

    except OmieAPIError as exc:
        print(f"Erro na API da Omie: {exc}", file=sys.stderr)
        return 2

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    logger.info("Gravando planilha em %s ...", args.output)
    escrever_planilha_movimentacoes(args.output, df_geral)

    print(f"\nPlanilha atualizada: {args.output} ({len(df_geral)} movimentações)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
