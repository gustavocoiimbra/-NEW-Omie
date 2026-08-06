# Relatório Financeiro Omie (Contas a Pagar e a Receber)

Gera um relatório financeiro em Excel a partir dos títulos financeiros do ERP
Omie (contas a pagar e a receber), buscados via `PesquisarLancamentos`
(`financas/pesquisartitulos`) e enriquecidos com dados de:

- **Clientes/Fornecedores** (`geral/clientes` — `ConsultarCliente`): razão social e CNPJ/CPF
- **Categorias** (`geral/categorias` — `ListarCategorias`): descrição da categoria financeira
- **Contas Correntes** (`geral/contacorrente` — `ListarContasCorrentes`): descrição da conta bancária

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

⚠️ Sem filtro de data isso pode retornar um volume grande de títulos e, na
etapa de enriquecimento, uma consulta por cliente/fornecedor distinto
(limitada pelo rate limit configurado) — em contas com histórico extenso a
geração pode demorar bem mais que uma consulta por período. Também é possível
informar só uma das duas datas, para um intervalo aberto de um dos lados
(ex.: só `--data-inicio` traz tudo a partir daquela data, sem limite final).

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
| `--max-workers-clientes` | `4` | Máx. de consultas `ConsultarCliente` em paralelo (4 = limite de concorrência da Omie por método) |
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

## Rateio de categorias

Um título na Omie pode ser rateado entre duas ou mais categorias financeiras
(`aCodCateg`). Quando isso ocorre, o relatório gera **uma linha por categoria**
para aquele título — não apenas uma linha com a categoria "principal". Os
valores monetários de cada linha (`Valor Título`, `Valor Aberto`, `Valor
Pago/Recebido`, `Juros`, `Multa`, `Desconto`, `Valor Líquido`) são
proporcionais ao percentual de rateio daquela categoria (`% Categoria`), de
forma que a soma das linhas rateadas sempre reconcilia exatamente com o total
original do título. A coluna `Valor Título (Total)` mantém o valor cheio do
título (repetido em todas as suas linhas) para referência.

Isso significa que a contagem de *linhas* nas abas "Geral", "Contas a Pagar" e
"Contas a Receber" pode ser maior que a contagem de *títulos* distintos — por
isso o Resumo e as abas "Por Status"/"Por Categoria"/"Por Cliente-Fornecedor"
contam títulos únicos (`Código Título` distinto), não linhas.

## Conteúdo do relatório (abas do Excel)

1. **Resumo** — totais de contas a pagar/receber, valores em aberto, pagos/recebidos e saldo projetado
2. **Geral** — todas as contas a pagar e a receber juntas (com coluna Natureza), ordenadas por data de vencimento
3. **Contas a Pagar** — lista detalhada dos títulos a pagar no período
4. **Contas a Receber** — lista detalhada dos títulos a receber no período
5. **Por Status** — quantidade e valores agrupados por status
6. **Por Categoria** — quantidade e valores agrupados por categoria financeira
7. **Por Cliente-Fornecedor** — quantidade e valores agrupados por cliente/fornecedor
8. **Fluxo Mensal** — projeção de fluxo de caixa (pagar x receber x saldo) por mês de vencimento

## Testando sem credenciais

Há um teste offline que valida toda a lógica de montagem do relatório e geração
do Excel usando uma amostra fixa (`tests/sample_titulos.json`), sem chamar a API:

```bash
python tests/test_offline.py
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
| Até 4 requisições simultâneas por IP + App Key + Método | `ConsultarCliente` (o maior volume de chamadas do projeto) é paralelizado com `ThreadPoolExecutor` — veja `--max-workers-clientes` |
| Máx. 100 registros por página | `--registros-por-pagina` tem padrão e é validado contra esse limite (`LIMITE_REGISTROS_POR_PAGINA` em `titulos.py`) |
| Bloqueio de 30 min (HTTP 425) após 10 falhas seguidas no mesmo método | `omie_client.py` detecta HTTP 425 e falha imediatamente com mensagem clara, em vez de insistir com retry e piorar o bloqueio |
| "Cacheie consultas no lado da aplicação" | Categorias, contas correntes e clientes/fornecedores são cacheados em disco (`.cache/`, TTL configurável via `--cache-ttl-horas`) — reexecuções para outro período não re-consultam cadastros que não mudaram |

O cache local em `.cache/` guarda dados cadastrais reais (razão social, CNPJ/CPF
de clientes) — está no `.gitignore` e não deve ser versionado. Use
`--sem-cache-disco` para forçar tudo fresco da API.

## Estrutura do projeto

```
src/
  config.py          # leitura/validação do .env
  omie_client.py      # cliente HTTP genérico (auth, rate limit thread-safe, retry, 425)
  local_cache.py        # cache local em disco (.cache/) com TTL
  titulos.py              # busca paginada + sonda de volume via PesquisarLancamentos
  enrichment.py             # categorias, contas correntes, cache paralelo de clientes/fornecedores
  report_builder.py          # achatamento dos títulos em linhas + agregações
  excel_writer.py             # geração do .xlsx formatado
  cli.py                        # orquestração / linha de comando
main.py                    # ponto de entrada
tests/
  sample_titulos.json      # amostra fiel ao schema da API para testes
  test_offline.py            # validação sem rede/credenciais
```
