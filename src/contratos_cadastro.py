"""Cadastro de contratos (`servicos/contrato`/`ListarContratos`) — catálogo
mestre usado por `contratos.py` para não depender só dos títulos que aparecem
numa janela do `ListarMovimentos` (contratos sem nenhum título faturado no
período consultado, ou cujos títulos foram lançados sem o campo `nCodCtr`,
ficavam invisíveis — ver investigação completa em
`sondas/PESQUISA_CONTRATOS_OS.md`).

`cCodSit` (status do cadastro do contrato) confirmado contra a conta real:
00 = Em elaboração, 10 = Ativo, 90 = Suspenso, 99 = Cancelado. Esse status é
o do *cadastro* do contrato na Omie — diverge com frequência do status
calculado a partir das parcelas reais (`contratos._status_contrato`): na
conta investigada, 20 dos 34 contratos com `cCodSit=10` (Ativo) já não
tinham nenhuma parcela em aberto/prevista. Por isso os dois status convivem
em `montar_contratos`, sem um substituir o outro.
"""
from __future__ import annotations

from typing import Any

from .omie_client import OmieClient

MODULO = "servicos/contrato"
CHAMADA = "ListarContratos"

_STATUS_CADASTRO = {
    "00": "Em elaboração",
    "10": "Ativo",
    "90": "Suspenso",
    "99": "Cancelado",
}


def _status_cadastro(cod_sit: str | None) -> str:
    return _STATUS_CADASTRO.get(cod_sit or "", f"Desconhecido ({cod_sit})")


def buscar_contratos_cadastro(
    client: OmieClient, registros_por_pagina: int = 100
) -> dict[int, dict[str, Any]]:
    """Busca todas as páginas de `ListarContratos` e devolve um dict
    `nCodCtr -> cabecalho resumido` (cNumCtr, nCodCli, cCodSit/status_cadastro,
    vigência, dia de faturamento e valor mensal cadastrado)."""
    contratos: dict[int, dict[str, Any]] = {}
    pagina = 1
    while True:
        resp = client.call(MODULO, CHAMADA, {"pagina": pagina, "registros_por_pagina": registros_por_pagina})
        lote = resp.get("contratoCadastro") or []
        for item in lote:
            cab = item.get("cabecalho") or {}
            cod_ctr = cab.get("nCodCtr")
            if not cod_ctr:
                continue
            contratos[cod_ctr] = {
                "nCodCtr": cod_ctr,
                "cNumCtr": cab.get("cNumCtr"),
                "nCodCli": cab.get("nCodCli"),
                "cCodSit": cab.get("cCodSit"),
                "status_cadastro": _status_cadastro(cab.get("cCodSit")),
                "dVigInicial": cab.get("dVigInicial"),
                "dVigFinal": cab.get("dVigFinal"),
                "nDiaFat": cab.get("nDiaFat"),
                "nValTotMes": cab.get("nValTotMes"),
            }
        total_paginas = resp.get("total_de_paginas", 1) or 1
        if pagina >= total_paginas or not lote:
            break
        pagina += 1
    return contratos
