"""Agrupa títulos e previsões (`ListarMovimentos`) por contrato (`nCodCtr`),
para alimentar o painel de visualização de contratos.

Só o `ListarMovimentos` expõe `PREVISAO_CONTRATO` — por isso este módulo
consome a saída de `movimentos.buscar_movimentos`, não `titulos.buscar_titulos`.

`nCodCtr`/`cNumCtr` é o mesmo valor em todas as parcelas de um contrato,
tanto nos títulos já lançados (`CONTA_A_PAGAR`/`CONTA_A_RECEBER`) quanto nas
previsões futuras (`PREVISAO_CONTRATO`) — confirmado empiricamente contra uma
conta real. Títulos sem `nCodCtr` (a maioria — despesas avulsas, notas
fiscais sem contrato formal) não são "contrato" e ficam de fora deste
agrupamento, exceto os poucos que a reconciliação heurística religar (veja
`_match_heuristico`).

Investigação completa (por que nem todo contrato aparece, o papel de
`ListarContratos`/`ListarOS`/`ConsultarContaReceber`) em
`sondas/PESQUISA_CONTRATOS_OS.md`. Duas lacunas confirmadas contra a conta
real motivaram as mudanças deste módulo:

1. Contratos cadastrados (`ListarContratos`) sem nenhum título com `nCodCtr`
   na janela consultada ficavam **totalmente ausentes** do relatório — agora
   `montar_contratos` recebe opcionalmente o cadastro
   (`contratos_cadastro.buscar_contratos_cadastro`) e inclui todo contrato
   cadastrado, mesmo sem nenhuma parcela casada.
2. Alguns títulos realmente faturados vêm sem `nCodCtr` (lançados por
   conciliação bancária manual, sem passar pelo módulo de contrato nem de
   OS) — `_match_heuristico` tenta religá-los ao contrato certo do mesmo
   cliente por proximidade de valor/data, sempre marcando o vínculo como
   `"heuristico"` (nunca mesclado silenciosamente como se fosse confirmado
   pela Omie).

Um título órfão sem candidato inequívoco não é simplesmente descartado: se
ele tem *alguma* característica de contrato (mesmo cliente de um contrato
cadastrado, natureza de receita) mas não passa nos critérios de
`_match_heuristico`, entra em `titulos_para_revisao` do(s) contrato(s)
candidato(s) — um sinal pra usuário avaliar manualmente e decidir se aprova
o vínculo, em vez de ficar invisível. Títulos `CANCELADO` órfãos são
ignorados nessa reconciliação (auto-ligação e sinal de revisão): um título
cancelado não representa nem um pagamento nem uma pendência real, então não
ajuda o usuário a decidir nada.

Caso à parte: uma parcela **já vinculada** a um contrato (confirmada pela
Omie ou religada por heurística) que aparece `CANCELADO` pode ter sido
substituída por um lançamento manual solto, sem que a Omie refaça o vínculo
formal — achado real (FISERV, `sondas/PESQUISA_CONTRATOS_OS.md`, seção 6/12).
`_buscar_substituto_cancelado` procura esse substituto entre os títulos
órfãos do mesmo cliente. Dois níveis de confiança:

- candidato achado por **valor exato**, já pago (`RECEBIDO`/`PAGO`), com a
  mesma categoria e os mesmos impostos retidos da parcela cancelada
  (`_substituto_e_confiavel`) — confiança alta o bastante pra **promover
  automaticamente** como uma parcela paga de verdade (`"vinculo":
  "substituto"`), contando em `resumo`/`status_contrato`/`valor_recorrente`
  como qualquer outra parcela paga. A parcela cancelada original nunca é
  alterada nem removida — só deixa de ser a única fonte de verdade daquele
  ciclo de cobrança;
- qualquer outro caso (achado só pela faixa de valor, ou exato mas sem
  bater categoria/impostos/status) fica como sinal em `titulos_para_revisao`
  — nunca mesclado automaticamente, precisa de aprovação manual.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .report_builder import _num, _parse_data

_STATUS_LABEL_NATUREZA = {"P": "Pagar", "R": "Receber"}


def _situacao_parcela(cabec: dict[str, Any]) -> str:
    """Classifica uma parcela (título ou previsão) num dos 5 estados que o
    painel exibe. Usa `cStatus` do `ListarMovimentos` como fonte de verdade
    (já vem calculado pela Omie) em vez de rederivar a partir de datas —
    mais simples e não diverge do que a própria Omie mostra.

    "Pago no prazo" vs. "com atraso" é a única derivação feita aqui: compara
    `dDtPagamento` com `dDtVenc` (a Omie não expõe esse diferencial pronto).
    """
    status = cabec.get("cStatus")
    if status == "PREVISAO":
        return "Previsto"
    if status == "CANCELADO":
        return "Cancelado"
    if status in ("PAGO", "RECEBIDO"):
        venc = _parse_data(cabec.get("dDtVenc"))
        pagamento = _parse_data(cabec.get("dDtPagamento"))
        if venc and pagamento and pagamento > venc:
            return "Pago com atraso"
        return "Pago no prazo"
    if status == "ATRASADO":
        return "Atrasado"
    return "Em aberto"  # "A VENCER", "VENCE HOJE" e quaisquer outros


def _status_contrato(parcelas: list[dict[str, Any]]) -> str:
    """Status agregado do contrato, por prioridade:

    1. "Em atraso" — tem ao menos uma parcela vencida e não paga.
    2. "Ativo" — não está em atraso e tem ao menos uma parcela futura,
       prevista ou já emitida e ainda não vencida (a Omie continua gerando
       cobrança para esse contrato). Nos dados reais, "Em aberto" sempre
       vem acompanhado de "Previsto" enquanto o contrato segue rodando —
       mas checar os dois evita classificar como "Encerrado" um contrato
       cuja última parcela emitida ainda nem venceu.
    3. "Cancelado" — todas as parcelas já lançadas (exclui previsões) estão
       canceladas, e não há parcela futura.
    4. "Encerrado" — nenhuma das situações acima: teve parcelas normais no
       passado, mas não há mais nada previsto (contrato parece ter parado).
    """
    situacoes = [p["situacao"] for p in parcelas]
    if "Atrasado" in situacoes:
        return "Em atraso"
    if "Previsto" in situacoes or "Em aberto" in situacoes:
        return "Ativo"
    if situacoes and all(s == "Cancelado" for s in situacoes):
        return "Cancelado"
    return "Encerrado"


_TOLERANCIA_VALOR_HEURISTICO = 0.05  # 5% — calibrado contra casos reais, ver PESQUISA_CONTRATOS_OS.md

# Teto de plausibilidade pro SINAL de revisão (não pra religação automática):
# além dessa distância do valor mensal cadastrado, um título não é "quase uma
# correspondência" — é outra cobrança do mesmo cliente sem relação com este
# contrato. Achado real que motivou o teto: sem ele, um título de R$ 30.000
# era sinalizado pra revisão de um contrato de R$ 15.000/mês (100% de
# diferença) só por ser do mesmo cliente — ver PESQUISA_CONTRATOS_OS.md,
# seção 11. Calibrado pela distribuição real: 18 dos 103 títulos então
# sinalizados estavam a até 10% de distância (os casos genuínos, tipo
# CERENSA/PLUGIN a 6.1-6.2%); poucos a mais até 30-50%; o resto (majoritário)
# passava de 75%, alguns acima de 1000% — claramente não relacionados.
_TOLERANCIA_VALOR_REVISAO_MAX = 0.50  # 50%


def _match_heuristico(cabec: dict[str, Any], candidatos: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Tenta religar um título sem `nCodCtr` a um contrato do cadastro do
    mesmo cliente. Só casa quando sobra **exatamente 1** candidato plausível
    depois de filtrar por:

    - `nCodOS` presente — o título precisa ter nascido de uma Ordem de
      Serviço de verdade (`cOrigem="VENR"`), a mesma origem de toda parcela
      já confirmada (`nCodCtr` direto) de qualquer contrato. Título
      `cOrigem="MANR"` (lançamento manual, sem OS/NF) nunca tem essa
      procedência, mesmo com valor idêntico — achado real: título
      `11066616943` do cliente FISERV batia o valor exato do contrato
      (R$ 35.000) e caía dentro da vigência, mas é `MANR` sem OS, e todo o
      resto da cobrança daquele contrato seguiu faturado normalmente pela
      via formal (OS/NF) sem nunca referenciar esse título — ou seja, é
      quase certo que não é uma parcela real do contrato, é um lançamento
      avulso que só coincide em valor. Ver `sondas/PESQUISA_CONTRATOS_OS.md`,
      seção 12. Título `MANR` plausível ainda pode entrar em
      `titulos_para_revisao` (`_candidatos_revisao`) — só não é
      auto-confirmado;
    - natureza "R" (contratos em `servicos/contrato` são sempre de receita —
      um título "P" do mesmo cliente é uma relação diferente, não o contrato);
    - vencimento dentro de `dVigInicial`..`dVigFinal` do contrato;
    - valor a até `_TOLERANCIA_VALOR_HEURISTICO` de distância de `nValTotMes`.

    0 ou 2+ candidatos plausíveis => ambíguo, não casa (fica órfão mesmo).
    """
    if cabec.get("cNatureza") != "R":
        return None
    if not cabec.get("nCodOS"):
        return None
    venc = _parse_data(cabec.get("dDtVenc"))
    valor = _num(cabec.get("nValorTitulo"))
    if venc is None or not valor:
        return None

    plausiveis = []
    for c in candidatos:
        inicio = _parse_data(c.get("dVigInicial"))
        fim = _parse_data(c.get("dVigFinal"))
        if inicio and venc < inicio:
            continue
        if fim and venc > fim:
            continue
        val_mes = _num(c.get("nValTotMes"))
        if not val_mes:
            continue
        if abs(valor - val_mes) / val_mes > _TOLERANCIA_VALOR_HEURISTICO:
            continue
        plausiveis.append(c)

    return plausiveis[0] if len(plausiveis) == 1 else None


def _candidatos_revisao(cabec: dict[str, Any], candidatos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filtra, dentre os contratos do mesmo cliente, só os plausíveis o
    bastante pro sinal de revisão manual: valor a até
    `_TOLERANCIA_VALOR_REVISAO_MAX` de `nValTotMes`. Sem esse teto, qualquer
    recebimento do cliente (por menor relação que tenha com o contrato)
    acabava sinalizado — não é esse o objetivo (ver constante acima)."""
    valor = _num(cabec.get("nValorTitulo"))
    if not valor:
        return []
    plausiveis = []
    for c in candidatos:
        val_mes = _num(c.get("nValTotMes"))
        if not val_mes:
            continue
        if abs(valor - val_mes) / val_mes <= _TOLERANCIA_VALOR_REVISAO_MAX:
            plausiveis.append(c)
    return plausiveis


def _revisao_motivo(cabec: dict[str, Any], candidato: dict[str, Any]) -> str:
    """Explica, em texto simples, por que um título do mesmo cliente de
    `candidato` não foi religado automaticamente por `_match_heuristico` —
    orienta o que o usuário precisa checar antes de aprovar o vínculo."""
    motivos = []
    if not cabec.get("nCodOS"):
        motivos.append("lançado sem Ordem de Serviço (não veio pela via formal de faturamento do contrato)")
    venc = _parse_data(cabec.get("dDtVenc"))
    inicio = _parse_data(candidato.get("dVigInicial"))
    fim = _parse_data(candidato.get("dVigFinal"))
    if venc and inicio and venc < inicio:
        motivos.append("vencimento antes do início da vigência do contrato")
    if venc and fim and venc > fim:
        motivos.append("vencimento depois do fim da vigência do contrato")
    valor = _num(cabec.get("nValorTitulo"))
    val_mes = _num(candidato.get("nValTotMes"))
    if valor and val_mes:
        diff_pct = abs(valor - val_mes) / val_mes * 100
        if diff_pct > _TOLERANCIA_VALOR_HEURISTICO * 100:
            motivos.append(f"valor {diff_pct:.1f}% diferente do valor mensal cadastrado do contrato")
    if not motivos:
        motivos.append("mais de um contrato do mesmo cliente é compatível com este título — ambíguo")
    return "; ".join(motivos)


# Janela de dias entre a emissão da parcela cancelada e a emissão do
# candidato a substituto. Calibrada contra o único par real confirmado
# (FISERV, ver PESQUISA_CONTRATOS_OS.md seção 6): título cancelado emitido
# 15/01/2026, substituto manual emitido 07/01/2026 — 8 dias de diferença.
# 30 dias dá folga generosa sem abrir demais (evita casar lançamentos de
# meses diferentes que só coincidem em valor).
_JANELA_DIAS_SUBSTITUTO_CANCELADO = 30

# Faixa de valor pro substituto quando não há correspondência exata: larga
# de propósito (pedido explícito do usuário) pra acomodar multa, desconto e
# encargos que mudam o valor final pago em relação ao valor original da
# parcela cancelada.
_FAIXA_VALOR_SUBSTITUTO_MIN = 0.5
_FAIXA_VALOR_SUBSTITUTO_MAX = 3.0


def _buscar_substituto_cancelado(
    cabec_cancelado: dict[str, Any],
    titulos_candidatos: list[dict[str, Any]],
    contrato: dict[str, Any] | None,
) -> tuple[dict[str, Any], bool] | None:
    """Quando uma parcela **já vinculada** a um contrato aparece `CANCELADO`,
    procura entre os títulos órfãos do mesmo cliente um lançamento manual que
    pareça ser o substituto real — a Omie não reformaliza esse tipo de
    correção (não recria o título com o mesmo `nCodCtr`), fica só um
    lançamento solto (`cOrigem="MANR"`).

    1. Não procura se o contrato já tinha `dVigFinal` antes do vencimento da
       parcela cancelada — a relação já tinha acabado, não faz sentido
       esperar um substituto depois disso.
    2. Candidato: mesmo cliente, `cOrigem="MANR"`, natureza "R", não
       cancelado, `dDtEmissao` a até `_JANELA_DIAS_SUBSTITUTO_CANCELADO` dias
       da emissão da parcela cancelada.
    3. Primeiro tenta valor **exato** (a até 1 centavo); só se não achar
       nenhum tenta a faixa [`_FAIXA_VALOR_SUBSTITUTO_MIN`x,
       `_FAIXA_VALOR_SUBSTITUTO_MAX`x] do valor da parcela cancelada.
    4. Só devolve se sobrar **exatamente 1** candidato em alguma das duas
       tentativas — ambíguo (0 ou 2+) não escolhe.

    Devolve `(titulo_candidato, achou_por_valor_exato)` — quem chama usa o
    booleano pra decidir se o candidato é forte o bastante pra promover a
    parcela paga de verdade (`_substituto_e_confiavel`) ou se fica só como
    sinal de revisão (o match por faixa nunca é forte o bastante sozinho).
    """
    venc_cancelada = _parse_data(cabec_cancelado.get("dDtVenc"))
    emissao_cancelada = _parse_data(cabec_cancelado.get("dDtEmissao"))
    valor_cancelada = _num(cabec_cancelado.get("nValorTitulo"))
    if emissao_cancelada is None or not valor_cancelada:
        return None

    fim_vigencia = _parse_data((contrato or {}).get("dVigFinal"))
    if fim_vigencia and venc_cancelada and fim_vigencia < venc_cancelada:
        return None

    na_janela: list[dict[str, Any]] = []
    for titulo in titulos_candidatos:
        cabec = titulo.get("cabecTitulo", {}) or {}
        if cabec.get("cOrigem") != "MANR":
            continue
        if cabec.get("cNatureza") != "R":
            continue
        if cabec.get("cStatus") == "CANCELADO":
            continue
        emissao = _parse_data(cabec.get("dDtEmissao"))
        if emissao is None:
            continue
        if abs((emissao - emissao_cancelada).days) > _JANELA_DIAS_SUBSTITUTO_CANCELADO:
            continue
        na_janela.append(titulo)

    def _valor(titulo: dict[str, Any]) -> float:
        return _num((titulo.get("cabecTitulo") or {}).get("nValorTitulo")) or 0.0

    exatos = [t for t in na_janela if abs(_valor(t) - valor_cancelada) < 0.01]
    if exatos:
        return (exatos[0], True) if len(exatos) == 1 else None

    minimo = valor_cancelada * _FAIXA_VALOR_SUBSTITUTO_MIN
    maximo = valor_cancelada * _FAIXA_VALOR_SUBSTITUTO_MAX
    na_faixa = [t for t in na_janela if minimo <= _valor(t) <= maximo]
    return (na_faixa[0], False) if len(na_faixa) == 1 else None


# Campos de imposto retido comparados entre a parcela cancelada e o
# candidato a substituto — corroborar por imposto é evidência bem mais forte
# que só o valor bruto bater, porque é uma coincidência muito mais difícil
# entre transações de fato diferentes (os valores de PIS/COFINS/CSLL/IR/ISS
# dependem da alíquota configurada na categoria E do valor da nota). Cada par
# é (campo do valor, campo do flag de retenção correspondente).
_CAMPOS_IMPOSTOS = (
    ("nValorPIS", "cRetPIS"),
    ("nValorCOFINS", "cRetCOFINS"),
    ("nValorCSLL", "cRetCSLL"),
    ("nValorIR", "cRetIR"),
    ("nValorISS", "cRetISS"),
    ("nValorINSS", "cRetINSS"),
)
_TOLERANCIA_IMPOSTO = 0.02  # 2 centavos de folga por arredondamento


def _valor_retido_comparavel(cabec: dict[str, Any], campo_valor: str, campo_flag: str) -> float:
    """Valor de uma retenção pra fins de comparação, zerado sempre que o
    flag correspondente (`cRetXXX`) não confirma explicitamente "S" — tanto
    "N" quanto o campo simplesmente ausente contam como "não retido" (mesma
    informação, é só como a Omie decidiu representar isso título a título).
    Achado real: título com `cRetISS="N"` ainda tinha `nValorISS=750`
    preenchido (valor de referência/calculado, não o que foi de fato
    retido) — comparar o valor bruto sem checar o flag gerava incompatível
    onde na verdade os dois lados diziam "sem ISS retido". Mesmo padrão já
    usado em `report_builder._num_retido` pro relatório DRE."""
    if cabec.get(campo_flag) != "S":
        return 0.0
    return _num(cabec.get(campo_valor))


def _impostos_compativeis(cabec_cancelado: dict[str, Any], cabec_candidato: dict[str, Any]) -> bool:
    """Compara os impostos efetivamente retidos entre os dois títulos, campo
    a campo (ver `_valor_retido_comparavel`) — os dois lados precisam bater
    dentro da tolerância. Sem retenção nenhuma dos dois lados também conta
    como compatível: é consistente, não é um sinal faltando."""
    for campo_valor, campo_flag in _CAMPOS_IMPOSTOS:
        v_cancelado = _valor_retido_comparavel(cabec_cancelado, campo_valor, campo_flag)
        v_candidato = _valor_retido_comparavel(cabec_candidato, campo_valor, campo_flag)
        if abs(v_cancelado - v_candidato) > _TOLERANCIA_IMPOSTO:
            return False
    return True


def _categoria_compativel(cabec_cancelado: dict[str, Any], cabec_candidato: dict[str, Any]) -> bool:
    """A categoria do candidato precisa ser a mesma da parcela cancelada —
    categorias diferentes indicam um tipo de cobrança diferente, mesmo que o
    valor bruto coincida."""
    cat_cancelado = cabec_cancelado.get("cCodCateg")
    return bool(cat_cancelado) and cat_cancelado == cabec_candidato.get("cCodCateg")


def _substituto_e_confiavel(cabec_cancelado: dict[str, Any], cabec_candidato: dict[str, Any]) -> bool:
    """Critério reforçado pra promover o candidato a substituto como uma
    parcela PAGA de verdade (em vez de só sinalizar pra revisão manual):
    além do valor bruto exato (já garantido por quem chama — só chamado
    quando `_buscar_substituto_cancelado` achou por valor exato, não por
    faixa), exige que o candidato já esteja efetivamente pago, que a
    categoria bata e que os impostos retidos batam. Só com todos os sinais
    reforçando ao mesmo tempo é seguro o bastante pra contar automaticamente
    — qualquer um desses faltando, fica como sinal de revisão (comportamento
    anterior, inalterado)."""
    if cabec_candidato.get("cStatus") not in ("RECEBIDO", "PAGO"):
        return False
    if not _categoria_compativel(cabec_cancelado, cabec_candidato):
        return False
    if not _impostos_compativeis(cabec_cancelado, cabec_candidato):
        return False
    return True


# Só busca pagamento avulso pra título ATRASADO há mais dias que isso — um
# atraso recente pode genuinamente ainda estar em aberto, sem nada pra achar;
# o pedido do usuário foi especificamente sobre atrasos "superior a 60 dias".
_DIAS_ATRASO_MINIMO_BUSCA = 60


def _categoria_dre_elegivel(categoria_map: dict[str, dict[str, str]], cod_categ: str) -> bool:
    """Uma categoria é elegível pro DRE quando não está marcada como
    não-exibida/transferência/totalizadora no cadastro Omie — mesmos três
    flags de `report_builder._dre_flag`, mas isolados da checagem de
    `cStatus`: naquela função todo título `ATRASADO` já dá "Não" só pelo
    status, o que não ajuda a diferenciar nada aqui (o ponto é justamente
    avaliar títulos atrasados)."""
    info = categoria_map.get(cod_categ) or {}
    return not (info.get("nao_exibir") == "S" or info.get("transferencia") == "S" or info.get("totalizadora") == "S")


def _valor_liquido(cabec: dict[str, Any]) -> float:
    """Valor líquido do título: bruto menos os impostos efetivamente
    retidos (mesma lógica de `_valor_retido_comparavel` — só desconta
    quando `cRetXXX="S"`). É o valor que de fato deveria ter circulado
    financeiramente depois da retenção."""
    bruto = _num(cabec.get("nValorTitulo"))
    retido = sum(
        _valor_retido_comparavel(cabec, campo_valor, campo_flag) for campo_valor, campo_flag in _CAMPOS_IMPOSTOS
    )
    return bruto - retido


def _buscar_pagamento_atrasado(
    cabec_atrasado: dict[str, Any],
    titulos_candidatos: list[dict[str, Any]],
    hoje: date,
) -> dict[str, Any] | None:
    """Título `ATRASADO` há mais de `_DIAS_ATRASO_MINIMO_BUSCA` dias pode já
    ter sido pago de fato via um lançamento avulso nunca religado ao título
    original — a baixa de uma transferência bancária, por exemplo, não fica
    automaticamente vinculada ao título em aberto que ela quita.

    Candidato: mesmo cliente, mesma natureza, não cancelado, lançado
    (`dDtEmissao`, ou `dDtVenc` se não houver emissão) **depois** do
    vencimento do título atrasado — e cujo valor bruto bate com o valor
    **líquido** do atrasado (`_valor_liquido`): uma transferência bancária
    tipicamente não traz quebra de imposto nenhuma no título (o valor
    lançado já é o que de fato circulou, sem retenção separada), então
    comparar contra o bruto do atrasado erraria por causa da retenção que
    só existe do lado do título formal.

    Só resolve com **exatamente 1** candidato — ambíguo não escolhe. Nunca
    promove automaticamente (só sinaliza, ver `montar_contratos`): diferente
    de uma parcela cancelada, um título atrasado pode genuinamente seguir em
    aberto — "achei um valor parecido lançado depois" é uma evidência mais
    fraca que "a parcela foi formalmente cancelada e apareceu um substituto".
    """
    venc_atrasado = _parse_data(cabec_atrasado.get("dDtVenc"))
    if venc_atrasado is None:
        return None
    if (hoje - venc_atrasado).days <= _DIAS_ATRASO_MINIMO_BUSCA:
        return None
    valor_liquido_atrasado = _valor_liquido(cabec_atrasado)
    if not valor_liquido_atrasado:
        return None

    candidatos = []
    for titulo in titulos_candidatos:
        cabec = titulo.get("cabecTitulo", {}) or {}
        if cabec.get("cNatureza") != cabec_atrasado.get("cNatureza"):
            continue
        if cabec.get("cStatus") == "CANCELADO":
            continue
        data_lancamento = _parse_data(cabec.get("dDtEmissao")) or _parse_data(cabec.get("dDtVenc"))
        if data_lancamento is None or data_lancamento <= venc_atrasado:
            continue
        valor_candidato = _num(cabec.get("nValorTitulo")) or 0.0
        if abs(valor_candidato - valor_liquido_atrasado) >= 0.01:
            continue
        candidatos.append(cabec)

    return candidatos[0] if len(candidatos) == 1 else None


def montar_contratos(
    titulos_raw: list[dict[str, Any]],
    categoria_map: dict[str, dict[str, str]],
    cc_map: dict[int, str],
    cliente_map: dict[int, dict[str, Any]],
    contratos_cadastro: dict[int, dict[str, Any]] | None = None,
    hoje: date | None = None,
) -> list[dict[str, Any]]:
    """Agrupa `titulos_raw` (saída de `movimentos.buscar_movimentos`) por
    `nCodCtr` e retorna uma lista de contratos, cada um com suas parcelas
    (passadas e futuras) e um status agregado.

    `contratos_cadastro` (opcional, saída de
    `contratos_cadastro.buscar_contratos_cadastro`) é o catálogo mestre de
    `ListarContratos`. Quando informado:

    - todo contrato cadastrado entra no resultado, mesmo sem nenhum título
      casado (parcelas=[], status vem do cadastro — veja `status_cadastro`);
    - títulos sem `nCodCtr` passam por `_match_heuristico` antes de serem
      descartados, religando ao contrato certo quando não há ambiguidade E o
      título tem `nCodOS` (nasceu de uma Ordem de Serviço real — mesma
      procedência de qualquer parcela já confirmada; título lançado manual,
      sem OS, nunca é auto-ligado mesmo com valor idêntico, ver
      `_match_heuristico`) — marcados com `"vinculo": "heuristico"` em vez de
      `"confirmado"`;
    - parcela **já vinculada** (confirmada ou heurística) que aparece
      `CANCELADO` passa por `_buscar_substituto_cancelado`: se achar um
      lançamento manual plausível e inequívoco do mesmo cliente (mesmo valor,
      ou dentro de 0,5x–3x se não achar valor exato, dentro de uma janela de
      dias da emissão) **e** for forte o bastante (valor exato + já pago +
      categoria e impostos retidos batendo — `_substituto_e_confiavel`), o
      substituto é **promovido** direto como parcela (`"vinculo":
      "substituto"`), contando em `resumo`/`status_contrato`. Caso contrário
      (achado só pela faixa, ou exato mas sem bater os outros sinais), a
      parcela cancelada ganha `"substituto_sugerido"` e o candidato entra em
      `titulos_para_revisao` com o motivo explícito de qual cancelamento ele
      provavelmente cobre — sem contar em nada até aprovação manual;
    - parcela **já vinculada** com situação `ATRASADO` há mais de
      `_DIAS_ATRASO_MINIMO_BUSCA` (60) dias passa por
      `_buscar_pagamento_atrasado`: procura um lançamento avulso do mesmo
      cliente, lançado depois do vencimento, cujo valor bruto bata com o
      valor **líquido** do atrasado (bruto menos impostos efetivamente
      retidos — uma transferência bancária tipicamente não traz quebra de
      imposto no título). Só sinaliza (`titulos_para_revisao`, com a
      categoria do atrasado marcada como elegível ou não pro DRE no motivo)
      — **nunca promove**: um título atrasado pode genuinamente seguir em
      aberto, então essa evidência é mais fraca que a de um cancelamento;
    - o que sobrar (nem auto-ligado, nem usado como substituto) mas ainda é
      plausível (mesmo cliente de algum contrato cadastrado, valor a até
      `_TOLERANCIA_VALOR_REVISAO_MAX` do valor mensal) entra em
      `"titulos_para_revisao"` desse(s) contrato(s) — não conta em
      `resumo`/`status_contrato`/`valor_recorrente`, é só um sinal pra
      avaliação manual. Um título do mesmo cliente com valor muito distante
      não é um "quase bateu", é outra cobrança sem relação — não entra nem
      aqui. Títulos `CANCELADO` órfãos nunca entram nessa reconciliação (nem
      auto-ligação, nem sinal de revisão, nem servem de substituto).

    Sem `contratos_cadastro`, o comportamento é o original: só contratos com
    ao menos um título trazendo `nCodCtr` aparecem, e `titulos_para_revisao`
    vem sempre vazio.
    """
    hoje = hoje or date.today()
    from .report_builder import _categoria_descricao  # import local: evita ciclo no topo

    contratos_cadastro = contratos_cadastro or {}
    candidatos_por_cliente: dict[Any, list[dict[str, Any]]] = {}
    for cadastro in contratos_cadastro.values():
        candidatos_por_cliente.setdefault(cadastro.get("nCodCli"), []).append(cadastro)

    grupos: dict[Any, list[tuple[dict[str, Any], str]]] = {}
    orfaos: list[dict[str, Any]] = []
    for titulo in titulos_raw:
        cabec = titulo.get("cabecTitulo", {}) or {}
        cod_ctr = cabec.get("nCodCtr")
        if cod_ctr:
            grupos.setdefault(cod_ctr, []).append((titulo, "confirmado"))
        else:
            orfaos.append(titulo)

    titulos_revisao_por_ctr: dict[Any, list[dict[str, Any]]] = {}

    # Passo 1: tenta auto-ligar cada órfão não cancelado (ver
    # _match_heuristico). O que sobrar (não ligado) fica disponível como pool
    # de candidatos pros dois passos seguintes.
    orfaos_nao_ligados: list[dict[str, Any]] = []
    for titulo in orfaos:
        cabec = titulo.get("cabecTitulo", {}) or {}
        if cabec.get("cStatus") == "CANCELADO":
            # Cancelado nao representa pagamento nem pendencia real -- nao
            # ajuda a decidir nada, nem auto-liga nem sinaliza pra revisao,
            # nem serve de candidato a substituto de outra coisa.
            continue
        candidatos = candidatos_por_cliente.get(cabec.get("nCodCliente"), [])
        alvo = _match_heuristico(cabec, candidatos)
        if alvo is not None:
            grupos.setdefault(alvo["nCodCtr"], []).append((titulo, "heuristico"))
        else:
            orfaos_nao_ligados.append(titulo)

    # Passo 2: pra cada parcela JÁ VINCULADA (confirmada ou religada) que
    # precisa de reconciliação, procura um candidato entre os órfãos ainda
    # não ligados do mesmo cliente. Dois casos:
    #
    # - CANCELADO: pode ter sido substituído por um lançamento avulso (ver
    #   _buscar_substituto_cancelado). Candidato forte o bastante (valor
    #   exato + já pago + categoria e impostos batendo — ver
    #   _substituto_e_confiavel) é promovido direto como parcela paga.
    # - ATRASADO há mais de _DIAS_ATRASO_MINIMO_BUSCA dias: pode já ter sido
    #   pago via um lançamento avulso nunca religado (ver
    #   _buscar_pagamento_atrasado) — comparando o valor LÍQUIDO do atrasado
    #   contra o bruto do candidato. Nunca promove (evidência mais fraca que
    #   o caso de cancelamento — um atrasado pode genuinamente seguir em
    #   aberto), só sinaliza.
    #
    # Cada órfão só pode ser sugerido pra uma parcela — uma vez usado, sai
    # do pool pra não ser sugerido de novo (nem pra outra parcela aqui, nem
    # pro sinal genérico do passo 3).
    substitutos_por_titulo: dict[Any, dict[str, Any]] = {}
    titulos_consumidos_como_substituto: set[Any] = set()
    for cod_ctr, titulos_do_contrato in grupos.items():
        contrato_cad = contratos_cadastro.get(cod_ctr)
        promovidos: list[tuple[dict[str, Any], str]] = []
        for titulo, _vinculo in titulos_do_contrato:
            cabec = titulo.get("cabecTitulo", {}) or {}
            status = cabec.get("cStatus")
            if status not in ("CANCELADO", "ATRASADO"):
                continue
            cod_cliente_ctr = cabec.get("nCodCliente")
            candidatos_orfaos = [
                t for t in orfaos_nao_ligados
                if (t.get("cabecTitulo") or {}).get("nCodCliente") == cod_cliente_ctr
                and (t.get("cabecTitulo") or {}).get("nCodTitulo") not in titulos_consumidos_como_substituto
            ]

            if status == "CANCELADO":
                achado = _buscar_substituto_cancelado(cabec, candidatos_orfaos, contrato_cad)
                if achado is None:
                    continue
                titulo_substituto, achou_exato = achado
                cabec_substituto = titulo_substituto.get("cabecTitulo", {}) or {}
                titulos_consumidos_como_substituto.add(cabec_substituto.get("nCodTitulo"))

                promovido = achou_exato and _substituto_e_confiavel(cabec, cabec_substituto)
                if promovido:
                    # Confiança alta o bastante (valor exato + pago +
                    # categoria e impostos batendo) -- entra como parcela de
                    # verdade, com vinculo proprio pra ficar rastreavel que
                    # veio de inferencia e nao de nCodCtr/heuristica. Nunca
                    # mexe na parcela cancelada em si (ela continua
                    # Cancelado, so deixa de ser a unica fonte de verdade
                    # daquele ciclo de cobranca).
                    promovidos.append((titulo_substituto, "substituto"))
                else:
                    titulos_revisao_por_ctr.setdefault(cod_ctr, []).append({
                        "nCodTitulo": cabec_substituto.get("nCodTitulo"),
                        "vencimento": cabec_substituto.get("dDtVenc"),
                        "valor": _num(cabec_substituto.get("nValorTitulo")),
                        "status_omie": cabec_substituto.get("cStatus"),
                        "motivo": (
                            f"possível substituto da parcela cancelada {cabec.get('nCodTitulo')} "
                            f"(mesmo cliente, lançamento manual próximo em data/valor)"
                        ),
                    })
                # Anota na parcela cancelada em ambos os casos -- so muda a
                # confianca (`promovido`); quem consome os dados decide o
                # que fazer com um substituto so sinalizado vs. ja
                # contabilizado.
                substitutos_por_titulo[cabec.get("nCodTitulo")] = {"cabec": cabec_substituto, "promovido": promovido}

            else:  # ATRASADO há mais de _DIAS_ATRASO_MINIMO_BUSCA dias
                cabec_pagamento = _buscar_pagamento_atrasado(cabec, candidatos_orfaos, hoje)
                if cabec_pagamento is None:
                    continue
                titulos_consumidos_como_substituto.add(cabec_pagamento.get("nCodTitulo"))
                dre_elegivel = _categoria_dre_elegivel(categoria_map, cabec.get("cCodCateg", ""))
                titulos_revisao_por_ctr.setdefault(cod_ctr, []).append({
                    "nCodTitulo": cabec_pagamento.get("nCodTitulo"),
                    "vencimento": cabec_pagamento.get("dDtVenc"),
                    "valor": _num(cabec_pagamento.get("nValorTitulo")),
                    "status_omie": cabec_pagamento.get("cStatus"),
                    "motivo": (
                        f"possível pagamento avulso do título atrasado há mais de "
                        f"{_DIAS_ATRASO_MINIMO_BUSCA} dias {cabec.get('nCodTitulo')} (valor bate com o líquido "
                        f"do título atrasado; categoria do título atrasado "
                        f"{'é' if dre_elegivel else 'não é'} destinada ao DRE)"
                    ),
                })
                # Nunca promove -- so sinaliza (ver _buscar_pagamento_atrasado).
                substitutos_por_titulo[cabec.get("nCodTitulo")] = {"cabec": cabec_pagamento, "promovido": False}
        titulos_do_contrato.extend(promovidos)

    # Passo 3: o que sobrar (nem auto-ligado, nem usado como substituto) mas
    # ainda tem cara de contrato (mesmo cliente de um contrato cadastrado,
    # valor plausível) vira sinal de revisão genérico — ver
    # _TOLERANCIA_VALOR_REVISAO_MAX.
    for titulo in orfaos_nao_ligados:
        cabec = titulo.get("cabecTitulo", {}) or {}
        if cabec.get("nCodTitulo") in titulos_consumidos_como_substituto:
            continue
        candidatos = candidatos_por_cliente.get(cabec.get("nCodCliente"), [])
        plausiveis = _candidatos_revisao(cabec, candidatos)
        for candidato in plausiveis:
            titulos_revisao_por_ctr.setdefault(candidato["nCodCtr"], []).append({
                "nCodTitulo": cabec.get("nCodTitulo"),
                "vencimento": cabec.get("dDtVenc"),
                "valor": _num(cabec.get("nValorTitulo")),
                "status_omie": cabec.get("cStatus"),
                "motivo": _revisao_motivo(cabec, candidato),
            })

    # Todo contrato do cadastro entra no resultado, mesmo sem nenhum título
    # casado — antes ficava totalmente ausente do relatório.
    for cod_ctr in contratos_cadastro:
        grupos.setdefault(cod_ctr, [])

    contratos: list[dict[str, Any]] = []
    for cod_ctr, titulos_do_contrato in grupos.items():
        cadastro = contratos_cadastro.get(cod_ctr)
        titulos_do_contrato.sort(
            key=lambda par: _parse_data((par[0].get("cabecTitulo") or {}).get("dDtVenc")) or hoje
        )

        parcelas: list[dict[str, Any]] = []
        for titulo, vinculo in titulos_do_contrato:
            cabec = titulo.get("cabecTitulo", {}) or {}
            parcela = {
                "nCodTitulo": cabec.get("nCodTitulo"),
                "vencimento": cabec.get("dDtVenc"),
                "pagamento": cabec.get("dDtPagamento") or None,
                "valor": _num(cabec.get("nValorTitulo")),
                "status_omie": cabec.get("cStatus"),
                "situacao": _situacao_parcela(cabec),
                "vinculo": vinculo,
            }
            info_substituto = substitutos_por_titulo.get(cabec.get("nCodTitulo"))
            if info_substituto is not None:
                substituto = info_substituto["cabec"]
                parcela["substituto_sugerido"] = {
                    "nCodTitulo": substituto.get("nCodTitulo"),
                    "vencimento": substituto.get("dDtVenc"),
                    "valor": _num(substituto.get("nValorTitulo")),
                    "status_omie": substituto.get("cStatus"),
                    # True: substituto ja entrou como parcela "substituto" em
                    # `parcelas` (conta em resumo/status). False: so sinal em
                    # titulos_para_revisao, precisa aprovacao manual.
                    "promovido": info_substituto["promovido"],
                }
            parcelas.append(parcela)

        if titulos_do_contrato:
            primeiro_cabec = titulos_do_contrato[0][0].get("cabecTitulo", {}) or {}
            cod_categ = primeiro_cabec.get("cCodCateg", "")
            natureza = primeiro_cabec.get("cNatureza", "")
            cod_cc = primeiro_cabec.get("nCodCC")
            num_ctr = primeiro_cabec.get("cNumCtr") or (cadastro or {}).get("cNumCtr")
        else:
            # Contrato só existe no cadastro — nenhum título casado (nem
            # confirmado, nem heurístico) para inferir esses campos.
            cod_categ = ""
            natureza = ""
            cod_cc = None
            num_ctr = (cadastro or {}).get("cNumCtr")

        # Cliente: prioriza o cadastro do contrato (`nCodCli` de
        # ListarContratos) em vez de "o cliente do primeiro título por
        # vencimento" — caso real confirmado: o título mais antigo de um
        # contrato pode ter sido lançado com o cliente errado (erro de
        # digitação corrigido nas parcelas seguintes, nunca corrigido nessa
        # parcela cancelada) e distorcia o nome exibido do contrato inteiro.
        # Ver sondas/PESQUISA_CONTRATOS_OS.md, seção 5.
        if cadastro and cadastro.get("nCodCli"):
            cod_cliente = cadastro["nCodCli"]
        elif titulos_do_contrato:
            cod_cliente = titulos_do_contrato[0][0].get("cabecTitulo", {}).get("nCodCliente")
        else:
            cod_cliente = None

        cad = cliente_map.get(cod_cliente) or {}

        # Valor recorrente = valor da parcela mais recente não cancelada
        # (previsão se houver, senão a última realizada) — a maioria dos
        # contratos tem valor constante entre parcelas, mas usar a mais
        # recente cobre reajustes. Sem nenhuma parcela, cai para o valor
        # mensal cadastrado no contrato.
        nao_canceladas = [p for p in parcelas if p["situacao"] != "Cancelado"]
        if nao_canceladas:
            valor_recorrente = nao_canceladas[-1]["valor"]
        elif parcelas:
            valor_recorrente = parcelas[-1]["valor"]
        else:
            valor_recorrente = _num((cadastro or {}).get("nValTotMes"))

        # Inclui "Atrasado": se a única parcela pendente já venceu, ela é o
        # próximo pagamento a acompanhar — deixar `proxima_parcela` em branco
        # nesse caso escondia exatamente os contratos que mais precisam de
        # atenção (ex.: um contrato "Em atraso" com uma única parcela vencida
        # ficava sem nenhuma parcela sinalizada aqui).
        proximas = [p for p in parcelas if p["situacao"] in ("Previsto", "Em aberto", "Atrasado")]
        proxima_parcela = min(proximas, key=lambda p: _parse_data(p["vencimento"]) or hoje) if proximas else None

        resumo = {
            "total_parcelas": len(parcelas),
            "pagas_no_prazo": sum(1 for p in parcelas if p["situacao"] == "Pago no prazo"),
            "pagas_com_atraso": sum(1 for p in parcelas if p["situacao"] == "Pago com atraso"),
            "em_aberto": sum(1 for p in parcelas if p["situacao"] == "Em aberto"),
            "atrasadas": sum(1 for p in parcelas if p["situacao"] == "Atrasado"),
            "previstas": sum(1 for p in parcelas if p["situacao"] == "Previsto"),
            "canceladas": sum(1 for p in parcelas if p["situacao"] == "Cancelado"),
        }

        # `status_cadastro` (de ListarContratos) e o status calculado a partir
        # das parcelas divergem com frequência (contrato "Ativo" no cadastro
        # mas sem nada em aberto/previsto há meses) — nenhum substitui o
        # outro. Sem nenhuma parcela, o cadastro é o único sinal disponível.
        status_cadastro = (cadastro or {}).get("status_cadastro")
        if parcelas or not status_cadastro:
            status_contrato = _status_contrato(parcelas)
            origem_status = "parcelas"
        else:
            status_contrato = status_cadastro
            origem_status = "cadastro"

        contratos.append(
            {
                "nCodCtr": cod_ctr,
                "cNumCtr": num_ctr,
                "cliente": cad.get("nome_fantasia") or cad.get("razao_social") or "",
                "natureza": _STATUS_LABEL_NATUREZA.get(natureza, natureza),
                "categoria": _categoria_descricao(categoria_map, cod_categ) if cod_categ else "",
                "conta_corrente": cc_map.get(cod_cc, "") if cod_cc else "",
                "valor_recorrente": valor_recorrente,
                "status_contrato": status_contrato,
                "status_cadastro": status_cadastro,
                "origem_status": origem_status,
                "proxima_parcela": proxima_parcela,
                "resumo": resumo,
                "parcelas": parcelas,
                "titulos_para_revisao": titulos_revisao_por_ctr.get(cod_ctr, []),
            }
        )

    contratos.sort(key=lambda c: (c["status_contrato"] != "Em atraso", c["cliente"]))
    return contratos
