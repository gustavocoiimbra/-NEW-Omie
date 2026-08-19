# Consultas Power Query -- Omie

Pacote de consultas Power Query (linguagem M) para trazer movimentações
financeiras, saldo de caixa e os cadastros de enriquecimento (categoria/
conta corrente/cliente) da API da Omie direto no Excel.

**ATENÇÃO:** app_key/app_secret ficam gravados dentro do arquivo .xlsx (nos
parâmetros da consulta). Nunca compartilhe/envie/suba essa planilha depois
de configurar as credenciais -- é equivalente a compartilhar a senha da
conta Omie.

## Arquivos

Cada arquivo `.pq` desta pasta é o conteúdo de **uma** consulta. O nome do
arquivo é o nome exato que a consulta deve ter no Excel.

| Arquivo | O que traz | Vira aba na planilha? |
|---|---|---|
| `fnOmieCall.pq` | Função auxiliar: envelope padrão de chamada à API da Omie | Não -- é uma função, não uma tabela |
| `CategoriaMap.pq` | Cadastro de categorias financeiras | Opcional |
| `ContaCorrenteMap.pq` | Cadastro de contas correntes | Opcional |
| `ClienteMap.pq` | Cadastro de clientes/fornecedores | Opcional |
| `Movimentacoes.pq` | Contas a pagar e a receber, com rateio de categoria e previsão de faturamento de contrato -- consulta principal | **Sim** |
| `SaldoCaixaBase.pq` | Saldo e extrato das contas de caixa -- consulta intermediária | **Não** |
| `CaixaSaldoPorConta.pq` | Saldo real por conta (lê `SaldoCaixaBase`) | **Sim** |
| `CaixaLancamentosPrevistos.pq` | Lançamentos previstos a vencer (lê `SaldoCaixaBase`) | **Sim** |
| `CaixaFluxoSemanal.pq` | Fluxo de caixa projetado por semana (lê `SaldoCaixaBase`) | **Sim** |

## Como criar cada consulta no Excel

Para cada arquivo: **Dados > Obter Dados > Consulta em Branco** > no painel
**Consultas**, clique com o botão direito na nova consulta > **Renomear** >
digite o nome exato do arquivo (sem `.pq`) > **Editor Avançado** > apague o
conteúdo padrão > cole o conteúdo do arquivo > **Concluído**.

## Carregando o resultado como aba da planilha

Criar a consulta não gera uma aba sozinho -- é preciso carregá-la. No editor
do Power Query: **Página Inicial > Fechar e Carregar > Fechar e Carregar
Em...** (a seta ao lado do botão, não o botão em si -- clicar direto no
botão carrega com as opções padrão do Excel, que nem sempre são as
desejadas aqui). Na janela que abre:

- **Tabela** (não "Relatório de Tabela Dinâmica" nem "Gráfico Dinâmico") --
  é o formato que as fórmulas/tabelas dinâmicas da planilha vão consumir.
- **Nova planilha** -- cada consulta carregada ganha sua própria aba, com o
  nome da consulta. Pra renomear a aba sem afetar a consulta, dê duplo
  clique no nome da aba na parte inferior da planilha (é independente do
  nome da consulta no painel Consultas).
- Deixe **"Adicionar estes dados ao Modelo de Dados"** desmarcado, a menos
  que vá montar Tabelas/Gráficos Dinâmicos que precisem cruzar dados entre
  consultas diferentes.

Quais consultas carregar como aba (coluna "Vira aba na planilha?" na tabela
acima):

- **`Movimentacoes`, `CaixaSaldoPorConta`, `CaixaLancamentosPrevistos`,
  `CaixaFluxoSemanal`** -- são o resultado final, sempre carregar como aba.
- **`CategoriaMap`, `ContaCorrenteMap`, `ClienteMap`** -- opcional. Alimentam
  `Movimentacoes` por referência de nome (não precisam de aba própria pra
  isso funcionar), mas carregar como aba ajuda a conferir o cadastro
  diretamente na planilha. Se preferir não ver essas abas, clique com o
  botão direito na consulta (painel Consultas) > **Habilitar Carregamento**
  (desmarcar) -- ela continua disponível pras outras consultas, só não vira
  aba.
- **`SaldoCaixaBase`** -- nunca carregar como aba: o resultado dela é um
  registro único com as 3 saídas de caixa dentro (não uma tabela), e
  carregar geraria um erro ou uma aba sem utilidade. Confirme que
  "Habilitar Carregamento" está desmarcado nela (botão direito na consulta,
  painel Consultas).
- **`fnOmieCall`** -- o Excel já classifica funções separadamente (fica em
  "Outras Consultas", sem a opção de carregar) -- nenhuma ação necessária.

Depois de carregadas, **Dados > Atualizar Tudo** atualiza todas as abas de
uma vez, refazendo as chamadas à API.

## Configuração inicial (antes de criar as consultas)

**Página Inicial > Gerenciar Parâmetros > Novo Parâmetro**, tipo Texto:

- `OmieAppKey` = sua app_key
- `OmieAppSecret` = sua app_secret

## Ordem de criação

1. `fnOmieCall`, `CategoriaMap`, `ContaCorrenteMap`, `ClienteMap` -- não
   dependem uns dos outros, crie em qualquer ordem entre eles.
2. `Movimentacoes` -- depende dos 4 anteriores.
3. `SaldoCaixaBase` -- depende só de `fnOmieCall` e `ContaCorrenteMap` (NÃO
   de `Movimentacoes`). Antes de usar, abra o Editor Avançado e configure
   `ContasCaixaTexto` (formato: `nome exato no cadastro Omie:rótulo,nome
   exato no cadastro:rótulo,...` -- o rótulo é opcional). Depois de criar,
   **desligue "Habilitar Carregamento"** (botão direito na consulta, painel
   Consultas) -- ela é só uma etapa intermediária, não uma tabela pra
   planilha.
4. `CaixaSaldoPorConta`, `CaixaLancamentosPrevistos`, `CaixaFluxoSemanal` --
   dependem só de `SaldoCaixaBase`, crie as 3 por último, em qualquer ordem
   entre si (essas sim, com carregamento ligado).

## Endpoints usados

| Endpoint | Método (chamada) | Consulta que usa |
|---|---|---|
| `financas/pesquisartitulos` | `PesquisarLancamentos` | `Movimentacoes` (fonte primária de título) |
| `financas/mf` | `ListarMovimentos` | `Movimentacoes` (só previsão de faturamento de contrato) |
| `geral/categorias` | `ListarCategorias` | `CategoriaMap` |
| `geral/contacorrente` | `ListarContasCorrentes` | `ContaCorrenteMap` |
| `geral/clientes` | `ListarClientes` | `ClienteMap` |
| `financas/extrato` | `ListarExtrato` | `SaldoCaixaBase` |

## Dicionário de campos -- aba "Movimentacoes"

Cada linha é uma coluna da tabela final, na ordem em que aparece. "Campo da
API" usa o nome do campo tal como a Omie devolve (antes de qualquer
renomeação feita na consulta).

| Coluna | Endpoint | Campo da API | Observação |
|---|---|---|---|
| `x` | -- | -- | Sempre em branco -- sem fonte na API |
| `Tipo` | `pesquisartitulos` / `mf` | `cNatureza` | `R` -> "1. Contas a Receber"; `P` -> "2. Contas a Pagar" |
| `Grupo` | `geral/categorias` | `categoria_superior` (2º lookup) | Descrição da categoria-pai (categoria_superior) da categoria do título; "" quando o título não tem categoria_superior |
| `Categoria` | `geral/categorias` | `descricao` + `conta_inativa` | Nome da categoria; soma o sufixo " (inativa)" quando `conta_inativa="S"`; sem correspondência no cadastro, mostra o próprio código (`cCodCateg`) |
| `Observação da Conta` | `pesquisartitulos` / `mf` | `observacao` | `\|` vira espaço; decodifica `&amp; &lt; &gt; &quot; &#39;` |
| `Data de Registro (completa)` | `pesquisartitulos` / `mf` | `dDtRegistro` | Data |
| `Data de Emissão (completa)` | `pesquisartitulos` / `mf` | `dDtEmissao` | Data |
| `NC/Nfe` | `pesquisartitulos` / `mf` | `cNumDocFiscal` | Número do documento fiscal |
| `Data de Vencimento (completa)` | `pesquisartitulos` / `mf` | `dDtVenc` | Data |
| `Situação do Vencimento` | -- (derivado) | `cStatus` + `resumo.cLiquidado` + `dDtVenc` | "Cancelado" se `cStatus="CANCELADO"`; "Pago"/"Recebido" se liquidado; senão, faixa de dias vencido/a vencer contra a data local do computador |
| `Valor da Conta` | `pesquisartitulos` / `mf` | `nValorTitulo` | Valor cheio do título -- ou da categoria, quando o título tem rateio |
| `Pago ou Recebido` | `pesquisartitulos` / `mf` | `resumo.nValPago` | Decimal |
| `A Pagar ou Receber` | `pesquisartitulos` / `mf` | `resumo.nValAberto` | Decimal; previsão de contrato usa `resumo.nValLiquido` no lugar (não tem `nValAberto`) |
| `Conta Corrente` | `geral/contacorrente` | `descricao` (via `nCodCC`) | Sem correspondência no cadastro, mostra o próprio código |
| `Cliente ou Fornecedor (Nome Fantasia)` | `geral/clientes` | `nome_fantasia` (via `nCodCliente`) | Sem nome fantasia, usa `razao_social` |
| `Observação do Pagto ou Recbto` | `pesquisartitulos` | `lancamentos[].cObsLanc` | Só existe em `PesquisarLancamentos`; concatenado com " \| " quando há mais de um lançamento de baixa; em branco pra previsão de contrato e títulos sem baixa registrada |
| `Data de Pagto` | `pesquisartitulos` / `mf` | `dDtPagamento` | Data |
| `COFINS Retido` | `pesquisartitulos` / `mf` | `nValorCOFINS` (só quando `cRetCOFINS="S"`) | Decimal |
| `CSLL Retido` | `pesquisartitulos` / `mf` | `nValorCSLL` (só quando `cRetCSLL="S"`) | Decimal |
| `INSS Retido` | `pesquisartitulos` / `mf` | `nValorINSS` (só quando `cRetINSS="S"`) | Decimal |
| `IR Retido` | `pesquisartitulos` / `mf` | `nValorIR` (só quando `cRetIR="S"`) | Decimal |
| `ISS Retido` | `pesquisartitulos` / `mf` | `nValorISS` (só quando `cRetISS="S"`) | Decimal |
| `PIS Retido` | `pesquisartitulos` / `mf` | `nValorPIS` (só quando `cRetPIS="S"`) | Decimal |
| `Desconto` | `pesquisartitulos` / `mf` | `resumo.nDesconto` | Decimal |
| `Juros` | `pesquisartitulos` / `mf` | `resumo.nJuros` | Decimal |
| `DRE` | -- (derivado) | `cStatus` + `categoria.nao_exibir`/`transferencia`/`totalizadora` | "Não" quando `cStatus="ATRASADO"` ou a categoria está marcada como não-exibida/transferência/totalizadora; "Sim" nos demais casos |
| `cod.fcx` | -- | -- | Sempre em branco -- nenhum endpoint da Omie devolve esse código |

**Campo capturado mas sem coluna própria nesta versão:** `resumo.nMulta`
(multa aplicada ao título) vem em `PesquisarLancamentos`, mas não tem coluna
na tabela final -- só está disponível dentro do `resumo` bruto, se algum dia
for necessário adicionar.

**cCodCateg com rateio:** quando um título está dividido entre categorias
(`aCodCateg[]` com mais de uma entrada), a tabela final tem uma linha por
categoria -- cada linha usa `cCodCateg`/`nValorTitulo`/retenções da sua
própria entrada em `aCodCateg[]`, não do título inteiro.
