# Relatório Financeiro Omie (Contas a Pagar e a Receber)

Gera um relatório financeiro em Excel a partir dos títulos financeiros do ERP
Omie (contas a pagar e a receber), buscados via `PesquisarLancamentos`
(`financas/pesquisartitulos`) e enriquecidos com dados de:

- **Clientes/Fornecedores** (`geral/clientes` — `ListarClientes`): nome fantasia (com fallback para razão social) e CNPJ/CPF
- **Categorias** (`geral/categorias` — `ListarCategorias`): descrição da categoria financeira
- **Contas Correntes** (`geral/contacorrente` — `ListarContasCorrentes`): descrição da conta bancária

Também há uma implementação equivalente que busca os títulos via
`ListarMovimentos` em vez de `PesquisarLancamentos` — veja [Fonte
alternativa: ListarMovimentos](#fonte-alternativa-listarmovimentos-main_movimentospy).

## Instalação

```bash
pip install -r requirements.txt
cp .env.example .env
# edite .env e preencha OMIE_APP_KEY e OMIE_APP_SECRET
```

As credenciais são geradas em **Omie > Configurações > Aplicativos > Chaves de Aplicativo**.

## Uso

```bash
python main.py --data-inicio 01/07/2026 --data-fim 31/07/2026
```

O relatório é salvo em `output/relatorio_financeiro_<inicio>_a_<fim>.xlsx`.

`--data-inicio`/`--data-fim` são **opcionais** — a API da Omie não documenta
nenhum parâmetro de `PesquisarLancamentos` como obrigatório. Rodando sem os
dois, o relatório traz **todos os títulos já lançados na conta**, sem filtro
de período:

```bash
python main.py
```

⚠️ Sem filtro de data isso pode retornar um volume grande de títulos — em
contas com histórico extenso a geração pode demorar bem mais que uma consulta
por período. Também é possível informar só uma das duas datas, para um
intervalo aberto de um dos lados (ex.: só `--data-inicio` traz tudo a partir
daquela data, sem limite final).

### Opções

| Flag | Padrão | Descrição |
|---|---|---|
| `--data-inicio` | nenhum (todos os títulos) | Data inicial do período (`dd/mm/aaaa`) |
| `--data-fim` | nenhum (todos os títulos) | Data final do período (`dd/mm/aaaa`) |
| `--filtro-data` | `vencimento` | Campo de data usado no filtro: `vencimento`, `emissao` ou `pagamento` |
| `--natureza` | `PR` | `P` = só contas a pagar, `R` = só a receber, `PR` = ambos |
| `--status` | todos | Filtra por status: `EMABERTO`, `LIQUIDADO`, `ATRASADO`, `AVENCER`, `VENCEHOJE`, `PAGTO_PARCIAL`, `RECEBIDO`, `CANCELADO` |
| `--output` | `output/relatorio_financeiro_*.xlsx` | Caminho do arquivo de saída |
| `--registros-por-pagina` | `100` | Tamanho de página nas chamadas à API (100 é o limite documentado pela Omie) |
| `--sem-cache-disco` | desligado | Desativa o cache local em `.cache/` (força reconsultar tudo via API) |
| `--cache-ttl-horas` | `24` | Validade do cache local em disco, em horas |
| `--debug` | desligado | Grava as respostas brutas da API em `debug_raw/` (útil para depuração) |
| `--env-file` | `.env` | Caminho alternativo para o arquivo de credenciais |

### Exemplos

```bash
# Só contas a receber em aberto, vencendo em agosto/2026
python main.py --data-inicio 01/08/2026 --data-fim 31/08/2026 --natureza R --status EMABERTO

# Filtrando pela data de pagamento (títulos efetivamente liquidados no mês)
python main.py --data-inicio 01/07/2026 --data-fim 31/07/2026 --filtro-data pagamento --status LIQUIDADO

# Todos os títulos já lançados na conta, sem filtro de período
python main.py

# Todos os títulos em aberto a partir de uma data, sem limite final
python main.py --data-inicio 01/01/2026 --status EMABERTO
```

## Categoria por título (sem rateio)

Um título na Omie pode ser rateado entre duas ou mais categorias financeiras
(`aCodCateg`/`categorias`, dependendo do endpoint). O relatório **não**
divide o título nessas categorias — cada título gera sempre **uma linha**,
com a categoria única de `cCodCateg` recebendo 100% do valor.

Isso foi uma decisão deliberada, não uma limitação esquecida: comparando
título a título contra o `ListarMovimentos` (fonte alternativa, veja abaixo),
confirmamos que esse endpoint não expõe o rateio de categorias de forma
confiável. Para as duas fontes de dados produzirem exatamente o mesmo
resultado para o mesmo título, o rateio foi removido também da implementação
via `PesquisarLancamentos` (que sim tem o dado de rateio disponível, mas
deixou de usá-lo por consistência).

Como cada título sempre gera uma linha, a contagem de *linhas* nas abas
"Geral", "Contas a Pagar" e "Contas a Receber" é igual à contagem de
*títulos* distintos.

## Conteúdo do relatório (abas do Excel)

1. **Resumo** — totais de contas a pagar/receber, valores em aberto, pagos/recebidos e saldo projetado
2. **Geral** — todas as contas a pagar e a receber juntas, no layout padrão de exportação da Omie (veja abaixo)
3. **Contas a Pagar** — lista detalhada dos títulos a pagar no período
4. **Contas a Receber** — lista detalhada dos títulos a receber no período
5. **Por Status** — quantidade e valores agrupados por status
6. **Por Categoria** — quantidade e valores agrupados por categoria financeira
7. **Por Cliente-Fornecedor** — quantidade e valores agrupados por cliente/fornecedor
8. **Fluxo Mensal** — projeção de fluxo de caixa (pagar x receber x saldo) por mês de vencimento

## Aba "Geral": layout padrão da Omie

A aba "Geral" replica colunas, nomes e estilo visual do modelo de exportação
nativo da Omie (referência: aba "bdContas" de uma planilha baixada
manualmente do sistema) — cabeçalho cinza claro em negrito com quebra de
linha, congelamento de painel, valores em formato contábil (parênteses para
negativo) e datas em `dd/mm/yyyy`.

| Coluna | Origem |
|---|---|
| `Tipo` | `cNatureza` do título (`1. Contas a Receber` / `2. Contas a Pagar`) |
| `Grupo` | Categoria pai (`categoria_superior`) da categoria do título, resolvida via `ListarCategorias` |
| `Categoria` | Descrição da categoria financeira (`ListarCategorias`) |
| `Data de Registro (completa)` | `dDtRegistro` do título |
| `Data de Emissão (completa)` / `Data de Vencimento (completa)` / `Data de Pagto` | `dDtEmissao` / `dDtVenc` / `dDtPagamento` |
| `Situação do Vencimento` | Calculada (não vem pronta da API): `Pago`/`Recebido` quando liquidado; senão, faixas de dias vencidos/a vencer (até 30, 31–60, 61–90, mais de 90) a partir da data de vencimento |
| `Valor da Conta` / `Pago ou Recebido` / `A Pagar ou Receber` | `nValorTitulo` / `resumo.nValPago` / `resumo.nValAberto` |
| `COFINS/CSLL/INSS/IR/ISS/PIS Retido` | Campos de retenção do título (`nValorCOFINS`, `nValorCSLL`, `nValorINSS`, `nValorIR`, `nValorISS`, `nValorPIS`) |
| `DRE` | `Sim` quando a categoria tem `codigo_dre` cadastrado na Omie, `Não` caso contrário |
| `Cliente ou Fornecedor (Nome Fantasia)` | Nome fantasia (fallback razão social) via `ListarClientes` |
| `x`, `Observação do Pagto ou Recbto`, `cod.fcx` | Sem fonte na API da Omie — mantidas em branco só para preservar a estrutura do modelo |

As demais abas (Contas a Pagar/Receber, Por Status, Por Categoria, Por
Cliente-Fornecedor, Fluxo Mensal) usam o schema interno original do projeto,
sem alteração.

## Fonte alternativa: ListarMovimentos (`main_movimentos.py`)

Gera o **mesmo relatório** (mesmas abas, mesmo layout), mas buscando os dados
via `ListarMovimentos` (`financas/mf`) em vez de `PesquisarLancamentos`
(`financas/pesquisartitulos`):

```bash
python main_movimentos.py --data-inicio 01/07/2026 --data-fim 31/07/2026
```

As mesmas opções de `main.py` estão disponíveis (`--data-inicio`,
`--data-fim`, `--filtro-data`, `--natureza`, `--output`, etc.), com duas
diferenças:

- `--status` não é validado localmente contra uma lista fixa — o vocabulário
  de status do `ListarMovimentos` é diferente do `PesquisarLancamentos` (ex.:
  `"A VENCER"` com espaço, `"PAGO"`/`"RECEBIDO"` em vez de
  `"AVENCER"`/`"LIQUIDADO"`); passe o valor exato aceito pela sua conta.
- `--natureza PR` (padrão) faz **uma única busca paginada** trazendo Pagar e
  Receber juntos — o `ListarMovimentos` aceita `cNatureza` opcional, diferente
  do `PesquisarLancamentos`, que exige natureza por chamada e por isso busca
  em duas passadas.

O arquivo de saída padrão usa o prefixo `relatorio_financeiro_movimentos_`
para não colidir com o gerado por `main.py`.

**Por que não é um espelho 1:1 do endpoint**: `ListarMovimentos` retorna, na
mesma consulta, registros de naturezas bem diferentes — confirmado
empiricamente contra uma conta real, não documentado explicitamente pela
Omie. O campo `detalhes.cGrupo` de cada item indica o tipo:

| `cGrupo` | O que é | Incluído no relatório? |
|---|---|---|
| `CONTA_A_PAGAR` / `CONTA_A_RECEBER` | O título em si (mesma granularidade do `PesquisarLancamentos`) | Sim |
| `PREVISAO_CONTRATO` | Previsão de faturamento de um contrato — ainda não é um título lançado (`cStatus="PREVISAO"`) | Sim, com tratamento especial (veja abaixo) |
| `CONTA_CORRENTE_PAG` / `CONTA_CORRENTE_REC` | O lançamento da baixa no extrato bancário — um **segundo registro para o mesmo `nCodTitulo`** já coberto por `CONTA_A_PAGAR`/`CONTA_A_RECEBER`, com um `resumo` reduzido repetindo `nValPago` | Não (duplicaria "Valor Pago/Recebido" se incluído) |

`src/movimentos.py` filtra por allow-list (só os grupos acima viram linha do
relatório — qualquer `cGrupo` não mapeado é ignorado, não incluído por
padrão) e adapta cada item para o mesmo shape `{"cabecTitulo": ...,
"resumo": ...}` que `report_builder.py` já usa — nenhuma outra parte do
pipeline (enriquecimento, agregações, geração do Excel) precisou mudar. O
array `categorias` (rateio) do `ListarMovimentos` é ignorado — veja
[Categoria por título (sem rateio)](#categoria-por-título-sem-rateio) para o
porquê.

**`PREVISAO_CONTRATO` tem um `resumo` reduzido** (só `nValLiquido`, sem
`nValPago`/`nValAberto`/`cLiquidado`) — sem tratamento, apareceria com "Valor
Aberto" e "Valor Pago" zerados mesmo tendo um valor previsto. `_adaptar_movimento`
aplica um fallback explícito (decisão do projeto, não documentada pela Omie):
quando `nValAberto` está ausente, usa `nValLiquido` no lugar. Isso mistura
previsões de faturamento com títulos já lançados nas mesmas abas/totais do
Resumo — se quiser distingui-las, o campo `cGrupo` original ainda está
disponível em `cabecTitulo` (não aparece como coluna do relatório, mas dá
para inspecionar via `--debug`).

### Comparação título a título contra PesquisarLancamentos

Validado com uma comparação campo a campo dos 162 títulos de um mesmo período
(01/07 a 31/07/2026) entre os dois endpoints — não só os totais agregados, mas
cada título individualmente:

- **Mesmo conjunto de títulos**: os 162 `nCodTitulo` retornados são idênticos
  nos dois lados (nenhum exclusivo de um endpoint), e os 9 indicadores do
  Resumo batem exatamente.
- **Diferenças de formato, sem impacto no relatório**: `cCodIntTitulo` e as
  colunas de retenção (`nValorPIS/COFINS/CSLL/IR/ISS/INSS`) vêm ausentes
  (`None`) no `ListarMovimentos` em vez de `0`/`""` explícito como no
  `PesquisarLancamentos` — equivalentes depois de tratados pelo código
  (`_num()` já trata ausência como zero).
- **`observacao` vinha vazia por padrão** — corrigido enviando
  `lDadosCad=True` em toda chamada (parâmetro "incluir dados de cadastro"),
  o que recupera a observação para a maioria dos títulos.
- **Rateio de categoria não é exposto** pelo `ListarMovimentos`: dos 162
  títulos, 5 são rateados entre 2–3 categorias no `PesquisarLancamentos` (com
  `aCodCateg` populado corretamente), mas o array `categorias` do
  `ListarMovimentos` veio **sempre vazio** para eles — mesmo com
  `lDadosCad=True`. Foi essa descoberta que motivou remover o rateio das duas
  implementações (veja acima); com isso, os 5 títulos também passaram a
  produzir exatamente o mesmo resultado nos dois endpoints.

## Testando sem credenciais

Há testes offline que validam toda a lógica de montagem do relatório e
geração do Excel usando amostras fixas, sem chamar a API:

```bash
python tests/test_offline.py             # PesquisarLancamentos (tests/sample_titulos.json)
python tests/test_movimentos_offline.py  # ListarMovimentos (tests/sample_movimentos.json)
```

## Notas sobre a API da Omie

- O envelope de chamada é sempre `POST` com corpo
  `{"call": "...", "app_key": "...", "app_secret": "...", "param": [{...}]}`.
- A Omie exige uma natureza (`P` ou `R`) por chamada em `PesquisarLancamentos`,
  por isso a busca é feita em duas passadas quando `--natureza PR`.
- Os nomes de campos usados (`nCodTitulo`, `cabecTitulo`, `resumo`, etc.) foram
  extraídos da documentação oficial do endpoint. Caso a Omie retorne uma
  estrutura diferente da esperada em sua conta, rode com `--debug` e inspecione
  os arquivos JSON gerados em `debug_raw/` para ajustar o mapeamento em
  `src/report_builder.py`.

## Conformidade com os limites de consumo da API

Baseado em https://ajuda.omie.com.br/pt-BR/articles/8112984-limites-de-consumo-da-api-do-omie:

| Limite documentado | Como o projeto lida com isso |
|---|---|
| 240 req/min (4/s) por IP + App Key + Método; 960 req/min por IP | [omie_client.py](src/omie_client.py): limitador de taxa global (`OMIE_MAX_REQ_POR_SEGUNDO` no `.env`, padrão 3/s), thread-safe |
| Máx. 100 registros por página | `--registros-por-pagina` tem padrão e é validado contra esse limite (`LIMITE_REGISTROS_POR_PAGINA` em `titulos.py`); clientes/fornecedores são obtidos via `ListarClientes` (listagem paginada), não mais 1 chamada `ConsultarCliente` por cliente distinto |
| Bloqueio de 30 min (HTTP 425) após 10 falhas seguidas no mesmo método | `omie_client.py` detecta HTTP 425 e falha imediatamente com mensagem clara, em vez de insistir com retry e piorar o bloqueio |
| "Cacheie consultas no lado da aplicação" | Categorias, contas correntes e clientes/fornecedores são cacheados em disco (`.cache/`, TTL configurável via `--cache-ttl-horas`) — reexecuções para outro período não re-consultam cadastros que não mudaram |

O cache local em `.cache/` guarda dados cadastrais reais (razão social, nome fantasia, CNPJ/CPF
de clientes) — está no `.gitignore` e não deve ser versionado. Use
`--sem-cache-disco` para forçar tudo fresco da API.

## Estrutura do projeto

```
src/
  config.py          # leitura/validação do .env
  omie_client.py      # cliente HTTP genérico (auth, rate limit thread-safe, retry, 425)
  local_cache.py        # cache local em disco (.cache/) com TTL
  titulos.py              # busca paginada + sonda de volume via PesquisarLancamentos
  movimentos.py             # busca paginada + adaptação via ListarMovimentos (fonte alternativa)
  enrichment.py               # categorias, contas correntes e clientes/fornecedores (listagem paginada, com cache)
  report_builder.py             # achatamento dos títulos em linhas + agregações (comum às duas fontes)
  excel_writer.py                 # geração do .xlsx formatado
  cli.py                            # orquestração / linha de comando (PesquisarLancamentos)
  cli_movimentos.py                   # orquestração / linha de comando (ListarMovimentos)
main.py                            # ponto de entrada (PesquisarLancamentos)
main_movimentos.py                   # ponto de entrada (ListarMovimentos)
tests/
  sample_titulos.json      # amostra fiel ao schema do PesquisarLancamentos
  test_offline.py            # validação sem rede/credenciais (PesquisarLancamentos)
  sample_movimentos.json   # amostra fiel ao schema do ListarMovimentos
  test_movimentos_offline.py # validação sem rede/credenciais (ListarMovimentos)
```
