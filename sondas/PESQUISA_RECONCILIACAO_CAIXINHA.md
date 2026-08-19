# Pesquisa: reconciliação bdContas vs. API — a conta "Caixinha" e o rateio de categoria

Investigação feita contra a conta real (não fixtures), a partir dos 166 lançamentos que
apareciam "só na planilha nativa" (`bdContas`) numa reconciliação completa contra
`ListarMovimentos`. Os dumps brutos usados aqui estão em `sondas/output/` (fora do
controle de versão). **Resultado: 161 dos 166 (97%) têm causa raiz confirmada.**

## Resumo executivo

| Pergunta | Resposta curta |
|---|---|
| Por que 91 lançamentos da conta "Caixinha" só aparecem na planilha nativa? | **Confirmado.** Existem só em `ListarExtrato` — nunca em `ListarMovimentos`, `ListarLancCC` nem `ListarContasPagar` (filtrado pra essa conta). São `cSituacao="Previsto"` + `cTipoDocumento="Pedido de Compra"`, isto é, previsões, não títulos formais. |
| Esses 91 lançamentos têm alguma contrapartida financeira real? | **Parcialmente — 61% sim.** 57 de 94 lançamentos reais da Caixinha têm um título formal `PAGO` confirmado em outra conta corrente (Itaú Unibanco ou Bradesco), tipicamente ~1 mês depois. Os outros 39% não têm par mesmo com tolerância de 180 dias. |
| Por que quase o dobro de "Itaú Unibanco" só-na-nativa em relação ao esperado? | **93% (70 de 75) é rateio de categoria** — um único título na API, dividido em 2-3 linhas por categoria na planilha nativa. Não é dado ausente, é limitação de reconciliação por valor. |
| Sobra alguma coisa sem explicação? | **Sim, 5 registros** (de 166) — testados contra 3 endpoints diferentes, sem aparecer em nenhum. Provavelmente alterados/excluídos na Omie depois da exportação nativa. |

---

## 1. A conta "Caixinha" — lançamentos que só existem no extrato bancário

### 1.1 O achado

A conta corrente "Caixinha" (há **dois códigos cadastrados** com esse nome:
`11057330227` e `11564214808`) tem 94 lançamentos reais (excluindo marcadores de saldo
`cDesCliente="SALDO"`/`"SALDO ANTERIOR"`) que existem **só** em `financas/extrato · ListarExtrato`.
Testados exaustivamente contra as outras fontes de título/lançamento:

| Endpoint testado | Resultado |
|---|---|
| `financas/mf · ListarMovimentos` | **0** — testado em janela larga (7 meses, 1 chamada) e mês a mês (7 chamadas), mesmo resultado: zero |
| `financas/contacorrentelancamentos · ListarLancCC` | **0** — testado contra o histórico completo da empresa (5.530 lançamentos, todas as contas), nenhum com `nCodCC` da Caixinha |
| `financas/contapagar · ListarContasPagar` (filtrado pra Caixinha) | Não testado isoladamente (ver seção 1.3 — testado contra **outras** contas, com resultado positivo) |

### 1.2 Categoria, tipo de lançamento e outros campos

Ao contrário do que a exploração inicial (superficial, só olhando o registro de marcador
de saldo) sugeriu, os lançamentos **reais** da Caixinha em `ListarExtrato` têm um conjunto
rico de campos — bem mais do que os 5 campos (`cDesCliente`, `dDataLancamento`, `nSaldo`,
`nSaldoPrev`, `nValorDocumento`) que os marcadores de saldo mostram:

```json
{
  "cCodCategoria": "0.01.02",
  "cDesCategoria": "Saída de Transferência",
  "cDesCliente": "Kalunga SA",
  "cRazCliente": "Kalunga SA",
  "cDocCliente": "43.283.811/0195-00",
  "cDocumentoFiscal": "000193798",
  "cNumero": "000193798",
  "cNatureza": "P",
  "cParcela": "001/001",
  "cSituacao": "Previsto",
  "cTipoDocumento": "Pedido de Compra",
  "cOrigem": "Previsão de Pedido de Compra",
  "cDataInclusao": "04/06/2024",
  "cHoraInclusao": "13:40:35",
  "dDataLancamento": "04/06/2024",
  "nCodCliente": -1,
  "nCodLancamento": 11067814667,
  "nValorDocumento": -302.93
}
```

**Achado central:** em toda a amostra verificada, os lançamentos reais da Caixinha têm
sempre `cSituacao="Previsto"`, `cTipoDocumento="Pedido de Compra"` e
`cOrigem="Previsão de Pedido de Compra"` — nunca `"Conciliado"` nem outro tipo de
documento. A categoria (`cCodCategoria="0.01.02"`) é sempre **"Saída de Transferência"**,
uma categoria genérica de movimentação de caixa, não a categoria real da despesa
(ex.: "Material de Escritório" pra uma compra na Kalunga) — a categoria de negócio real
só aparece na planilha nativa, não nesse campo do extrato.

### 1.3 Hipótese confirmada: são previsões de compra, e ~61% viram título real depois, noutra conta

**Hipótese testada e parcialmente confirmada:** os lançamentos da Caixinha são uma
**previsão/provisionamento interno ligado a Pedido de Compra**, não uma movimentação
formal de título. Quando o pagamento de fato acontece, ele é lançado como um título
formal **na conta bancária real** (Itaú Unibanco ou Bradesco) — não na Caixinha.

Confirmado cruzando os 94 lançamentos reais da Caixinha contra `ListarContasPagar` de
Itaú Unibanco (3.467 títulos) + Bradesco (812 títulos), pareando 1:1 por valor + data mais
próxima (sem reaproveitar o mesmo título pra dois lançamentos da Caixinha):

| Tolerância de data | Pareados | Sem par |
|---|---:|---:|
| 90 dias | 56 de 94 (60%) | 38 |
| 180 dias | 57 de 94 (61%) | 37 |

Ampliar a janela pra 180 dias resgatou só **mais 1 caso** — confirma que 90 dias já
captura praticamente tudo que segue esse mecanismo; o que sobra não é "janela curta
demais", é que genuinamente não segue esse padrão.

**Padrão por fornecedor** (todos os pareados vieram com `status_titulo=PAGO`):

| Fornecedor | Conta real do pagamento | Defasagem típica |
|---|---|---|
| GIMBA (alimentação, recorrente) | Itaú Unibanco, sempre | +27 a +46 dias (instâncias mais recentes: -1 a -4 dias) |
| Editora Globo S.A. (assinatura mensal fixa, R$129,90) | Bradesco, sempre | +25 a +31 dias, muito consistente |
| Fornecedores pontuais (THE KEY, Lobby Tecnologia, Refrigelo, Divinho, Mundo Cerealista, JSMC, Amora Maker) | Itaú Unibanco | 0 a +46 dias (1 caso com -155 dias — título real *antes* da previsão) |

**Quem fica sem par (37 de 94, 39%):** concentrado em **Kalunga SA** (papelaria — 9
ocorrências, valores pequenos de R$44 a R$895) e fornecedores de alimentação pontuais
(Zona Cerealista, Garrama, Bold Snacks, SK, Varanda Frutas, SPD Comércio, parte do
próprio GIMBA), além de Evolução Ltda, FOF Suplementos e Lenovo (valores maiores,
isolados). Hipótese não confirmada: provavelmente pago por um caminho que
`ListarContasPagar` não cobre (cartão corporativo, débito não formalizado como título).

### 1.4 Caminhos descartados nesta investigação

- **`financas/contapagar · ConsultarContaPagar`** com `codigo_lancamento_omie=nCodLancamento`
  (o código do extrato) — erro "Lançamento não cadastrado para o Código". Confirma que
  `nCodLancamento` do extrato é um espaço de códigos próprio, não o mesmo de
  `codigo_lancamento_omie` de contas a pagar.
- **`produtos/pedidocompra · PesquisarPedCompra`** — testado pra um lançamento específico
  (Kalunga SA, R$302,93, 04/06/2024): zero Pedidos de Compra cadastrados na janela em
  torno dessa data, nem no ano de 2024 inteiro. O rótulo `cOrigem="Previsão de Pedido de
  Compra"` parece ser uma classificação interna da Omie, não uma referência a um registro
  hoje consultável nesse módulo.

### 1.5 Nota técnica: cuidado ao testar `ConsultarContaPagar`/`PesquisarPedCompra` em lote

Ambos os métodos retornam **HTTP 500** tanto pra erro recuperável (rede/instabilidade)
quanto pra erro determinístico de negócio ("não cadastrado", "sem registros pra essa
página"). O `src/omie_client.py` atual trata todo HTTP 500 como recuperável e tenta de
novo — em erro determinístico isso nunca teria sucesso, e repetir a mesma chamada
idêntica aciona o bloqueio de "consumo redundante" da Omie, que pode escalar até o
bloqueio de 30 minutos (HTTP 425) por método. Aconteceu nesta investigação com os dois
métodos acima. Vale considerar não reter em erros cuja mensagem indica claramente que
é determinístico, mas isso é uma melhoria de código, não foi implementada aqui.

---

## 2. Rateio de categoria — por que "Itaú Unibanco" tinha quase o dobro de registros esperado

### 2.1 O achado

Dos 75 registros "só na planilha nativa" da conta Itaú Unibanco, **70 (93%) são o mesmo
padrão de rateio**: um único título no `ListarMovimentos`/`ListarContasPagar` (com um
valor e uma categoria só), dividido em **2 ou 3 linhas por categoria** na planilha nativa
`bdContas`. Como a reconciliação casa por valor, nenhuma das partes bate com o título
combinado — por isso aparecem como "só na nativa", mas não é dado ausente.

### 2.2 Os dois subtipos confirmados

**a) Impostos (20 registros, "RECEITA FAZENDA")** — 10 pares confirmados por NC/Nfe +
data de vencimento idênticos, cada par somando exatamente o valor do título único:

| Par de categorias | Contexto |
|---|---|
| IRPJ + CSLL | Recolhimento mensal padrão |
| PIS + COFINS | Sobre faturamento |
| IRRF + CSRF (sobre serviços tomados) | Retenção em serviços |
| IRRF + INSS | Retenção em folha/serviço |

**b) Despesas de viagem / reembolso a pessoa física (50 registros, 24 grupos)** —
mesmo cliente + mesma data, 2-3 linhas com categorias diferentes, tipicamente
"Despesas com Transporte" + "Lanches e Refeições" (às vezes + "Hospedagem",
"Confraternização" ou "Material de Uso e Consumo"). Formato clássico de relatório de
despesas de viagem/reembolso corporativo, dividido por categoria no export nativo.

---

## 3. Os 5 registros que continuam sem explicação

Depois de explicar Caixinha (91) e rateio (70), sobram **5 dos 166** — todos do lado
"Itaú Unibanco", testados contra `ListarMovimentos` (os 5 grupos de `cGrupo`, campos
`nValorTitulo` e `nValorMovCC`), `ListarLancCC` (histórico completo) e `ListarContasPagar`
(filtrado por Itaú, 3.467 títulos) — sem aparecer em nenhum:

| Fornecedor | Valor | Vencimento (nativo) |
|---|---:|---|
| VERBENA FLORES | R$ 558,00 | 17/03/2026 |
| Editora Globo S.A. | R$ 129,90 | 20/04/2026 |
| Editora Globo S.A. | R$ 129,90 | 20/05/2026 |
| LC EMPREENDIMENTO IMOBILIARIO SPE LTDA | R$ 616,27 | 18/05/2026 |
| LOBBY TECNOLOGIA E PRODUTOS PERSONALIZADOS LTDA | R$ 177,25 | 07/08/2026 |

Hipótese mais provável, não confirmada: título alterado ou excluído na Omie depois do
momento em que a exportação nativa foi feita — não é possível confirmar sem
`nCodTitulo` do lado nativo (a planilha `bdContas` não traz esse campo).

> Nota: esses são registros **distintos** dos lançamentos da Caixinha, mesmo quando o
> nome do fornecedor coincide (ex.: "Editora Globo S.A." e "Lobby Tecnologia" também
> aparecem como lançamentos reais da Caixinha, seção 1 — com valores e datas
> diferentes dos 5 acima). São achados de duas investigações separadas que só
> compartilham o nome do fornecedor por coincidência comercial.

---

## O que foi implementado

Nenhuma mudança de código foi feita a partir desta investigação — é um registro de
achados pra referência futura. Ver `GLOSSARIO.md` (seção 3.5) para o resumo dos campos
do extrato incorporado à referência principal.
