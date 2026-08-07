"""Ponto de entrada do painel de contratos: busca títulos/previsões via
ListarMovimentos, agrupa por contrato (src/contratos.py) e grava um JSON
pronto para alimentar o dashboard (veja dashboard/).

Uso:
    python main_dashboard.py --data-inicio 01/02/2026 --data-fim 31/12/2026

Diferente de main.py/main_movimentos.py, não gera Excel — só o JSON de
contratos. Precisa do ListarMovimentos (não do PesquisarLancamentos) porque
só ele retorna as previsões futuras (PREVISAO_CONTRATO).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import date, datetime

from src.config import ConfigError, carregar_config
from src.contratos import montar_contratos
from src.enrichment import build_categoria_map, build_cliente_map, build_conta_corrente_map
from src.movimentos import buscar_movimentos
from src.omie_client import OmieAPIError, OmieClient

logger = logging.getLogger("dashboard")

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
        prog="painel-contratos-omie",
        description="Gera o JSON de contratos (parcelas + previsões) para o painel de visualização.",
    )
    p.add_argument(
        "--data-inicio", default=None, type=_validar_data,
        help="Início da janela de vencimento buscada (dd/mm/aaaa). Padrão: 6 meses atrás.",
    )
    p.add_argument(
        "--data-fim", default=None, type=_validar_data,
        help="Fim da janela de vencimento buscada (dd/mm/aaaa). Padrão: fim do ano corrente.",
    )
    p.add_argument("--output", default=os.path.join("output", "contratos.json"), help="Caminho do JSON de saída")
    p.add_argument("--sem-cache-disco", action="store_true", help="Desativa o cache local em disco (.cache/)")
    p.add_argument("--cache-ttl-horas", type=float, default=24.0, help="Validade (TTL) do cache local, em horas")
    p.add_argument("--env-file", default=None, help="Caminho customizado para o arquivo .env")
    return p.parse_args(argv)


def _janela_padrao() -> tuple[str, str]:
    hoje = date.today()
    mes_absoluto = hoje.year * 12 + (hoje.month - 1) - 6
    inicio = date(mes_absoluto // 12, mes_absoluto % 12 + 1, 1)
    fim = date(hoje.year, 12, 31)
    return inicio.strftime("%d/%m/%Y"), fim.strftime("%d/%m/%Y")


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

    padrao_inicio, padrao_fim = _janela_padrao()
    data_inicio = args.data_inicio or padrao_inicio
    data_fim = args.data_fim or padrao_fim

    client = OmieClient(
        app_key=config.app_key, app_secret=config.app_secret, max_req_por_segundo=config.max_req_por_segundo,
    )
    usar_cache_disco = not args.sem_cache_disco
    ttl_segundos = args.cache_ttl_horas * 3600

    try:
        logger.info("Buscando títulos e previsões (%s a %s)...", data_inicio, data_fim)
        titulos_raw = buscar_movimentos(client, data_inicio=data_inicio, data_fim=data_fim)
        logger.info("Total de registros: %d", len(titulos_raw))

        categoria_map = build_categoria_map(client, usar_cache=usar_cache_disco, ttl_segundos=ttl_segundos)
        cc_map = build_conta_corrente_map(client, usar_cache=usar_cache_disco, ttl_segundos=ttl_segundos)
        cliente_map = build_cliente_map(client, usar_cache=usar_cache_disco, ttl_segundos=ttl_segundos)

        contratos = montar_contratos(titulos_raw, categoria_map, cc_map, cliente_map)
        logger.info("Contratos identificados (com nCodCtr): %d", len(contratos))

    except OmieAPIError as exc:
        print(f"Erro na API da Omie: {exc}", file=sys.stderr)
        return 2

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(
            {"gerado_em": date.today().isoformat(), "periodo": {"inicio": data_inicio, "fim": data_fim}, "contratos": contratos},
            f, ensure_ascii=False, indent=2, default=str,
        )
    print(f"\nJSON de contratos gerado com sucesso: {args.output} ({len(contratos)} contratos)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
