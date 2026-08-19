// financas/extrato · ListarExtrato, uma chamada por conta corrente
// configurada. NÃO carregue esta consulta numa planilha (desligue "Habilitar
// Carregamento", botão direito na consulta no painel Consultas) -- ela
// devolve um record com as 3 partes do resultado; "CaixaSaldoPorConta",
// "CaixaLancamentosPrevistos" e "CaixaFluxoSemanal" referenciam cada uma o
// campo delas, assim as chamadas à API rodam só 1 vez por atualização, não 1
// vez por consulta de saída. Depende de "fnOmieCall" e "ContaCorrenteMap" --
// crie essas duas antes.
//
// Antes de usar, configure "ContasCaixaTexto" abaixo (editável direto no
// Editor Avançado).
//
// Achados empíricos sobre o endpoint ListarExtrato:
// - "nSaldoAnterior" da resposta é o saldo já conciliado do fechamento do dia
//   ANTERIOR a dPeriodoInicial -- consultando com dPeriodoInicial=hoje, isso
//   já é "o saldo real de hoje";
// - "listaMovimentos" tem linhas que não são lançamentos de verdade
//   (marcadores de saldo diário) -- filtramos só cSituacao="Previsto";
// - cSituacao="Previsto" também cobre títulos JÁ vencidos e em aberto (não só
//   "a vencer") -- por isso filtramos também por dDataLancamento >= hoje;
// - uma janela de datas larga pode devolver listaMovimentos vazio pra alguma
//   conta específica mesmo com lançamentos existindo -- isso não é tratado
//   automaticamente aqui; se uma conta parecer estar perdendo lançamentos
//   numa janela larga, reduza DiasPrevisao.
let
    // ---- Configuração (edite aqui) ----
    // Lista separada por vírgula de contas correntes a considerar no saldo
    // de caixa: "nome exato no cadastro Omie:rótulo,nome exato no cadastro:rótulo,...".
    // O rótulo é opcional -- se omitido (sem ":"), usa o próprio nome do
    // cadastro. Precisa bater EXATAMENTE com "ContaCorrente_Descricao" de
    // ContaCorrenteMap.
    ContasCaixaTexto = "",
    // Quantos dias à frente projetar.
    DiasPrevisao = 90,

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

    // ---------- Busca do extrato (1 chamada por conta, sem paginação) ----------
    Hoje = DateTime.Date(DateTime.LocalNow()),
    HojeTexto = fnDataTexto(Hoje),
    FimTexto = fnDataTexto(Date.AddDays(Hoje, DiasPrevisao)),

    fnBuscarExtrato = (nCodCC as number) as record =>
        fnOmieCall("financas/extrato", "ListarExtrato", [ nCodCC = nCodCC, dPeriodoInicial = HojeTexto, dPeriodoFinal = FimTexto ]),

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

    // ---------- 3 saídas (cada consulta "Caixa*" lê um campo destes) ----------
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

    Resultado = [ SaldoPorConta = SaldoPorConta, LancamentosPrevistos = LancamentosPrevistos, FluxoSemanal = FluxoSemanal ]
in
    Resultado
