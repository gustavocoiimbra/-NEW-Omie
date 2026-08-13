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
# além dessa distância do valor de referência do contrato (ver
# _valor_referencia_contrato — nValTotMes cadastrado na época deste achado),
# um título não é "quase uma correspondência" — é outra cobrança do mesmo
# cliente sem relação com este contrato. Achado real que motivou o teto: sem
# ele, um título de R$ 30.000
# era sinalizado pra revisão de um contrato de R$ 15.000/mês (100% de
# diferença) só por ser do mesmo cliente — ver PESQUISA_CONTRATOS_OS.md,
# seção 11. Calibrado pela distribuição real: 18 dos 103 títulos então
# sinalizados estavam a até 10% de distância (os casos genuínos, tipo
# CERENSA/PLUGIN a 6.1-6.2%); poucos a mais até 30-50%; o resto (majoritário)
# passava de 75%, alguns acima de 1000% — claramente não relacionados.
_TOLERANCIA_VALOR_REVISAO_MAX = 0.50  # 50%

_K_VIZINHOS_VALOR_REFERENCIA = 3  # média das até 3 parcelas confirmadas mais
# próximas em data -- K=1 é frágil a uma parcela atípica isolada (desconto/
# rateio pontual); K=3 suaviza sem arriscar misturar lados de um reajuste, já
# que são as 3 mais próximas EM DATA, não 3 quaisquer. Mediana real de
# parcelas por contrato é 8 (mín. 0, 8 contratos com <=2) -- degrada
# graciosamente pra média de 1 ou 2 quando não há 3 disponíveis.

_MIN_PARCELAS_VALOR_REFERENCIA = 2  # ver docstring de _valor_referencia_contrato


def _valor_referencia_contrato(
    candidato: dict[str, Any],
    parcelas_confirmadas: list[dict[str, Any]],
    venc_titulo: date | None,
) -> float:
    """Valor de referência pra comparar um título órfão: média das até
    `_K_VIZINHOS_VALOR_REFERENCIA` parcelas CONFIRMADAS (`vinculo="confirmado"`
    -- nunca heurística/substituto) do próprio contrato mais próximas por
    vencimento do título avaliado. Substitui o valor estático `nValTotMes`
    (cadastro, pode ficar desatualizado depois de um reajuste) por um valor
    ancorado no histórico observado do próprio contrato.

    Cai pra `nValTotMes` quando há menos de `_MIN_PARCELAS_VALOR_REFERENCIA`
    parcelas confirmadas elegíveis -- tanto pelo caso óbvio (contrato ainda
    sem nenhuma parcela confirmada) quanto pra evitar um caso degenerado: com
    só 1 parcela confirmada, se ela for justamente o título ATRASADO/
    CANCELADO que `_resolver_pendencias` está tentando resolver, a "média"
    vira o próprio valor dele -- qualquer órfão do mesmo valor bateria 0% de
    diferença por tautologia, inclusive um que a busca de substituto (mais
    rigorosa, checa categoria/impostos) já rejeitou por outro motivo. Com o
    mínimo de 2 parcelas *independentes* a comparação deixa de ser circular.
    """
    if venc_titulo is not None:
        elegiveis = [
            p for p in parcelas_confirmadas
            if _parse_data(p.get("dDtVenc")) is not None and _num(p.get("nValorTitulo"))
        ]
        if len(elegiveis) >= _MIN_PARCELAS_VALOR_REFERENCIA:
            elegiveis.sort(key=lambda p: abs((_parse_data(p.get("dDtVenc")) - venc_titulo).days))
            vizinhas = elegiveis[:_K_VIZINHOS_VALOR_REFERENCIA]
            valores = [_num(p.get("nValorTitulo")) for p in vizinhas]
            return sum(valores) / len(valores)
    return _num(candidato.get("nValTotMes"))


def _match_heuristico(
    cabec: dict[str, Any],
    candidatos: list[dict[str, Any]],
    parcelas_confirmadas_por_ctr: dict[Any, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    """Tenta religar um título sem `nCodCtr` a um contrato do cadastro do
    mesmo cliente. Só casa quando sobra **exatamente 1** candidato plausível
    depois de filtrar por:

    - natureza "R" (contratos em `servicos/contrato` são sempre de receita —
      um título "P" do mesmo cliente é uma relação diferente, não o contrato);
    - vencimento não anterior a `dVigInicial` do contrato;
    - valor a até `_TOLERANCIA_VALOR_HEURISTICO` de distância do valor de
      referência do contrato (`_valor_referencia_contrato` — parcelas
      confirmadas vizinhas em data, ou `nValTotMes` na falta delas).

    0 ou 2+ candidatos plausíveis => ambíguo, não casa (fica órfão mesmo).

    Não exige `nCodOS` nem vencimento antes de `dVigFinal` (removidos por
    pedido explícito — na prática a maioria dos títulos que precisam dessa
    religação é `cOrigem="MANR"`, sem OS vinculada, e contratos renovados
    informalmente seguem sendo cobrados depois do fim de vigência formal
    cadastrado; exigir os dois fazia a heurística quase nunca casar nada).

    Isso reabre o risco descrito em `sondas/PESQUISA_CONTRATOS_OS.md`,
    seção 12 (caso FISERV: título `11066616943`, valor idêntico ao contrato
    e dentro da vigência, mas lançamento avulso sem relação real com ele — o
    resto da cobrança daquele contrato seguiu faturado normalmente pela via
    formal, sem nunca referenciar esse título) — sem OS e sem teto de
    vigência, valor+natureza+vencimento mínimo é a única defesa contra esse
    tipo de coincidência. Nível de confiança de um match desta função,
    portanto, é mais baixo que antes: ainda é o candidato mais plausível
    entre os do mesmo cliente (exatamente 1 sobra depois do filtro de
    valor), mas sem a confirmação formal (OS) nem o limite de prazo que
    detectariam boa parte das coincidências como a da FISERV.
    """
    if cabec.get("cNatureza") != "R":
        return None
    venc = _parse_data(cabec.get("dDtVenc"))
    valor = _num(cabec.get("nValorTitulo"))
    if venc is None or not valor:
        return None

    plausiveis = []
    for c in candidatos:
        inicio = _parse_data(c.get("dVigInicial"))
        if inicio and venc < inicio:
            continue
        val_ref = _valor_referencia_contrato(c, parcelas_confirmadas_por_ctr.get(c.get("nCodCtr"), []), venc)
        if not val_ref:
            continue
        if abs(valor - val_ref) / val_ref > _TOLERANCIA_VALOR_HEURISTICO:
            continue
        plausiveis.append(c)

    return plausiveis[0] if len(plausiveis) == 1 else None


def _candidatos_revisao(
    cabec: dict[str, Any],
    candidatos: list[dict[str, Any]],
    parcelas_confirmadas_por_ctr: dict[Any, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Filtra, dentre os contratos do mesmo cliente, só os plausíveis o
    bastante pro sinal de revisão manual: valor a até
    `_TOLERANCIA_VALOR_REVISAO_MAX` do valor de referência do contrato (ver
    `_valor_referencia_contrato`). Sem esse teto, qualquer recebimento do
    cliente (por menor relação que tenha com o contrato) acabava sinalizado
    — não é esse o objetivo (ver constante acima)."""
    valor = _num(cabec.get("nValorTitulo"))
    if not valor:
        return []
    venc = _parse_data(cabec.get("dDtVenc"))
    plausiveis = []
    for c in candidatos:
        val_ref = _valor_referencia_contrato(c, parcelas_confirmadas_por_ctr.get(c.get("nCodCtr"), []), venc)
        if not val_ref:
            continue
        if abs(valor - val_ref) / val_ref <= _TOLERANCIA_VALOR_REVISAO_MAX:
            plausiveis.append(c)
    return plausiveis


def _revisao_motivo(
    cabec: dict[str, Any],
    candidato: dict[str, Any],
    parcelas_confirmadas_por_ctr: dict[Any, list[dict[str, Any]]],
) -> str:
    """Explica, em texto simples, por que um título do mesmo cliente de
    `candidato` não foi religado automaticamente por `_match_heuristico` —
    orienta o que o usuário precisa checar antes de aprovar o vínculo.
    `_match_heuristico` não exige mais `nCodOS` nem vencimento antes de
    `dVigFinal` (ver docstring), então essas duas razões saíram daqui — só
    sobra o que continua sendo critério de fato: vencimento antes do início
    da vigência, valor fora da tolerância, ou ambiguidade."""
    motivos = []
    venc = _parse_data(cabec.get("dDtVenc"))
    inicio = _parse_data(candidato.get("dVigInicial"))
    if venc and inicio and venc < inicio:
        motivos.append("vencimento antes do início da vigência do contrato")
    valor = _num(cabec.get("nValorTitulo"))
    val_ref = _valor_referencia_contrato(candidato, parcelas_confirmadas_por_ctr.get(candidato.get("nCodCtr"), []), venc)
    if valor and val_ref:
        diff_pct = abs(valor - val_ref) / val_ref * 100
        if diff_pct > _TOLERANCIA_VALOR_HEURISTICO * 100:
            motivos.append(f"valor {diff_pct:.1f}% diferente do valor de referência do contrato")
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

# Janela de dias entre o vencimento do título atrasado e o lançamento do
# candidato a pagamento avulso — mesma ideia de `_JANELA_DIAS_SUBSTITUTO_CANCELADO`,
# mas deliberadamente mais larga: uma parcela cancelada tende a ser resolvida
# rápido, enquanto um título atrasado (por definição, já em aberto há pelo
# menos `_DIAS_ATRASO_MINIMO_BUSCA` dias) pode levar bem mais tempo até
# alguém notar e reconciliar manualmente o pagamento avulso. Único caso real
# validado até agora (CERENSA, ver PESQUISA_CONTRATOS_OS.md seção 15) ficou a
# 31 dias de distância — 90 dá folga generosa sobre isso sem abrir a ponto de
# pegar lançamentos de meses muito distantes que só coincidem em valor.
_JANELA_DIAS_PAGAMENTO_ATRASADO = 90

# Faixa de valor pro pagamento avulso quando não há correspondência exata do
# líquido — mesma faixa e mesma razão de `_FAIXA_VALOR_SUBSTITUTO_*`: multa,
# desconto, tarifa bancária ou um pagamento parcial/combinado (duas parcelas
# quitadas numa única transferência) mudam o valor final recebido em relação
# ao líquido teórico do título atrasado.
_FAIXA_VALOR_PAGAMENTO_ATRASADO_MIN = 0.5
_FAIXA_VALOR_PAGAMENTO_ATRASADO_MAX = 3.0


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
) -> tuple[dict[str, Any], bool] | None:
    """Título `ATRASADO` há mais de `_DIAS_ATRASO_MINIMO_BUSCA` dias pode já
    ter sido pago de fato via um lançamento avulso nunca religado ao título
    original — a baixa de uma transferência bancária, por exemplo, não fica
    automaticamente vinculada ao título em aberto que ela quita.

    Candidato: `cOrigem="MANR"` (lançamento manual — mesma exigência de
    `_buscar_substituto_cancelado`/`_match_heuristico`: uma nota fiscal
    formal do mesmo cliente é outra cobrança de verdade, não uma
    reconciliação avulsa), mesma natureza, não cancelado, lançado
    (`dDtEmissao`, ou `dDtVenc` se não houver emissão) **depois** do
    vencimento do título atrasado e a até `_JANELA_DIAS_PAGAMENTO_ATRASADO`
    dias dessa data.

    Comparação de valor em dois níveis, igual a `_buscar_substituto_cancelado`:
    primeiro tenta o valor bruto do candidato batendo com o valor **líquido**
    do atrasado (`_valor_liquido`) a até 1 centavo — uma transferência
    bancária tipicamente não traz quebra de imposto nenhuma no título, então
    comparar contra o bruto do atrasado erraria por causa da retenção que só
    existe do lado do título formal; só se não achar nenhum exato tenta a
    faixa [`_FAIXA_VALOR_PAGAMENTO_ATRASADO_MIN`x,
    `_FAIXA_VALOR_PAGAMENTO_ATRASADO_MAX`x] do líquido (acomoda multa,
    desconto, tarifa ou pagamento parcial/combinado). Em cada nível, só
    resolve com **exatamente 1** candidato — ambíguo (0 ou 2+) não escolhe.

    Devolve `(cabec_candidato, achou_por_valor_exato)`. Nunca promove
    automaticamente (só sinaliza, ver `montar_contratos`), nem por valor
    exato: diferente de uma parcela cancelada, um título atrasado pode
    genuinamente seguir em aberto — "achei um valor parecido lançado depois"
    é uma evidência mais fraca que "a parcela foi formalmente cancelada e
    apareceu um substituto".
    """
    venc_atrasado = _parse_data(cabec_atrasado.get("dDtVenc"))
    if venc_atrasado is None:
        return None
    if (hoje - venc_atrasado).days <= _DIAS_ATRASO_MINIMO_BUSCA:
        return None
    valor_liquido_atrasado = _valor_liquido(cabec_atrasado)
    if not valor_liquido_atrasado:
        return None

    na_janela: list[dict[str, Any]] = []
    for titulo in titulos_candidatos:
        cabec = titulo.get("cabecTitulo", {}) or {}
        if cabec.get("cOrigem") != "MANR":
            continue
        if cabec.get("cNatureza") != cabec_atrasado.get("cNatureza"):
            continue
        if cabec.get("cStatus") == "CANCELADO":
            continue
        data_lancamento = _parse_data(cabec.get("dDtEmissao")) or _parse_data(cabec.get("dDtVenc"))
        if data_lancamento is None or data_lancamento <= venc_atrasado:
            continue
        if (data_lancamento - venc_atrasado).days > _JANELA_DIAS_PAGAMENTO_ATRASADO:
            continue
        na_janela.append(cabec)

    def _valor(cabec: dict[str, Any]) -> float:
        return _num(cabec.get("nValorTitulo")) or 0.0

    exatos = [c for c in na_janela if abs(_valor(c) - valor_liquido_atrasado) < 0.01]
    if exatos:
        return (exatos[0], True) if len(exatos) == 1 else None

    minimo = valor_liquido_atrasado * _FAIXA_VALOR_PAGAMENTO_ATRASADO_MIN
    maximo = valor_liquido_atrasado * _FAIXA_VALOR_PAGAMENTO_ATRASADO_MAX
    na_faixa = [c for c in na_janela if minimo <= _valor(c) <= maximo]
    return (na_faixa[0], False) if len(na_faixa) == 1 else None


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
      descartados, religando ao contrato certo do mesmo cliente quando não
      há ambiguidade (natureza receita, vencimento não anterior à vigência
      inicial, valor a até `_TOLERANCIA_VALOR_HEURISTICO` do valor de
      referência do contrato — ver `_match_heuristico`/
      `_valor_referencia_contrato`) — marcados com `"vinculo": "heuristico"` em
      vez de `"confirmado"`;
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
      `_buscar_pagamento_atrasado`: procura um lançamento manual
      (`cOrigem="MANR"`) do mesmo cliente, lançado depois do vencimento e a
      até `_JANELA_DIAS_PAGAMENTO_ATRASADO` dias dessa data, cujo valor
      bruto bata com o valor **líquido** do atrasado (bruto menos impostos
      efetivamente retidos — uma transferência bancária tipicamente não
      traz quebra de imposto no título) — ou, sem match exato, dentro da
      faixa [`_FAIXA_VALOR_PAGAMENTO_ATRASADO_MIN`x,
      `_FAIXA_VALOR_PAGAMENTO_ATRASADO_MAX`x] desse líquido. Só sinaliza
      (`titulos_para_revisao`, com o motivo dizendo se o valor bateu exato
      ou só na faixa, se a categoria e os impostos retidos do candidato
      batem com os do atrasado, e se a categoria do atrasado é elegível pro
      DRE) — **nunca promove**, nem por valor exato: um título atrasado pode
      genuinamente seguir em aberto, então essa evidência é mais fraca que a
      de um cancelamento;
    - o que sobrar (nem auto-ligado, nem usado como substituto) mas ainda é
      plausível (mesmo cliente de algum contrato cadastrado, valor a até
      `_TOLERANCIA_VALOR_REVISAO_MAX` do valor de referência do contrato)
      entra em `"titulos_para_revisao"` desse(s) contrato(s) — não conta em
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
    substitutos_por_titulo: dict[Any, dict[str, Any]] = {}

    # Pool compartilhado de candidatos: todo órfão não cancelado, disponível
    # tanto pra religação heurística (Passo 1) quanto pra busca de
    # substituto/pagamento avulso (Passo 2). Cancelado não representa
    # pagamento nem pendência real -- não ajuda a decidir nada, nem auto-liga
    # nem sinaliza pra revisão, nem serve de candidato a nada.
    candidatos_pool: list[dict[str, Any]] = [
        t for t in orfaos if (t.get("cabecTitulo") or {}).get("cStatus") != "CANCELADO"
    ]
    # "Consumido" cobre os dois jeitos de um órfão sair do pool: religado por
    # heurística (Passo 1) ou usado como substituto/pagamento avulso (Passo
    # 2) -- uma vez usado de qualquer um dos dois jeitos, não pode ser
    # sugerido de novo (nem pro outro passo, nem pro sinal genérico do
    # passo 3).
    titulos_consumidos: set[Any] = set()

    def _candidatos_disponiveis(cod_cliente: Any) -> list[dict[str, Any]]:
        return [
            t for t in candidatos_pool
            if (t.get("cabecTitulo") or {}).get("nCodCliente") == cod_cliente
            and (t.get("cabecTitulo") or {}).get("nCodTitulo") not in titulos_consumidos
        ]

    def _resolver_pendencias(
        cod_ctr: Any, titulos_a_checar: list[tuple[dict[str, Any], str]]
    ) -> list[tuple[dict[str, Any], str]]:
        """Pra cada parcela CANCELADO/ATRASADO em `titulos_a_checar`, procura
        um candidato a substituto/pagamento avulso entre os órfãos do mesmo
        cliente ainda disponíveis (ver `_candidatos_disponiveis`). Dois casos:

        - CANCELADO: pode ter sido substituído por um lançamento avulso (ver
          `_buscar_substituto_cancelado`). Candidato forte o bastante (valor
          exato + já pago + categoria e impostos batendo — ver
          `_substituto_e_confiavel`) é promovido direto como parcela paga.
        - ATRASADO há mais de `_DIAS_ATRASO_MINIMO_BUSCA` dias: pode já ter
          sido pago via um lançamento avulso nunca religado (ver
          `_buscar_pagamento_atrasado`) — comparando o valor LÍQUIDO do
          atrasado contra o bruto do candidato. Nunca promove (evidência
          mais fraca que o caso de cancelamento — um atrasado pode
          genuinamente seguir em aberto), só sinaliza.

        Devolve as parcelas promovidas (substituto confiável) pra quem chama
        anexar ao grupo do contrato. Chamada duas vezes por `montar_contratos`
        — antes e depois do Passo 1 (religação heurística) — ver nota de
        prioridade lá.
        """
        contrato_cad = contratos_cadastro.get(cod_ctr)
        promovidos: list[tuple[dict[str, Any], str]] = []
        for titulo, _vinculo in titulos_a_checar:
            cabec = titulo.get("cabecTitulo", {}) or {}
            status = cabec.get("cStatus")
            if status not in ("CANCELADO", "ATRASADO"):
                continue
            candidatos_orfaos = _candidatos_disponiveis(cabec.get("nCodCliente"))

            if status == "CANCELADO":
                achado = _buscar_substituto_cancelado(cabec, candidatos_orfaos, contrato_cad)
                if achado is None:
                    continue
                titulo_substituto, achou_exato = achado
                cabec_substituto = titulo_substituto.get("cabecTitulo", {}) or {}
                titulos_consumidos.add(cabec_substituto.get("nCodTitulo"))

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
                        "pagamento": cabec_substituto.get("dDtPagamento") or None,
                        "valor": _num(cabec_substituto.get("nValorTitulo")),
                        "status_omie": cabec_substituto.get("cStatus"),
                        "situacao": _situacao_parcela(cabec_substituto),
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
                achado = _buscar_pagamento_atrasado(cabec, candidatos_orfaos, hoje)
                if achado is None:
                    continue
                cabec_pagamento, achou_exato = achado
                titulos_consumidos.add(cabec_pagamento.get("nCodTitulo"))
                dre_elegivel = _categoria_dre_elegivel(categoria_map, cabec.get("cCodCateg", ""))
                categoria_bate = _categoria_compativel(cabec, cabec_pagamento)
                impostos_batem = _impostos_compativeis(cabec, cabec_pagamento)
                motivo_valor = (
                    "valor bate exatamente com o líquido do título atrasado"
                    if achou_exato
                    else (
                        f"valor dentro da faixa {_FAIXA_VALOR_PAGAMENTO_ATRASADO_MIN:.1f}x–"
                        f"{_FAIXA_VALOR_PAGAMENTO_ATRASADO_MAX:.1f}x do líquido do título atrasado, sem bater exato"
                    )
                )
                titulos_revisao_por_ctr.setdefault(cod_ctr, []).append({
                    "nCodTitulo": cabec_pagamento.get("nCodTitulo"),
                    "vencimento": cabec_pagamento.get("dDtVenc"),
                    "pagamento": cabec_pagamento.get("dDtPagamento") or None,
                    "valor": _num(cabec_pagamento.get("nValorTitulo")),
                    "status_omie": cabec_pagamento.get("cStatus"),
                    "situacao": _situacao_parcela(cabec_pagamento),
                    "motivo": (
                        f"possível pagamento avulso do título atrasado há mais de "
                        f"{_DIAS_ATRASO_MINIMO_BUSCA} dias {cabec.get('nCodTitulo')} ({motivo_valor}; "
                        f"categoria {'bate' if categoria_bate else 'não bate'} com a do atrasado; "
                        f"impostos retidos {'batem' if impostos_batem else 'não batem'}; "
                        f"categoria do título atrasado {'é' if dre_elegivel else 'não é'} destinada ao DRE)"
                    ),
                })
                # Nunca promove -- so sinaliza (ver _buscar_pagamento_atrasado).
                substitutos_por_titulo[cabec.get("nCodTitulo")] = {"cabec": cabec_pagamento, "promovido": False}
        return promovidos

    # Passo 2a: resolve primeiro as pendências (CANCELADO/ATRASADO) das
    # parcelas JÁ CONFIRMADAS (nCodCtr direto) -- antes do Passo 1
    # (religação heurística genérica) ter chance de reivindicar candidatos
    # do mesmo pool. Prioridade explícita, por pedido do usuário: a busca de
    # substituto/pagamento avulso tem checagens mais rigorosas (categoria,
    # impostos, status pago) do que `_match_heuristico`, que só olha
    # natureza + vigência inicial + valor — um candidato que serviria pros
    # dois não deve ser capturado pelo mais permissivo primeiro.
    for cod_ctr, titulos_do_contrato in grupos.items():
        titulos_do_contrato.extend(_resolver_pendencias(cod_ctr, titulos_do_contrato))

    # Snapshot de parcelas CONFIRMADAS por contrato, congelado ANTES do
    # Passo 1 rodar -- ver _valor_referencia_contrato. Fixo neste ponto de
    # propósito: religar um órfão usando o valor de outro órfão religado
    # momentos antes na mesma passada tornaria o resultado dependente da
    # ordem de candidatos_pool (ordem de paginação da API, não cronológica)
    # -- inaceitável numa reconciliação que precisa ser auditável.
    parcelas_confirmadas_por_ctr: dict[Any, list[dict[str, Any]]] = {
        cod_ctr: [(t.get("cabecTitulo") or {}) for t, vinculo in titulos_do_contrato if vinculo == "confirmado"]
        for cod_ctr, titulos_do_contrato in grupos.items()
    }

    # Passo 1: tenta auto-ligar cada órfão ainda não consumido pelo passo 2a
    # (ver _match_heuristico). O que sobrar (não ligado) fica disponível como
    # pool de candidatos pro passo 3.
    orfaos_nao_ligados: list[dict[str, Any]] = []
    for titulo in candidatos_pool:
        cabec = titulo.get("cabecTitulo", {}) or {}
        if cabec.get("nCodTitulo") in titulos_consumidos:
            continue
        candidatos = candidatos_por_cliente.get(cabec.get("nCodCliente"), [])
        alvo = _match_heuristico(cabec, candidatos, parcelas_confirmadas_por_ctr)
        if alvo is not None:
            titulos_consumidos.add(cabec.get("nCodTitulo"))
            grupos.setdefault(alvo["nCodCtr"], []).append((titulo, "heuristico"))
        else:
            orfaos_nao_ligados.append(titulo)

    # Passo 2b: mesma resolução de pendências do passo 2a, agora só pras
    # parcelas CANCELADO/ATRASADO que passaram a existir no grupo por causa
    # do Passo 1 (religadas por heurística) -- as confirmadas já foram
    # tratadas no 2a, antes do Passo 1 rodar.
    for cod_ctr, titulos_do_contrato in grupos.items():
        novos = [par for par in titulos_do_contrato if par[1] == "heuristico"]
        titulos_do_contrato.extend(_resolver_pendencias(cod_ctr, novos))

    # Passo 3: o que sobrar (nem auto-ligado, nem usado como substituto) mas
    # ainda tem cara de contrato (mesmo cliente de um contrato cadastrado,
    # valor plausível) vira sinal de revisão genérico — ver
    # _TOLERANCIA_VALOR_REVISAO_MAX.
    for titulo in orfaos_nao_ligados:
        cabec = titulo.get("cabecTitulo", {}) or {}
        if cabec.get("nCodTitulo") in titulos_consumidos:
            continue
        candidatos = candidatos_por_cliente.get(cabec.get("nCodCliente"), [])
        plausiveis = _candidatos_revisao(cabec, candidatos, parcelas_confirmadas_por_ctr)
        for candidato in plausiveis:
            titulos_revisao_por_ctr.setdefault(candidato["nCodCtr"], []).append({
                "nCodTitulo": cabec.get("nCodTitulo"),
                "vencimento": cabec.get("dDtVenc"),
                "pagamento": cabec.get("dDtPagamento") or None,
                "valor": _num(cabec.get("nValorTitulo")),
                "status_omie": cabec.get("cStatus"),
                "situacao": _situacao_parcela(cabec),
                "motivo": _revisao_motivo(cabec, candidato, parcelas_confirmadas_por_ctr),
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
