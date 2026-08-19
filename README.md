# Relatório Financeiro Omie (Contas a Pagar e a Receber)

Consulta a API da Omie e gera um relatório financeiro em Excel (contas a
pagar e a receber), enriquecido com clientes/fornecedores, categorias e
contas correntes.

O projeto tem **duas implementações equivalentes**, cada uma consultando um
endpoint diferente da Omie:

| Entry point | Endpoint consultado | Módulo |
|---|---|---|
| `python main.py` | `PesquisarLancamentos` (`financas/pesquisartitulos`) | `src/titulos.py` |
| `python main_movimentos.py` | `ListarMovimentos` (`financas/mf`) | `src/movimentos.py` |

As duas produzem o **mesmo relatório** (mesmas abas, mesmo layout) — a lógica
de montagem do relatório (`src/report_builder.py`) é 100% compartilhada entre
elas. As diferenças entre os dois endpoints (o que cada um retorna, filtros
aceitos, casos especiais) estão documentadas como docstring no topo de
`src/movimentos.py`, não aqui.

Além dos dois relatórios em Excel, o projeto tem **dois painéis** que geram
JSON em vez de planilha, pensados pra alimentar um dashboard HTML:

| Entry point | Gera | Módulo |
|---|---|---|
| `python main_dashboard.py` | `output/contratos.json` — carteira de contratos (parcelas, status, reconciliação heurística de títulos órfãos) | `src/contratos.py` + `src/contratos_cadastro.py` |
| `python main_caixa.py` | `output/caixa.json` — saldo real das contas operacionais + projeção de fluxo de caixa semanal | `src/extrato.py` |

Ver `GLOSSARIO.md` para o significado de cada campo desses dois JSONs e o
fluxo completo de onde cada dado vem.

Tem ainda um terceiro tipo de saída: `main_planilha.py` mantém uma **planilha
única e estável** (`output/movimentacoes_atualizado.xlsx` por padrão) com o
snapshot mais atual das movimentações — só a aba de movimentações (mesmo
layout "Geral"/bdContas dos outros relatórios), sobrescrita por completo a
cada execução, sem as abas analíticas de `main.py`/`main_movimentos.py`.
Pensado pra substituir uma reexportação manual da tela da Omie: roda de novo
quando quiser os dados mais recentes, sempre no mesmo caminho.

### Variáveis usadas em cada endpoint

| Endpoint | Módulo | Principais campos usados |
|---|---|---|
| `PesquisarLancamentos` / `ListarMovimentos` | `titulos.py` / `movimentos.py` | Título: `nCodTitulo`, `cCodCateg`, `nCodCliente`, `nCodCC`, `cStatus`, `cNatureza`, `nValorTitulo`, `observacao`, datas (`dDtEmissao`/`Venc`/`Pagamento`/`Registro`), retenções (`nValorPIS/COFINS/CSLL/IR/ISS/INSS` + flags `cRetXXX`) · Resumo: `cLiquidado`, `nValPago`, `nValAberto`, `nJuros`, `nMulta`, `nDesconto` · **Só `PesquisarLancamentos`**: `lancamentos[].cObsLanc` (texto real da baixa bancária — preenche "Observação do Pagto ou Recbto", ~58% dos títulos liquidados) |
| `ListarCategorias` (`geral/categorias`) | `enrichment.py` | `codigo`, `descricao`, `categoria_superior`, `codigo_dre`, `conta_inativa`, `nao_exibir`, `transferencia`, `totalizadora` |
| `ListarContasCorrentes` (`geral/contacorrente`) | `enrichment.py` | `nCodCC`, `descricao` |
| `ListarClientes` (`geral/clientes`) | `enrichment.py` | `codigo_cliente_omie`, `razao_social`, `nome_fantasia`, `cnpj_cpf` |

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
# ou, usando a outra fonte de dados:
python main_movimentos.py --data-inicio 01/07/2026 --data-fim 31/07/2026
```

O relatório é salvo em `output/relatorio_financeiro_<inicio>_a_<fim>.xlsx`
(prefixo `relatorio_financeiro_movimentos_` quando gerado por
`main_movimentos.py`).

`--data-inicio`/`--data-fim` são opcionais — rodando sem os dois, o relatório
traz todos os títulos já lançados na conta, sem filtro de período (pode
demorar bem mais em contas com histórico extenso).

Os dois painéis JSON têm seus próprios entry points e opções (`--help` em
cada um pra ver todas):

```bash
python main_dashboard.py                    # output/contratos.json (histórico completo)
python main_dashboard.py --ano 2025,2026    # filtra o JSON final por ano (não muda o que é buscado na API)
python main_caixa.py                        # output/caixa.json
python main_planilha.py                     # output/movimentacoes_atualizado.xlsx (sobrescrito a cada execução)
```

### Opções

| Flag | Padrão | Descrição |
|---|---|---|
| `--data-inicio` / `--data-fim` | nenhum (todos os títulos) | Período do filtro (`dd/mm/aaaa`) |
| `--filtro-data` | `vencimento` | Campo de data usado no filtro: `vencimento`, `emissao` ou `pagamento` |
| `--natureza` | `PR` | `P` = só contas a pagar, `R` = só a receber, `PR` = ambos |
| `--status` | todos | Filtra por status do título |
| `--output` | `output/relatorio_financeiro_*.xlsx` | Caminho do arquivo de saída |
| `--registros-por-pagina` | `100` | Tamanho de página nas chamadas à API |
| `--sem-cache-disco` | desligado | Desativa o cache local em `.cache/` (força reconsultar tudo via API) |
| `--cache-ttl-horas` | `24` | Validade do cache local em disco, em horas |
| `--debug` | desligado | Grava as respostas brutas da API em `debug_raw/` (útil para depuração) |
| `--env-file` | `.env` | Caminho alternativo para o arquivo de credenciais |

`main.py --status` valida contra a lista documentada de status do
`PesquisarLancamentos`; `main_movimentos.py --status` não valida localmente
(o vocabulário do `ListarMovimentos` é diferente — veja `src/movimentos.py`).

## Conteúdo do relatório (abas do Excel)

1. **Resumo** — totais de contas a pagar/receber, valores em aberto, pagos/recebidos e saldo projetado
2. **Geral** — todos os títulos juntos, no layout padrão de exportação da Omie (mesmas colunas do relatório nativo "bdContas")
3. **Contas a Pagar** / **Contas a Receber** — lista detalhada dos títulos do período
4. **Por Status** / **Por Categoria** / **Por Cliente-Fornecedor** — quantidade e valores agrupados
5. **Fluxo Mensal** — projeção de fluxo de caixa por mês de vencimento

As regras de como cada coluna é calculada (categoria, grupo, DRE, situação do
vencimento, retenções, etc.) estão documentadas como docstring nas funções de
`src/report_builder.py` que montam cada aba — é a fonte de verdade, não este
README.

## Testando sem credenciais

```bash
python tests/test_offline.py             # PesquisarLancamentos (tests/sample_titulos.json)
python tests/test_movimentos_offline.py  # ListarMovimentos (tests/sample_movimentos.json)
```

Validam toda a lógica de montagem do relatório e geração do Excel a partir de
amostras fixas, sem chamar a API.

## Conformidade com os limites de consumo da API

Baseado em https://ajuda.omie.com.br/pt-BR/articles/8112984-limites-de-consumo-da-api-do-omie:
rate limit thread-safe e retentativas com backoff (`src/omie_client.py`),
detecção de bloqueio por excesso de requisições (HTTP 425), paginação
respeitando o limite de 100 registros, e cache em disco com TTL para
cadastros (categorias/contas correntes/clientes) que mudam raramente.

O cache local em `.cache/` guarda dados cadastrais reais (razão social, CNPJ/CPF
de clientes) — está no `.gitignore` e não deve ser versionado. Use
`--sem-cache-disco` para forçar tudo fresco da API.

## Estrutura do projeto

```
src/
  config.py          # leitura/validação do .env
  omie_client.py      # cliente HTTP genérico (auth, rate limit thread-safe, retry, 425)
  local_cache.py        # cache local em disco (.cache/) com TTL
  titulos.py              # busca paginada via PesquisarLancamentos
  movimentos.py             # busca paginada via ListarMovimentos (fonte alternativa)
  enrichment.py               # categorias, contas correntes e clientes/fornecedores (com cache)
  report_builder.py             # montagem das linhas/abas do relatório (comum às duas fontes)
  excel_writer.py                 # geração do .xlsx formatado
  cli.py                            # orquestração / linha de comando (PesquisarLancamentos)
  cli_movimentos.py                   # orquestração / linha de comando (ListarMovimentos)
  contratos_cadastro.py               # catálogo mestre de contratos via ListarContratos
  contratos.py                          # carteira de contratos: agrupa por nCodCtr, reconciliação heurística de títulos órfãos
  extrato.py                              # saldo real + projeção de fluxo de caixa via ListarExtrato
main.py                            # ponto de entrada (PesquisarLancamentos)
main_movimentos.py                   # ponto de entrada (ListarMovimentos)
main_dashboard.py                      # ponto de entrada (painel de contratos, JSON)
main_caixa.py                            # ponto de entrada (painel de fluxo de caixa, JSON)
main_planilha.py                           # ponto de entrada (planilha única e estável de movimentações)
tests/
  sample_titulos.json / test_offline.py             # validação sem rede (PesquisarLancamentos)
  sample_movimentos.json / test_movimentos_offline.py # validação sem rede (ListarMovimentos)
  test_extrato_offline.py                              # validação sem rede (extrato.py)
  test_contratos_offline.py                              # validação sem rede (contratos.py)
sondas/
  PESQUISA_CONTRATOS_OS.md          # investigação: cadastro de contratos vs. títulos do ListarMovimentos
  sonda_contratos_os.py               # script exploratório: ListarContratos/ListarOS/ConsultarContaReceber
  sonda_reconciliacao_bdcontas.py       # script exploratório: reconciliação bdContas nativo vs. API
  pesquisa.ipynb                          # notebook: reconciliação bdContas nativo vs. API (interativo)
power_query/
  README.md                          # instruções de configuração e ordem de criação das consultas
  fnOmieCall.pq / CategoriaMap.pq / ContaCorrenteMap.pq / ClienteMap.pq
  Movimentacoes.pq                     # PesquisarLancamentos + rateio + ListarMovimentos (previsão de contrato)
  SaldoCaixaBase.pq / CaixaSaldoPorConta.pq / CaixaLancamentosPrevistos.pq / CaixaFluxoSemanal.pq
```
