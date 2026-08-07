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

### Variáveis usadas em cada endpoint

| Endpoint | Módulo | Principais campos usados |
|---|---|---|
| `PesquisarLancamentos` / `ListarMovimentos` | `titulos.py` / `movimentos.py` | Título: `nCodTitulo`, `cCodCateg`, `nCodCliente`, `nCodCC`, `cStatus`, `cNatureza`, `nValorTitulo`, `observacao`, datas (`dDtEmissao`/`Venc`/`Pagamento`/`Registro`), retenções (`nValorPIS/COFINS/CSLL/IR/ISS/INSS` + flags `cRetXXX`) · Resumo: `cLiquidado`, `nValPago`, `nValAberto`, `nJuros`, `nMulta`, `nDesconto` |
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
main.py                            # ponto de entrada (PesquisarLancamentos)
main_movimentos.py                   # ponto de entrada (ListarMovimentos)
tests/
  sample_titulos.json / test_offline.py             # validação sem rede (PesquisarLancamentos)
  sample_movimentos.json / test_movimentos_offline.py # validação sem rede (ListarMovimentos)
```
