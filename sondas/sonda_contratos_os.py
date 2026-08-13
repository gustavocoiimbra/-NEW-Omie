"""Sonda exploratória: chama ListarContratos (servicos/contrato), ListarOS
(servicos/os) e, opcionalmente, ConsultarContaReceber (financas/contareceber)
contra a conta real configurada em .env, e grava a resposta bruta em
sondas/output/ para inspeção manual.

Objetivo: descobrir empiricamente (não documentado com confiança suficiente
pela Omie em fontes públicas acessíveis deste ambiente) os nomes exatos dos
campos de resposta — em especial como um contrato (ListarContratos) se liga
a uma Ordem de Serviço (ListarOS) e como uma OS se liga a um título
financeiro já lançado (nCodTitulo) — para então redesenhar `src/contratos.py`
sobre uma fonte de verdade real (o cadastro de contratos), em vez de inferir
contratos apenas a partir dos títulos que aparecem numa janela de datas do
ListarMovimentos.

Uso:
    python sondas/sonda_contratos_os.py
    python sondas/sonda_contratos_os.py --cliente "ADSMAIS"
    python sondas/sonda_contratos_os.py --cliente "ADSMAIS" --titulo 123456

Cada chamada é gravada em sondas/output/<timestamp>_<nome>.json (pasta fora
do controle de versão — veja sondas/.gitignore). Nada é modificado na Omie:
só chamadas de leitura (Listar*/Consultar*).

ATENÇÃO: os nomes de módulo abaixo (`servicos/contrato`, `servicos/os`,
`financas/contareceber`) foram obtidos por busca pública (URLs indexadas do
Portal do Desenvolvedor da Omie), não por teste direto — este ambiente não
tem acesso de rede a developer.omie.com.br/app.omie.com.br para validar antes
de rodar. Se algum retornar erro "call inválida"/404, confira o nome exato
em https://developer.omie.com.br/service-list/ e ajuste as constantes MODULO_*
abaixo.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import ConfigError, carregar_config  # noqa: E402
from src.omie_client import OmieAPIError, OmieClient  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("sonda_contratos_os")

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")

MODULO_CONTRATO = "servicos/contrato"
CHAMADA_LISTAR_CONTRATOS = "ListarContratos"

MODULO_OS = "servicos/os"
CHAMADA_LISTAR_OS = "ListarOS"

MODULO_CONTA_RECEBER = "financas/contareceber"
CHAMADA_CONSULTAR_CONTA_RECEBER = "ConsultarContaReceber"

MODULO_CLIENTES = "geral/clientes"
CHAMADA_LISTAR_CLIENTES = "ListarClientes"


def _gravar(nome: str, dados: object) -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    caminho = os.path.join(OUTPUT_DIR, f"{ts}_{nome}.json")
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2, default=str)
    logger.info("Gravado: %s", caminho)
    return caminho


def _resumo_chaves(resp: dict) -> None:
    """Log das chaves de topo do JSON de resposta — primeiro passo pra descobrir
    o nome do campo que traz a lista (varia por endpoint, ex.: "clientes_cadastro",
    "categoria_cadastro")."""
    if isinstance(resp, dict):
        for chave, valor in resp.items():
            tipo = type(valor).__name__
            tamanho = f" (len={len(valor)})" if isinstance(valor, (list, dict)) else ""
            logger.info("  chave de topo: %-30s tipo=%-6s%s", chave, tipo, tamanho)


def sondar_clientes(client: OmieClient, filtro_nome: str | None) -> list[dict]:
    logger.info("=== ListarClientes (%s/%s) — 1 página, pra localizar nCodCliente ===", MODULO_CLIENTES, CHAMADA_LISTAR_CLIENTES)
    encontrados: list[dict] = []
    pagina = 1
    while True:
        resp = client.call(MODULO_CLIENTES, CHAMADA_LISTAR_CLIENTES, {"pagina": pagina, "registros_por_pagina": 100})
        if pagina == 1:
            _resumo_chaves(resp)
        lote = resp.get("clientes_cadastro") or []
        if filtro_nome:
            alvo = filtro_nome.strip().lower()
            encontrados.extend(
                c for c in lote
                if alvo in (c.get("nome_fantasia") or "").lower() or alvo in (c.get("razao_social") or "").lower()
            )
        total_paginas = resp.get("total_de_paginas", 1) or 1
        if pagina >= total_paginas or not lote:
            break
        pagina += 1

    if filtro_nome:
        logger.info("Clientes/fornecedores que batem com %r: %d", filtro_nome, len(encontrados))
        for c in encontrados:
            logger.info("  nCodCliente=%s razao_social=%s nome_fantasia=%s", c.get("codigo_cliente_omie"), c.get("razao_social"), c.get("nome_fantasia"))
    return encontrados


def sondar_contratos(client: OmieClient, paginas: int = 1) -> list[dict]:
    logger.info("=== ListarContratos (%s/%s) ===", MODULO_CONTRATO, CHAMADA_LISTAR_CONTRATOS)
    todos: list[dict] = []
    pagina = 1
    while pagina <= paginas:
        resp = client.call(MODULO_CONTRATO, CHAMADA_LISTAR_CONTRATOS, {"pagina": pagina, "registros_por_pagina": 20})
        _resumo_chaves(resp)
        _gravar(f"listar_contratos_pagina{pagina}", resp)
        # Nome do campo de lista ainda não confirmado — tenta os candidatos mais
        # prováveis pela convenção da Omie (Listar<Coisa> -> "<coisa>_cadastro"
        # ou "cadastros"); ajuste aqui assim que o campo real aparecer no dump.
        for candidato in ("cadastros", "contratoCadastro", "contrato_cadastro", "listaContratos"):
            if candidato in resp:
                todos.extend(resp[candidato] or [])
                break
        total_paginas = resp.get("total_de_paginas") or resp.get("nTotPaginas") or 1
        if pagina >= total_paginas:
            break
        pagina += 1
    return todos


def sondar_os(client: OmieClient, paginas: int = 1) -> list[dict]:
    logger.info("=== ListarOS (%s/%s) ===", MODULO_OS, CHAMADA_LISTAR_OS)
    todos: list[dict] = []
    pagina = 1
    while pagina <= paginas:
        resp = client.call(MODULO_OS, CHAMADA_LISTAR_OS, {
            "pagina": pagina, "registros_por_pagina": 20, "apenas_importado_api": "N",
        })
        _resumo_chaves(resp)
        _gravar(f"listar_os_pagina{pagina}", resp)
        for candidato in ("osCadastro", "os_cadastro", "cadastros", "listaOS"):
            if candidato in resp:
                todos.extend(resp[candidato] or [])
                break
        total_paginas = resp.get("total_de_paginas") or resp.get("nTotPaginas") or 1
        if pagina >= total_paginas:
            break
        pagina += 1
    return todos


def sondar_conta_receber(client: OmieClient, n_cod_titulo: int) -> dict:
    logger.info("=== ConsultarContaReceber (%s/%s) — nCodTitulo=%s ===", MODULO_CONTA_RECEBER, CHAMADA_CONSULTAR_CONTA_RECEBER, n_cod_titulo)
    resp = client.call(MODULO_CONTA_RECEBER, CHAMADA_CONSULTAR_CONTA_RECEBER, {"codigo_lancamento_omie": n_cod_titulo})
    _resumo_chaves(resp)
    _gravar(f"consultar_conta_receber_{n_cod_titulo}", resp)
    return resp


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cliente", default=None, help="Substring do nome fantasia/razão social pra localizar o cliente-alvo (ex.: ADSMAIS)")
    p.add_argument("--titulo", type=int, default=None, help="nCodTitulo pra sondar via ConsultarContaReceber")
    p.add_argument("--paginas-contratos", type=int, default=1, help="Quantas páginas de ListarContratos buscar (padrão: 1, só pra inspecionar o formato)")
    p.add_argument("--paginas-os", type=int, default=1, help="Quantas páginas de ListarOS buscar (padrão: 1)")
    p.add_argument("--env-file", default=None, help="Caminho customizado para o .env")
    args = p.parse_args()

    try:
        config = carregar_config(args.env_file)
    except ConfigError as exc:
        print(f"Erro de configuração: {exc}", file=sys.stderr)
        return 1

    client = OmieClient(app_key=config.app_key, app_secret=config.app_secret, max_req_por_segundo=config.max_req_por_segundo)

    try:
        clientes_alvo = sondar_clientes(client, args.cliente) if args.cliente else []
        contratos = sondar_contratos(client, paginas=args.paginas_contratos)
        ordens_servico = sondar_os(client, paginas=args.paginas_os)

        if clientes_alvo:
            codigos = {c.get("codigo_cliente_omie") for c in clientes_alvo}
            logger.info("--- Filtrando contratos/OS pelos nCodCliente encontrados: %s ---", codigos)
            for c in contratos:
                # Nome do campo do cliente dentro do contrato ainda não confirmado
                # (candidatos comuns: nCodCli, nCodCliente, dentro de um bloco
                # "cabecalho" ou "identificacao") — o dump completo tem a resposta.
                if any(str(c.get(campo)) in {str(x) for x in codigos} for campo in ("nCodCli", "nCodCliente")):
                    logger.info("  Contrato possivelmente do cliente-alvo: %s", json.dumps(c, ensure_ascii=False)[:300])
            for os_item in ordens_servico:
                if any(str(os_item.get(campo)) in {str(x) for x in codigos} for campo in ("nCodCli", "nCodCliente")):
                    logger.info("  OS possivelmente do cliente-alvo: %s", json.dumps(os_item, ensure_ascii=False)[:300])

        if args.titulo:
            sondar_conta_receber(client, args.titulo)

    except OmieAPIError as exc:
        print(f"Erro na API da Omie: {exc}", file=sys.stderr)
        print(
            "Se o erro for 'call inválida' ou 404, o nome do módulo/chamada usado "
            "no script pode estar diferente do real — confira em "
            "https://developer.omie.com.br/service-list/ e ajuste as constantes "
            "MODULO_*/CHAMADA_* no topo deste arquivo.",
            file=sys.stderr,
        )
        return 2

    print(f"\nDumps gravados em: {OUTPUT_DIR}")
    print("Envie o conteúdo desses arquivos (ou cole os trechos relevantes) de volta "
          "para eu confirmar os nomes de campo e finalizar a implementação de contratos.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
