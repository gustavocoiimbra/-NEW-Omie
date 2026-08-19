# Propostas de Automação — Redução de Trabalho Manual

> Documento de discussão futura. Nenhum item aqui foi implementado ou
> aprovado — é um registro de ideias levantadas em 17/08/2026, a partir da
> constatação de que, mesmo com a extração de dados automatizada, ainda
> sobra trabalho manual pro colaborador que cuida da planilha (lançamentos,
> conferência, atualização).

## Contexto

O que já está automatizado (relatórios em Excel, painéis de contratos e de
caixa, planilha sempre atualizada, acesso direto pelo Excel) cobre a
**extração e organização do dado** vindo da Omie. O trabalho manual que
sobra hoje é principalmente de **triagem, decisão e comunicação** — coisas
que exigem alguém abrir a planilha/painel, procurar o que precisa de
atenção, e agir (ou avisar quem precisa agir).

As propostas abaixo estão separadas em dois grupos, porque têm perfis de
risco bem diferentes:

- **Só leitura/aviso** — encaixam direto no que já existe hoje (a extração
  de dados já é 100% leitura da Omie); o risco de um erro aqui é, no
  máximo, um alerta impreciso.
- **Ação de volta na Omie ou no cliente** — mudam de categoria de risco,
  porque passam a escrever na Omie ou a se comunicar direto com o cliente
  final; um erro aqui tem consequência externa, não só interna.

## Automações de leitura/aviso (baixo risco)

| Proposta | Problema que resolve |
|---|---|
| Notificação automática dos itens sinalizados pra revisão manual | O painel de contratos já identifica títulos que não conseguiu religar a um contrato com confiança, mas isso só aparece se alguém abrir o JSON/planilha e procurar. |
| Alerta de vencimento e atraso | Cobranças vencendo nos próximos dias e títulos atrasados há mais tempo do que o aceitável, sem depender de alguém abrir a planilha pra descobrir. |
| Alerta de caixa projetado negativo | Se o fluxo semanal projetar saldo negativo em alguma semana à frente, avisar automaticamente em vez de depender de checagem manual do painel. |
| Relatório de divergência de conciliação | Título marcado como pago/recebido sem uma baixa bancária correspondente (ou o contrário) — sinal de lançamento possivelmente errado na Omie. Foi assim, cruzando dados manualmente, que identificamos o padrão das baixas de rendimento de aplicação automática (ver `GLOSSARIO.md`). |
| Aviso de contrato perto do fim de vigência | Dá tempo de decidir renovação antes do contrato expirar, em vez de descobrir depois que já venceu. |
| Checagem de cadastro incompleto | Cliente sem CNPJ, contrato sem categoria — sinalizado antes de virar problema no relatório. |

## Automações de ação (maior impacto, maior cuidado)

| Proposta | Observação |
|---|---|
| Geração automática de cobrança pra títulos atrasados (boleto/PIX) | Tudo que existe hoje é só leitura da Omie, de propósito. Automatizar uma ação de escrita é uma mudança de categoria de risco — vale tratar como decisão separada, com validação extra antes de qualquer execução automática. |
| Envio automático de lembrete de cobrança ao cliente (e-mail/WhatsApp) | Mesma ressalva acima — comunicação direta com o cliente final exige mais cuidado com falso positivo. |
| Snapshot/versionamento histórico da planilha | A planilha sempre atualizada sobrescreve sempre o mesmo arquivo; guardar uma cópia datada periodicamente permitiria auditoria de "como estava em tal data" sem depender de backup manual. Risco baixo (não escreve na Omie nem fala com cliente), mas envolve mudança de processo de arquivo, por isso ficou nesse grupo. |

## Opção de arquitetura para o painel do cliente: GitHub Actions + WhatsApp

Levantada em 17/08/2026 como possível caminho pro item "painel para o
cliente final" (ainda em decisão, ver relatório publicado). A ideia: um
job agendado no GitHub Actions consulta a Omie, monta um resumo (dashboard)
e envia automaticamente pro WhatsApp do cliente via uma API de terceiro
(Z-API).

**Vantagem real:** reaproveita a lógica Python existente sem reescrever
nada (diferente das opções via n8n/Apps Script, que exigiriam portar a
reconciliação de contratos pra JavaScript) e não precisa de servidor
sempre ligado — o GitHub Actions liga, roda e desliga, sem custo de
hospedagem pra esse uso leve e agendado.

**Risco identificado — não é a API oficial do WhatsApp.** O Z-API conecta
via QR Code (mesmo mecanismo do WhatsApp Web), não pela API oficial da
Meta. A partir de 15/01/2026 a Meta passou a bloquear ativamente esse tipo
de conexão: estimativas de mercado apontam entre 40% e 60% das contas
conectadas por APIs alternativas suspensas só no 1º trimestre de 2026,
algumas em até 48h. Ou seja, risco real de perder o número de WhatsApp
usado — não teórico.

**Alternativa mais segura:** a API oficial do WhatsApp Business (Cloud
API, da própria Meta) não tem esse risco de banimento, mas exige mais
configuração inicial (verificação de negócio, aprovação de modelo de
mensagem pra iniciar conversa) e pode ter custo por conversa iniciada pela empresa.

**Ficou também em aberto, mesmo se a decisão for seguir por essa via:** o
que exatamente é "o dashboard" enviado — WhatsApp não renderiza HTML, então
seria uma imagem gerada, um PDF anexado, ou uma mensagem de texto
formatada, cada uma com uma complexidade de construção diferente.

## Para discussão

- Priorizar quais dessas entram primeiro — provavelmente as de leitura/aviso
  antes de qualquer automação de ação.
- Definir canal de notificação (e-mail, WhatsApp, Slack) e frequência.
- Para o grupo de ação: decidir quem aprova cada automação de escrita antes
  de ligar, e que critério de confiança mínimo é exigido (ex.: só cobrança
  de títulos com vínculo `"confirmado"`, nunca `"heuristico"`).
- Painel do cliente: decidir entre aceitar o risco de banimento do Z-API
  (ex.: testando primeiro com um número secundário) ou partir direto pra
  API oficial da Meta.
