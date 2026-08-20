// financas/extrato · ListarExtrato, DUAS chamadas por conta corrente
// configurada (uma janela pra frente, outra pra trás -- ver "DiasPrevisao"/
// "DiasHistorico" abaixo). NÃO carregue esta consulta numa planilha (desligue
// "Habilitar Carregamento", botão direito na consulta no painel Consultas) --
// ela devolve um record com as 4 partes do resultado; "CaixaSaldoPorConta",
// "CaixaLancamentosPrevistos", "CaixaFluxoSemanal" e "CaixaExtratoDiario"
// referenciam cada uma o campo delas, assim as chamadas à API rodam só 1 vez
// por atualização, não 1 vez por consulta de saída. Depende de "fnOmieCall" e
// "ContaCorrenteMap" -- crie essas duas antes.
//
// Antes de usar, configure "ContasCaixaTexto" abaixo (editável direto no
// Editor Avançado).
//
// Achados empíricos sobre o endpoint ListarExtrato:
// - "nSaldoAnterior" da resposta é o saldo já conciliado do fechamento do dia
//   ANTERIOR a dPeriodoInicial -- consultando com dPeriodoInicial=hoje, isso
//   já é "o saldo real de hoje";
// - "listaMovimentos" mistura 3 tipos de linha, distinguíveis por
//   "cSituacao": "Conciliado" (lançamento já baixado no banco -- de verdade),
//   "Previsto" (a vencer OU já vencido em aberto -- não dá pra saber qual só
//   pelo cSituacao, precisa checar a data) e ausente/null (marcador de saldo
//   do dia, "cDesCliente"="SALDO" -- ou "SALDO ANTERIOR" no primeiro, que é
//   sempre o dia ANTES de dPeriodoInicial). Confirmado por sonda: existe
//   exatamente 1 marcador de saldo por dia do período, mesmo em dias sem
//   nenhum lançamento, sempre depois dos lançamentos do dia na ordem da
//   resposta;
// - cada linha (lançamento OU marcador) já traz o saldo acumulado até ali em
//   "nSaldo" (só "Conciliado" -- "Previsto" não avança esse campo) e
//   "nSaldoPrev" ("Conciliado" + "Previsto" -- inclui pendente). É esse saldo
//   por linha que "CaixaExtratoDiario" usa como "saldo do dia", não um
//   cálculo manual;
// - cSituacao="Previsto" também cobre títulos JÁ vencidos e em aberto (não só
//   "a vencer") -- por isso "LancamentosPrevistos" (a vencer) filtra também
//   por dDataLancamento >= hoje; "ExtratoDiario" (histórico) não precisa
//   dessa checagem porque olha só pra trás;
// - uma janela de datas larga pode devolver listaMovimentos vazio pra alguma
//   conta específica mesmo com lançamentos existindo -- isso não é tratado
//   automaticamente aqui; se uma conta parecer estar perdendo lançamentos
//   numa janela larga, reduza DiasPrevisao/DiasHistorico.
let
    // ---- Configuração (edite aqui) ----
    // Lista separada por vírgula de contas correntes a considerar no saldo
    // de caixa: "nome exato no cadastro Omie:rótulo,nome exato no cadastro:rótulo,...".
    // O rótulo é opcional -- se omitido (sem ":"), usa o próprio nome do
    // cadastro. Precisa bater EXATAMENTE com "ContaCorrente_Descricao" de
    // ContaCorrenteMap.
    ContasCaixaTexto = "",
    // Quantos dias à frente projetar (CaixaLancamentosPrevistos/CaixaFluxoSemanal).
    DiasPrevisao = 90,
    // Quantos dias pra trás trazer no extrato diário (CaixaExtratoDiario).
    DiasHistorico = 30,

    // ---------- Funções auxiliares (mesma lógica das funções homônimas em "Movimentacoes", repetida aqui porque cada consulta é independente) ----------
    fnNum = (valor as any) as number =>
        if valor = null then 0
        else if Value.Is(valor, type number) then valor
        else try Number.From(valor) otherwise 0,

    fnSomaSegura = (lista as list) as number => if List.IsEmpty(lista) then 0 else List.Sum(lista),

    fnParaData = (texto as any) as nullable date =>
        if texto = null or texto = "" then null
        else
            let
                partes = Text.Split(Text.From(texto), "/"),
                valido = List.Count(partes) = 3
            in
                if not valido then null
                else try #date(Number.From(partes{2}), Number.From(partes{1}), Number.From(partes{0})) otherwise null,

    fnDataTexto = (d as date) as text =>
        Text.PadStart(Text.From(Date.Day(d)), 2, "0") & "/" &
        Text.PadStart(Text.From(Date.Month(d)), 2, "0") & "/" &
        Text.From(Date.Year(d)),

    // ---------- Config das contas ----------
    // Só o primeiro ":" separa nome de rótulo -- um rótulo que contenha ":"
    // não quebra o nome da conta.
    fnParConta = (item as text) as record =>
        let
            limpo = Text.Trim(item),
            pos = Text.PositionOf(limpo, ":"),
            nome = Text.Trim(if pos = -1 then limpo else Text.Start(limpo, pos)),
            rotuloBruto = if pos = -1 then "" else Text.Trim(Text.Range(limpo, pos + 1)),
            rotulo = if rotuloBruto = "" then nome else rotuloBruto
        in
            [ Nome = nome, Rotulo = rotulo ],

    ContasConfiguradas =
        let
            lista = List.Transform(
                List.Select(Text.Split(ContasCaixaTexto, ","), each Text.Trim(_) <> ""),
                each fnParConta(_)
            )
        in
            if List.IsEmpty(lista) then
                error "ContasCaixaTexto não configurado -- defina as contas correntes a considerar no saldo de caixa."
            else
                lista,

    // Resolve nCodCC por correspondência EXATA com ContaCorrenteMap -- falha
    // explícita se não achar, em vez de silenciar uma conta ausente do
    // cálculo de saldo.
    ContasResolvidas = Table.AddColumn(
        Table.FromRecords(ContasConfiguradas),
        "nCodCC",
        each
            let
                nomeAlvo = [Nome],
                achado = Table.SelectRows(ContaCorrenteMap, each [ContaCorrente_Descricao] = nomeAlvo)
            in
                if Table.IsEmpty(achado) then
                    error "Conta corrente não encontrada no cadastro Omie: " & nomeAlvo & ". Verifique ContasCaixaTexto contra os nomes cadastrados em ContaCorrenteMap."
                else
                    achado{0}[nCodCC]
    ),

    // ---------- Busca do extrato (2 chamadas por conta, sem paginação) ----------
    Hoje = DateTime.Date(DateTime.LocalNow()),
    HojeTexto = fnDataTexto(Hoje),
    FimTexto = fnDataTexto(Date.AddDays(Hoje, DiasPrevisao)),
    HistoricoInicioData = Date.AddDays(Hoje, -DiasHistorico),
    HistoricoInicioTexto = fnDataTexto(HistoricoInicioData),

    fnBuscarExtratoPeriodo = (nCodCC as number, inicio as text, fim as text) as record =>
        fnOmieCall("financas/extrato", "ListarExtrato", [ nCodCC = nCodCC, dPeriodoInicial = inicio, dPeriodoFinal = fim ]),

    // Janela pra frente (hoje -> hoje+DiasPrevisao) -- usada por SaldoReal/Lancamentos/FluxoSemanal.
    fnBuscarExtrato = (nCodCC as number) as record => fnBuscarExtratoPeriodo(nCodCC, HojeTexto, FimTexto),
    // Janela pra trás (hoje-DiasHistorico -> hoje) -- usada só por ExtratoDiario.
    fnBuscarExtratoHistorico = (nCodCC as number) as record => fnBuscarExtratoPeriodo(nCodCC, HistoricoInicioTexto, HojeTexto),

    ContasComExtrato = Table.AddColumn(ContasResolvidas, "Extrato", each fnBuscarExtrato([nCodCC])),
    ContasComSaldo = Table.AddColumn(ContasComExtrato, "SaldoReal", each fnNum(Record.FieldOrDefault([Extrato], "nSaldoAnterior", 0))),

    // ---------- Lançamentos previstos "a vencer" ----------
    fnLancamentosConta = (extrato as record, rotulo as text) as list =>
        let
            movs = Record.FieldOrDefault(extrato, "listaMovimentos", {}),
            filtrados = List.Select(movs, each
                Record.FieldOrDefault(_, "cSituacao", null) = "Previsto"
                and (let d = fnParaData(Record.FieldOrDefault(_, "dDataLancamento", null)) in d <> null and d >= Hoje)
            )
        in
            List.Transform(filtrados, each
                let
                    razCliente = Record.FieldOrDefault(_, "cRazCliente", null),
                    desCliente = Record.FieldOrDefault(_, "cDesCliente", null),
                    categoria = Record.FieldOrDefault(_, "cDesCategoria", null)
                in
                    [
                        Conta = rotulo,
                        Data = Record.FieldOrDefault(_, "dDataLancamento", null),
                        Natureza = Record.FieldOrDefault(_, "cNatureza", null),
                        ClienteFornecedor = if razCliente <> null and razCliente <> "" then razCliente else if desCliente <> null and desCliente <> "" then desCliente else "",
                        Categoria = if categoria <> null then categoria else "",
                        Valor = fnNum(Record.FieldOrDefault(_, "nValorDocumento", 0))
                    ]
            ),

    ContasComLancamentos = Table.AddColumn(ContasComSaldo, "Lancamentos", each fnLancamentosConta([Extrato], [Rotulo])),

    SaldoRealTotal = List.Sum(ContasComLancamentos[SaldoReal]),
    TodosLancamentos = List.Combine(ContasComLancamentos[Lancamentos]),

    // ---------- Extrato diário (lançamentos do dia + saldo de fechamento do dia) ----------
    // Encadeada a partir de "ContasComLancamentos" (não de "ContasResolvidas"
    // direto) de propósito: assim a busca histórica só começa depois que a
    // busca pra frente (linha acima) já terminou pra TODAS as contas, em vez
    // de virar um segundo binding independente que o M poderia avaliar em
    // paralelo com o primeiro -- as duas batem no mesmo método
    // (financas/extrato · ListarExtrato), sujeito ao mesmo limite de taxa por
    // IP+app_key+método.
    ContasComExtratoHistorico = Table.AddColumn(ContasComLancamentos, "ExtratoHistorico", each fnBuscarExtratoHistorico([nCodCC])),

    // Cada item de "listaMovimentos" vira uma linha de "Lançamento" (cSituacao
    // "Conciliado" ou "Previsto") ou uma linha de "Saldo do Dia" (cSituacao
    // ausente/null -- o marcador diário nativo do endpoint, ver comentário no
    // topo do arquivo). "_Ordem"/"_Hora" são só pra ordenação (removidas no
    // final) -- garantem lançamentos antes do saldo do dia mesmo se a ordem
    // bruta da API não for 100% confiável.
    fnLinhasExtratoDiario = (extrato as record, rotulo as text) as list =>
        let
            movs = Record.FieldOrDefault(extrato, "listaMovimentos", {}),
            // Descarta o marcador "SALDO ANTERIOR" (sempre o dia ANTES de
            // dPeriodoInicial) -- está fora da janela pedida, é só o saldo de
            // arrasto que "SaldoReal"/"nSaldoAnterior" já cobre em outro lugar.
            dentroDaJanela = List.Select(movs, each
                let d = fnParaData(Record.FieldOrDefault(_, "dDataLancamento", null))
                in d <> null and d >= HistoricoInicioData
            )
        in
            List.Transform(dentroDaJanela, each
                let
                    situacao = Record.FieldOrDefault(_, "cSituacao", null),
                    data = fnParaData(Record.FieldOrDefault(_, "dDataLancamento", null))
                in
                    if situacao = null then
                        [
                            Conta = rotulo, Data = data, Tipo = "Saldo do Dia", Situacao = "",
                            Natureza = "", ClienteFornecedor = "", Categoria = "",
                            DocumentoFiscal = "", Parcela = "", Origem = "", Valor = null,
                            SaldoReal = fnNum(Record.FieldOrDefault(_, "nSaldo", 0)),
                            SaldoPrevisto = fnNum(Record.FieldOrDefault(_, "nSaldoPrev", 0)),
                            _Ordem = 1, _Hora = "99:99:99"
                        ]
                    else
                        let
                            razCliente = Record.FieldOrDefault(_, "cRazCliente", null),
                            desCliente = Record.FieldOrDefault(_, "cDesCliente", null),
                            hora = Record.FieldOrDefault(_, "cHoraInclusao", null)
                        in
                            [
                                Conta = rotulo, Data = data, Tipo = "Lançamento", Situacao = situacao,
                                Natureza = Record.FieldOrDefault(_, "cNatureza", ""),
                                ClienteFornecedor = if razCliente <> null and razCliente <> "" then razCliente else if desCliente <> null and desCliente <> "" then desCliente else "",
                                Categoria = Record.FieldOrDefault(_, "cDesCategoria", ""),
                                DocumentoFiscal = Record.FieldOrDefault(_, "cDocumentoFiscal", ""),
                                Parcela = Record.FieldOrDefault(_, "cParcela", ""),
                                Origem = Record.FieldOrDefault(_, "cOrigem", ""),
                                Valor = fnNum(Record.FieldOrDefault(_, "nValorDocumento", 0)),
                                SaldoReal = null, SaldoPrevisto = null,
                                _Ordem = 0, _Hora = if hora = null then "00:00:00" else hora
                            ]
            ),

    ContasComExtratoDiario = Table.AddColumn(ContasComExtratoHistorico, "LinhasExtratoDiario", each fnLinhasExtratoDiario([ExtratoHistorico], [Rotulo])),
    TodasLinhasExtratoDiario = List.Combine(ContasComExtratoDiario[LinhasExtratoDiario]),

    // ---------- Fluxo semanal projetado (segunda a domingo, saldo acumulado) ----------
    // Inclui semanas sem nenhum lançamento -- o saldo acumulado não pode
    // "pular" períodos.
    InicioSemanaAtual = Date.StartOfWeek(Hoje, Day.Monday),
    FimHorizonte = Date.AddDays(Hoje, DiasPrevisao),
    Semanas = List.Generate(() => InicioSemanaAtual, each _ <= FimHorizonte, each Date.AddDays(_, 7)),

    EstadoFluxo = List.Accumulate(
        Semanas,
        [ Acumulado = SaldoRealTotal, Linhas = {} ],
        (estado, semanaInicio) =>
            let
                semanaFim = Date.AddDays(semanaInicio, 6),
                itens = List.Select(TodosLancamentos, each
                    let d = fnParaData([Data]) in d <> null and d >= semanaInicio and d <= semanaFim
                ),
                entradas = fnSomaSegura(List.Transform(List.Select(itens, each [Valor] >= 0), each [Valor])),
                saidas = fnSomaSegura(List.Transform(List.Select(itens, each [Valor] < 0), each [Valor])),
                liquido = entradas + saidas,
                novoAcumulado = estado[Acumulado] + liquido,
                linha = [
                    Inicio = semanaInicio,
                    Fim = semanaFim,
                    EntradasPrevistas = Number.Round(entradas, 2),
                    SaidasPrevistas = Number.Round(saidas, 2),
                    LiquidoPrevisto = Number.Round(liquido, 2),
                    SaldoPrevistoAcumulado = Number.Round(novoAcumulado, 2)
                ]
            in
                [ Acumulado = novoAcumulado, Linhas = estado[Linhas] & { linha } ]
    ),

    ExtratoDiarioBruto = Table.FromRecords(TodasLinhasExtratoDiario, {
        "Conta", "Data", "Tipo", "Situacao", "Natureza", "ClienteFornecedor", "Categoria",
        "DocumentoFiscal", "Parcela", "Origem", "Valor", "SaldoReal", "SaldoPrevisto", "_Ordem", "_Hora"
    }),
    ExtratoDiarioOrdenado = Table.Sort(ExtratoDiarioBruto, {
        {"Conta", Order.Ascending}, {"Data", Order.Ascending}, {"_Ordem", Order.Ascending}, {"_Hora", Order.Ascending}
    }),
    // "Valor"/"SaldoReal"/"SaldoPrevisto" misturam number e null (cada linha
    // só preenche um dos dois grupos de coluna) -- sem forçar o tipo aqui, a
    // inferência automática do Table.FromRecords tende a cair pra "any", e o
    // Excel trata a coluna como texto (quebra ordenação/formatação numérica).
    ExtratoDiarioTipado = Table.TransformColumnTypes(ExtratoDiarioOrdenado, {
        {"Data", type date}, {"Valor", type nullable number},
        {"SaldoReal", type nullable number}, {"SaldoPrevisto", type nullable number}
    }),
    ExtratoDiario = Table.RenameColumns(
        Table.RemoveColumns(ExtratoDiarioTipado, {"_Ordem", "_Hora"}),
        {
            {"Tipo", "Tipo de Linha"}, {"Situacao", "Situação"}, {"ClienteFornecedor", "Cliente ou Fornecedor"},
            {"DocumentoFiscal", "Documento Fiscal"}, {"SaldoReal", "Saldo Real (Conciliado)"},
            {"SaldoPrevisto", "Saldo Previsto (c/ Pendentes)"}
        }
    ),

    // ---------- 4 saídas (cada consulta "Caixa*" lê um campo destes) ----------
    SaldoPorConta = Table.SelectColumns(
        Table.RenameColumns(ContasComLancamentos, {{"Rotulo", "Conta"}, {"SaldoReal", "Saldo Real"}}),
        {"Conta", "Saldo Real"}
    ),

    LancamentosPrevistosBruto = Table.FromRecords(TodosLancamentos, {"Conta", "Data", "Natureza", "ClienteFornecedor", "Categoria", "Valor"}),
    LancamentosComOrdenacao = Table.AddColumn(LancamentosPrevistosBruto, "DataOrdenacao", each fnParaData([Data]), type nullable date),
    LancamentosOrdenados = Table.Sort(LancamentosComOrdenacao, {{"DataOrdenacao", Order.Ascending}, {"Conta", Order.Ascending}}),
    LancamentosPrevistos = Table.RenameColumns(
        Table.RemoveColumns(LancamentosOrdenados, {"DataOrdenacao"}),
        {{"ClienteFornecedor", "Cliente ou Fornecedor"}}
    ),

    // "Início"/"Fim" ficam como data nativa do Excel -- mais útil pra
    // ordenar/formatar na planilha.
    FluxoSemanal = Table.RenameColumns(
        Table.FromRecords(EstadoFluxo[Linhas], {"Inicio", "Fim", "EntradasPrevistas", "SaidasPrevistas", "LiquidoPrevisto", "SaldoPrevistoAcumulado"}),
        {
            {"Inicio", "Início"}, {"EntradasPrevistas", "Entradas Previstas"},
            {"SaidasPrevistas", "Saídas Previstas"}, {"LiquidoPrevisto", "Líquido Previsto"},
            {"SaldoPrevistoAcumulado", "Saldo Previsto Acumulado"}
        }
    ),

    Resultado = [ SaldoPorConta = SaldoPorConta, LancamentosPrevistos = LancamentosPrevistos, FluxoSemanal = FluxoSemanal, ExtratoDiario = ExtratoDiario ]
in
    Resultado
