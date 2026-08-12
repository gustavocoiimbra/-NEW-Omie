# Pesquisa: por que nem todo contrato aparece no painel — ListarContratos, ListarOS e ConsultarContaReceber

Investigação feita contra a conta real (não fixtures), disparada por um caso concreto: o contrato da
ADSMAIS PRODUÇÕES, PROMOÇÕES ARTÍSTICAS E CULTURAIS LTDA "sumia" do painel apesar de ter parcela prevista.
Os dumps brutos usados aqui estão em `sondas/output/` (fora do controle de versão). **Já implementado e
validado contra a conta real** — ver seções 8–9 e "O que foi implementado" no fim deste documento.

## Resumo executivo

| Pergunta do usuário | Resposta curta |
|---|---|
| Nem todo contrato aparece no `ListarMovimentos` | **Confirmado e corrigido.** 60 contratos existem em `ListarContratos`; só 56 tinham ao menos um título/previsão com `nCodCtr` preenchido no `ListarMovimentos`. Agora o cadastro entra como catálogo mestre — os 60 aparecem. |
| `ListarContratos` lista todos | **Confirmado.** É a única fonte que traz o cadastro completo, incluindo contratos sem nenhum título lançado. Agora usada como fonte mestre (`src/contratos_cadastro.py`). |
| Parcelas "atrasadas"/"canceladas" podem ter sido faturadas via OS | **Parcialmente, e a causa real da ADSMAIS era outra.** Toda parcela faturada já nasce com uma OS por trás (`nCodOS` no próprio título); cancelamento-e-reemissão já aparecia corretamente como duas linhas. O problema real de "atraso escondido" veio de títulos **sem OS e sem contrato** (conciliação bancária manual) — resolvido por reconciliação heurística (seção 8). A ADSMAIS em si era um bug diferente: erro de digitação de cliente numa parcela antiga (seção 9), já corrigido. |
| Lançamentos de pagamento (`ListarMovimentosFinanceiros`) precisam de campo extra para achar o contrato | **Não precisam** — confirmado, nenhuma mudança feita aqui. |
| `ConsultarContaReceber`/`ListarOS`/`ConsultarOS` em lote | **Confirmado desnecessário pelo usuário** — não integrados ao pipeline em lote (custo de rate limit não se paga; os campos já vêm de graça no título). |
| Reconciliação heurística dos títulos órfãos | **Implementada** (seção 8) — 42 parcelas religadas a 21 contratos. Resultado real: nenhum "atrasado" desapareceu (eram genuínos), mas 2 contratos que estavam com status desatualizado por falta do dado passaram a mostrar "Em atraso" corretamente. |
| O `ListarMovimentos` está trazendo todos os títulos previstos (`PREVISAO_CONTRATO`)? | **Não estava, com a janela padrão original — corrigido.** O horizonte de previsão da Omie não é fixo, e a janela padrão (fim em 31/12 do ano corrente) cortava previsões futuras em silêncio. Testado ao vivo com/sem filtro de data (seção 10): a causa não era "usar filtro corta previsão", era enviar `dDtVencDe` sem `dDtVencAte`. **Decisão final: nenhum filtro de data por padrão** — busca o histórico completo. |

Conclusão prática: **`ListarContratos` entrou no pipeline como catálogo mestre de contratos**, sem substituir o
`ListarMovimentos` (continua sendo a única fonte de parcelas/previsões), e o `ListarMovimentos` passou a ser
consultado **sem filtro de data por padrão** (histórico completo, sem risco de cortar previsão futura em
silêncio). O ganho foi parar de *inferir* a existência de um contrato a partir dos títulos e passar a *listar*
os contratos primeiro, anexando os títulos que existirem (confirmados ou religados por heurística).

---

## 1. `ListarContratos` (`servicos/contrato`) — estrutura confirmada

Chamada: `{"pagina": N, "registros_por_pagina": 20}` → resposta com `total_de_paginas`, `total_de_registros` e a
lista em **`contratoCadastro`** (não `cadastros`, como o script de sonda supunha por convenção — precisou ser
corrigido pela execução real).

Campos relevantes em `contratoCadastro[i].cabecalho`:

| Campo | Exemplo (ADSMAIS) | Uso |
|---|---|---|
| `nCodCtr` | `11425539548` | mesma chave que `nCodCtr` nos títulos do `ListarMovimentos` — é o join natural |
| `cNumCtr` | `"2025/00022"` | número legível do contrato (já usado no painel) |
| `nCodCli` | `11458425216` | cliente do contrato — **fonte de verdade**, mais confiável que inferir pelo primeiro título do grupo |
| `cCodSit` | `"10"` | status do contrato no cadastro (código, não texto — ver seção 4) |
| `dVigInicial` / `dVigFinal` | `27/10/2025` / `06/11/2026` | vigência contratual |
| `nDiaFat` | `5` | dia de faturamento recorrente |
| `nValTotMes` | `15000` | valor mensal cadastrado (pode divergir do valor da última parcela, útil para detectar reajuste não refletido) |

Total no cadastro: **60 contratos** (3 páginas de 20). O relatório atual (`main_dashboard.py`, janela
01/01/2024–31/12/2027) identifica **56**.

## 2. Os 4 contratos que ficavam de fora (antes da implementação — hoje aparecem via cadastro, seção 8/9)

Cruzando os 60 do cadastro contra os 56 do relatório original (só `nCodCtr` direto), por `nCodCtr`:

| nCodCtr | cNumCtr | cCodSit | Vigência | Motivo real (confirmado) |
|---|---|---|---|---|
| 11085507131 | 2024/00009 | 90 | 19/05/2024–01/12/2024 | Cliente tem 3 títulos reais faturados (R$ 21.116,25 cada), mas **sem `nCodCtr` nem `nCodOS`** — ver caso órfão na seção 3 |
| 11086469156 | 2024/00012 | 90 | 13/10/2023–13/10/2024 | `nValTotMes=0` — contrato cancelado sem nunca ter sido faturado |
| 11130042749 | 2024/00029 | 00 (rascunho) | 24/07/2024–03/10/2024 | Rascunho nunca ativado — o mesmo cliente tem **outro** contrato (`2024/00023`) que é o que foi de fato faturado. O rascunho é ruído do cadastro, não um contrato perdido |
| 11218410894 | 2025/00005 | 10 | 10/01/2025–13/01/2025 | Vigência de 3 dias, zero título e zero previsão encontrados em qualquer janela — parece ter sido criado e nunca efetivamente usado, apesar de `cCodSit=10` |

Nenhum desses 4 é o caso da ADSMAIS — o contrato dela (`11425539548`, `2025/00022`) **já está** nos 56. O que
aconteceu foi outra coisa (seção 5).

## 3. Ordem de Serviço (`servicos/os`) — o vínculo existe, mas não na listagem em massa

- `ListarOS` (paginado, `{"pagina", "registros_por_pagina", "apenas_importado_api"}`) retorna a lista em
  **`osCadastro`**. Cada item tem `Cabecalho.nCodCli`, `Cabecalho.nCodOS`, mas o bloco
  `InformacoesAdicionais` **não traz** o número do contrato nessa listagem.
- `ConsultarOS` (`{"nCodOS": <id>}`) — chamada de detalhe, **um registro por vez** — traz o mesmo conteúdo
  **mais** `InformacoesAdicionais.cNumContrato`, ex.: `"2025/00022"` para a OS 11458425475 (uma das parcelas da
  ADSMAIS). Confirma que a OS sabe de qual contrato ela veio — só não expõe isso na listagem em massa.
- **Não é necessário chamar `ConsultarOS`/`ListarOS` para religar título → contrato**: todo título retornado
  por `ListarMovimentos` que foi faturado via OS já vem com `nCodOS`, `cNumOS` e `nCodNF` diretamente no
  `cabecTitulo` — o vínculo título→OS já está disponível sem chamada extra. O que só existe via `ConsultarOS`
  é o vínculo **OS→contrato em texto** (`cNumContrato`), redundante com o `nCodCtr` que o próprio título já
  carrega quando o título tem contrato.
- Custo de usar `ConsultarOS` em massa: são 564 OS cadastradas — chamar uma a uma para todas violaria o rate
  limit rapidamente (a sonda já tomou um erro de "consumo redundante" só testando 2 chamadas seguidas). Só vale
  a pena sob demanda (ex.: o usuário clica numa parcela específica no painel e quer ver o detalhe da OS/NF).

## 4. `cCodSit` do contrato — confirmado

| `cCodSit` | Ocorrências | Status |
|---|---|---|
| `00` | 1 | Em elaboração |
| `10` | 34 | Ativo |
| `90` | 21 | Suspenso |
| `99` | 4 | Cancelado |

Confirmado pelo usuário contra a documentação oficial (a hipótese inicial, baseada só no padrão estatístico dos
60 registros, tinha `90`/`99` invertidos: havia chutado "Cancelado"/"Encerrado" onde o correto é
"Suspenso"/"Cancelado"). Implementado em `src/contratos_cadastro.py::_STATUS_CADASTRO`.

**Achado importante: `cCodSit` diverge do status que o painel já calcula a partir das parcelas.** Cruzando os
56 contratos do relatório atual:

| `cCodSit` (cadastro) | `status_contrato` (inferido de parcelas) | n |
|---|---|---|
| 10 (Ativo) | Ativo | 9 |
| 10 (Ativo) | Em atraso | 4 |
| 10 (Ativo) | **Encerrado** | **20** |
| 90 (Suspenso) | Em atraso | 2 |
| 90 (Suspenso) | Encerrado | 17 |
| 99 (Cancelado) | Cancelado | 1 |
| 99 (Cancelado) | Em atraso | 1 |
| 99 (Cancelado) | Encerrado | 2 |

20 dos 34 contratos marcados `cCodSit=10` (Ativo) no cadastro da Omie já não têm nenhuma parcela em
aberto/prevista — ou seja, o campo de status do **cadastro** do contrato fica desatualizado em relação à
realidade de faturamento (alguém não voltou lá para marcar como suspenso/cancelado quando o contrato parou de
gerar parcela). Isso significa que **`cCodSit` não deve substituir** o `status_contrato` que já é calculado a
partir das parcelas reais — ele serve para um propósito diferente e complementar: é o **único sinal disponível**
para os contratos que não têm nenhuma parcela no relatório (os 4 da seção 2), onde não há dado nenhum para
inferir status a partir de parcela.

## 5. O caso da ADSMAIS — causa raiz real: cliente inferido do título errado, não do contrato

O contrato `11425539548` (`2025/00022`) **já estava** nos 56 do relatório de ontem — só que com
`"cliente": "ALTERMARK"` em vez de ADSMAIS. Investigando as 14 parcelas uma a uma: 13 delas têm
`nCodCliente=11458425216` (ADSMAIS) — **menos a mais antiga** (`nCodTitulo=11429047035`, vencimento 20/11/2025,
`CANCELADO`), que tem `nCodCliente=11425539279` (**ALTERMARK COMUNICAÇÃO LTDA**, empresa diferente).
Confirmado via `ConsultarContaReceber` desse título específico.

Ou seja: é um erro real de digitação na Omie — a primeira nota fiscal desse contrato foi lançada para o cliente
errado (depois cancelada, nunca corrigida) e todas as parcelas seguintes já saíram certas. O bug era nosso: o
código escolhia o cliente do contrato a partir do **primeiro título por vencimento**
(`titulos_do_contrato[0]`), então bastava essa única parcela antiga e cancelada estar com o cliente trocado
para o contrato inteiro exibir o nome errado — mesmo com 13 das 14 parcelas corretas.

**Corrigido** em `contratos.py::montar_contratos`: o cliente agora vem do **cadastro do contrato**
(`ListarContratos.cabecalho.nCodCli`, através de `contratos_cadastro`) sempre que disponível, em vez de
"o cliente da parcela que por acaso vence primeiro". Isso também deixa o pipeline imune a esse tipo de erro de
digitação em qualquer parcela antiga/cancelada de qualquer contrato, não só o da ADSMAIS.

## 6. O caso órfão real: título faturado sem nenhum vínculo (nem contrato, nem OS)

Cliente `11067669315` (contrato cadastrado `2024/00009`, `cCodSit=90`) tem 3 títulos reais de R$ 21.116,25 no
`ListarMovimentos`, todos com `nCodCtr=None` **e** `nCodOS=None`. Confirmado via `ConsultarContaReceber`
(`nCodTitulo=11107694599`): `nCodOS: 0`, `cNumeroContrato: ""`, `id_origem: "MANR"`, observação
`"Gerado automaticamente pela importação do extrato.|TED ..."`.

Ou seja: esse título nasceu de uma **conciliação bancária manual** (alguém importou o extrato e classificou o
TED recebido direto como receita, sem passar pelo módulo de contrato nem de OS). **Nenhum endpoint da Omie tem
essa informação** — não é um campo que falta pedir, é um vínculo que nunca existiu nos dados de origem. A única
forma de reconectar isso ao contrato certo seria uma heurística (mesmo cliente + valor parecido + dentro da
vigência do contrato) — sujeita a erro, deveria ser sinalizada como "correspondência sugerida", nunca uma
atribuição automática silenciosa.

## 7. Baixas (`CONTA_CORRENTE_REC`/`CONTA_CORRENTE_PAG`) — não precisam de campo extra

Confirmado no dump: uma baixa tem `nCodTitulo` apontando para o título original (mesmo valor já usado para
achar `nCodCtr`), mas **não tem `nCodCtr` nem `nCodOS` própria** — ela é só o registro de "este título foi
conciliado no banco". Isso bate com o que `movimentos.py` já documenta e já faz: o pipeline atual ignora essas
linhas de propósito (evitar duplicar valor pago) e lê o status de pagamento (`cStatus=PAGO/RECEBIDO`,
`dDtPagamento`) direto do título original — que é exatamente onde `nCodCtr` também está. Não há necessidade de
nenhuma mudança aqui.

## 8. Reconciliação heurística — implementada, resultado medido contra a conta real

`contratos.py::_match_heuristico` religa um título sem `nCodCtr` ao contrato do mesmo cliente quando sobra
**exatamente 1 candidato plausível** depois de filtrar por natureza `"R"`, vencimento dentro da vigência do
contrato e valor a até 5% de `nValTotMes` (tolerância calibrada contra os 8 casos reais da seção 6/tabela
abaixo). Rodando contra a conta real (janela 2024–2027):

- **42 títulos religados** a 21 contratos diferentes, todos com `"vinculo": "heuristico"` na parcela (nunca
  misturado sem marcação com os `"confirmado"` que já vêm com `nCodCtr` da Omie).
- Nenhum candidato ambíguo foi forçado: títulos com 0 ou 2+ contratos plausíveis do mesmo cliente permanecem
  órfãos, como o caso da seção 6 (cliente `11067669315`, valor 6% fora da tolerância — continua de fora,
  corretamente, já que nem a própria Omie tem esse vínculo).

**Resultado real é diferente da expectativa inicial** — vale registrar por que: a hipótese era que religar os
órfãos faria "desaparecer" atrasados que na verdade já tinham sido pagos. Na prática, comparando o `status_contrato`
antes/depois:

| | Antes (56 contratos, só `nCodCtr` direto) | Depois (60 contratos, cadastro + heurística) |
|---|---|---|
| Em atraso | 7 | 9 |

Nenhum dos 7 contratos originalmente "Em atraso" saiu dessa situação — busquei manualmente, para cada um, um
título órfão do mesmo cliente em ±90 dias da parcela vencida que pudesse ser um reenvio/pagamento oculto, e não
achei nenhum candidato com valor batendo (a diferença mais próxima foi um título 6% menor num mês diferente —
não é o mesmo lançamento). Ou seja: esses 7 continuam genuinamente em atraso, sem pagamento escondido.

O que a heurística **de fato** revelou foram **2 contratos novos** em atraso — CERENSA (`2024/00006`) e FISERV
(`2024/00013`) — que antes apareciam com outro status porque a parcela vencida e não paga desses contratos
estava órfã (sem `nCodCtr`) e por isso **nunca entrava** no cálculo de status do contrato. Religar essas
parcelas não fez elas "sumirem" — corrigiu um status que estava artificialmente otimista por falta do dado.
Isso é o resultado correto (mais preciso), mesmo não sendo o que a hipótese original previa.

## 9. Bug adicional encontrado e corrigido: cliente do contrato vinha da parcela errada

Investigando o caso da ADSMAIS (seção 5) apareceu um bug de verdade, não relacionado a título órfão: o cliente
exibido de um contrato vinha de `titulos_do_contrato[0]` (a parcela que por acaso vence primeiro), então um
único erro de digitação numa nota fiscal antiga e cancelada bastava para o contrato inteiro exibir o cliente
errado, mesmo com todas as parcelas seguintes corretas. **Corrigido**: o cliente agora vem do cadastro do
próprio contrato (`nCodCli` de `ListarContratos`) sempre que disponível.

## 10. Janela padrão cortava previsões futuras — corrigido (sem nenhum filtro de data)

Pergunta direta do usuário depois da primeira rodada: "você também está trazendo todos os títulos que estão em
previsão de contrato?". Resposta original: **não estava, no caso de uso real (janela padrão do
`main_dashboard.py`)**:

1. **Toda `PREVISAO_CONTRATO` sempre vem com `nCodCtr`** (51/51 na amostra ampla) — isso nunca foi o problema;
   a reconciliação heurística não se aplica aqui.
2. **O horizonte de previsão da Omie não é fixo** (não é sempre "próximos 3 meses"): contratos com vigência
   registrada mais longe já têm previsão gerada mais de um ano à frente — encontrados 18 registros de
   `PREVISAO_CONTRATO` de 4 contratos (`2024/00004`, `2025/00011`, `2025/00020`, `2026/00005`) com vencimento
   em 2027. A janela padrão original (`_janela_padrao()`, fim fixo em 31/12 do ano corrente) cortava essas
   previsões antes de chegarem no `ListarMovimentos`, em silêncio — sem nenhum aviso, sem aparecer como
   "0 previsões", simplesmente não existiam no JSON.

**O comportamento de `dDtVencAte` foi caracterizado errado numa primeira rodada de testes — corrigido depois de
um segundo teste pedido pelo usuário.** Eu tinha concluído que "omitir `dDtVencAte` faz o `ListarMovimentos`
devolver zero `PREVISAO_CONTRATO`". Isso é **impreciso**: rodando dois testes completos (paginando até o fim)
contra a conta real —

| Teste | `dDtVencDe` | `dDtVencAte` | Total bruto | `PREVISAO_CONTRATO` |
|---|---|---|---|---|
| 1 | *(nenhum)* | *(nenhum)* | 10.779 | **51** |
| 2 | `01/02/2026` | `09/07/2027` | 2.466 | **50** |
| 3 (teste extra, isolando a causa) | `01/02/2026` | *(nenhum)* | 2.089 | **0** |

A causa real não é "usar filtro de data corta previsão" nem "omitir o filtro corta previsão" — é a combinação
específica **`dDtVencDe` presente sem `dDtVencAte`** que faz a Omie devolver zero previsões, sem erro, sem
aviso. Sem filtro nenhum (Teste 1) funciona normalmente, e ainda captura 1 previsão a mais que o Teste 2 (a
mesma `02/08/2027` da CREDIMORAR, que ficava de fora só porque o `dDtVencAte=09/07/2027` do teste 2 não tinha
a margem de segurança que foi adicionada depois — não é uma nova inconsistência).

**Decisão final (a pedido do usuário): não usar nenhum filtro de data por padrão.** Em vez de tentar calcular
um fim de janela "seguro" a partir do cadastro (abordagem intermediária que cheguei a implementar, com margem
de 60 dias sobre a vigência mais distante), `main_dashboard.py` agora **não envia `dDtVencDe` nem `dDtVencAte`**
quando o usuário não passa esses argumentos — busca o histórico completo (todos os grupos: `CONTA_A_PAGAR`,
`CONTA_A_RECEBER`, `CONTA_CORRENTE_PAG/REC`, `PREVISAO_CONTRATO`) de uma vez, sem risco de cortar nada em
silêncio, ao custo de mais páginas por execução (110 páginas / ~5.100 títulos filtrados contra ~15-30 páginas
das janelas anteriores). `--data-inicio`/`--data-fim` continuam disponíveis para quem quiser um recorte menor
(relatório mais rápido) — `main_dashboard.py` agora **valida e recusa** passar só um dos dois, com uma mensagem
de erro explicando por quê (em vez de silenciosamente devolver zero previsões).

---

## O que foi implementado

- **`src/contratos_cadastro.py`** (novo) — busca todas as páginas de `ListarContratos` e devolve
  `nCodCtr -> {cNumCtr, nCodCli, cCodSit, status_cadastro, dVigInicial, dVigFinal, nDiaFat, nValTotMes}`.
  Mapeamento `cCodSit` confirmado na seção 4.
- **`src/contratos.py::montar_contratos`** — novo parâmetro opcional `contratos_cadastro`:
  - todo contrato cadastrado entra no resultado, mesmo sem nenhum título casado (`parcelas: []`,
    `status_contrato` cai para `status_cadastro`, `origem_status: "cadastro"`);
  - títulos sem `nCodCtr` passam por `_match_heuristico` antes de serem descartados (seção 8);
  - cada parcela ganha `"vinculo": "confirmado"|"heuristico"`;
  - cliente do contrato vem do cadastro (`nCodCli`), não mais da primeira parcela por vencimento (seção 9);
  - todo contrato ganha `status_cadastro` (rótulo de `cCodSit`) ao lado de `status_contrato`, sem um substituir
    o outro (seção 4).
- **`main_dashboard.py`** — busca o cadastro via `buscar_contratos_cadastro` e passa para `montar_contratos`;
  loga quantos contratos vieram só do cadastro e quantas parcelas foram religadas por heurística. **Sem
  `--data-inicio`/`--data-fim`, nenhum filtro de data é enviado** ao `ListarMovimentos` (seção 10) — busca o
  histórico completo, sem risco de cortar previsão futura. Se o usuário passar só um dos dois argumentos, o
  script recusa com uma mensagem explicando por quê (essa combinação faz a Omie devolver zero previsões).
- **Não mexido, por decisão explícita**: `movimentos.py` (allow-list de `cGrupo` já correta — seção 7), e
  nenhuma chamada em lote a `ListarOS`/`ConsultarOS`/`ConsultarContaReceber` (seção 3/item 3 confirmado pelo
  usuário — custo de rate limit não se paga, os campos já vêm de graça no título).
- **Testes**: `tests/test_contratos_offline.py` ganhou 4 casos novos — contrato cadastrado sem título casado,
  título órfão religado por heurística, título órfão implausível permanecendo órfão, e o marcador
  `vinculo="confirmado"` nos títulos que já vinham com `nCodCtr`. Suite completa passando.
- **Validado contra a conta real**: 60/60 contratos agora aparecem (antes 56); 42 parcelas religadas; ADSMAIS
  exibe o cliente certo; sem filtro de data, o `ListarMovimentos` devolve as 51 `PREVISAO_CONTRATO` existentes,
  incluindo a de 02/08/2027 da CREDIMORAR.
