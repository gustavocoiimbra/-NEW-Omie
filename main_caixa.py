"""Ponto de entrada do saldo de caixa: busca o extrato das contas operacionais
configuradas em `OMIE_CONTAS_CAIXA` (`.env`) via `ListarExtrato`
(src/extrato.py) e grava um JSON com o saldo real, os lançamentos previstos
"a vencer" e o fluxo semanal projetado.

Uso:
    python main_caixa.py --semanas 12
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date

from src.config import ConfigError, carregar_config
from src.enrichment import build_conta_corrente_map
from src.extrato import montar_saldo_caixa
from src.omie_client import OmieAPIError, OmieClient

logger = logging.getLogger("caixa")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="saldo-caixa-omie",
        description="Gera o JSON de saldo de caixa (real + previsto por semana) das contas configuradas.",
    )
    p.add_argument("--semanas", type=int, default=12, help="Quantas semanas à frente projetar (padrão: 12)")
    p.add_argument("--output", default=os.path.join("output", "caixa.json"), help="Caminho do JSON de saída")
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
    dias_previsao = args.semanas * 7

    try:
        cc_map = build_conta_corrente_map(client, usar_cache=usar_cache_disco, ttl_segundos=ttl_segundos)
        logger.info("Consultando saldo de caixa (%d semanas à frente)...", args.semanas)
        saldo = montar_saldo_caixa(client, cc_map, config.contas_caixa, dias_previsao=dias_previsao)
        logger.info(
            "Saldo real: R$ %.2f | Previsto (%d lançamentos): R$ %.2f | Saldo previsto: R$ %.2f",
            saldo["saldo_real_total"], len(saldo["lancamentos_previstos"]), saldo["total_previsto"],
            saldo["saldo_previsto_total"],
        )
    except (OmieAPIError, ValueError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 2

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump({"gerado_em": date.today().isoformat(), **saldo}, f, ensure_ascii=False, indent=2, default=str)

    print(f"\nJSON de saldo de caixa gerado com sucesso: {args.output}")
    print(f"  Saldo real (hoje): R$ {saldo['saldo_real_total']:,.2f}")
    print(f"  Total previsto ({len(saldo['lancamentos_previstos'])} lançamentos): R$ {saldo['total_previsto']:,.2f}")
    print(f"  Saldo previsto: R$ {saldo['saldo_previsto_total']:,.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
