// Junta financas/pesquisartitulos · PesquisarLancamentos (contas a pagar e a
// receber já lançadas, buscadas por natureza P e R, com rateio de categoria
// expandido) com financas/mf · ListarMovimentos (só as previsões de
// faturamento de contrato, cGrupo="PREVISAO_CONTRATO", que não existem como
// título formal em PesquisarLancamentos).
//
// PesquisarLancamentos traz o rateio de categoria (aCodCateg[]) e o detalhe
// de pagamento completo (resumo: valor pago, valor em aberto, desconto,
// juros, multa) na mesma chamada, por título. lancamentos[].cObsLanc (o
// texto registrado na baixa bancária) também vem nessa chamada e preenche a
// coluna "Observação do Pagto ou Recbto".
//
// Depende de "fnOmieCall", "CategoriaMap", "ContaCorrenteMap" e "ClienteMap"
// -- crie essas quatro antes.
//
// Notas sobre o resultado:
// - "Situação do Vencimento" é calculada com a data local do computador no
//   momento do refresh (DateTime.LocalNow());
// - na ordenação por "Data de Vencimento (completa)", linhas sem data
//   preenchida vêm primeiro;
// - "Observação da Conta" decodifica as entidades HTML mais comuns (&amp;
//   &lt; &gt; &quot; &#39;);
// - "cod.fcx" fica sempre em branco -- não há endpoint da Omie que devolva
//   esse código;
// - "Observação do Pagto ou Recbto" fica em branco pra previsões de contrato
//   (não têm baixa bancária associada) e pra títulos cuja baixa não tem
//   texto registrado na Omie;
// - um título com rateio de categoria real gera uma linha por categoria,
//   cada uma com o valor proporcional -- o resultado não tem necessariamente
//   uma linha por título.
let
    // ---- Parâmetros de data opcionais (deixe "" pra não filtrar) ----
    DataVencDe = "",
    DataVencAte = "",

    // ---------- Funções auxiliares ----------
    // Acesso seguro a um campo dinâmico da API (pode não existir na linha, ou
    // nem existir na tabela inteira se nenhum título da página atual tiver
    // esse campo) -- usar [nomeCampo] direto quebraria nesse segundo caso.
    fnCampo = (linha as record, nome as text) as any => Record.FieldOrDefault(linha, nome, null),

    fnNum = (valor as any) as number =>
        if valor = null then 0
        else if Value.Is(valor, type number) then valor
        else try Number.From(valor) otherwise 0,

    // Zera o valor de uma retenção quando o flag correspondente (cRetXXX) é
    // explicitamente "N" -- o valor calculado pode vir preenchido mesmo com
    // a retenção desativada, então só o flag decide se ela conta ou não.
    fnNumRetido = (linha as record, campoValor as text, campoFlag as text) as number =>
        if fnCampo(linha, campoFlag) = "N" then 0 else fnNum(fnCampo(linha, campoValor)),

    // "|" (separador interno usado pela Omie em observações geradas por
    // importação de extrato) vira espaço; decodifica as entidades HTML mais
    // comuns.
    fnObservacao = (linha as record) as text =>
        let
            bruta = fnCampo(linha, "observacao"),
            texto = if bruta = null then "" else Text.From(bruta),
            semBarra = Text.Replace(texto, "|", " "),
            semAmp = Text.Replace(semBarra, "&amp;", "&"),
            semLt = Text.Replace(semAmp, "&lt;", "<"),
            semGt = Text.Replace(semLt, "&gt;", ">"),
            semAspas = Text.Replace(semGt, "&quot;", """"),
            semApostrofo = Text.Replace(semAspas, "&#39;", "'")
        in
            semApostrofo,

    // Datas da Omie vêm como texto "dd/mm/aaaa" -- converte para o tipo date.
    fnParaData = (texto as any) as nullable date =>
        if texto = null or texto = "" then null
        else
            let
                partes = Text.Split(Text.From(texto), "/"),
                valido = List.Count(partes) = 3
            in
                if not valido then null
                else try #date(Number.From(partes{2}), Number.From(partes{1}), Number.From(partes{0})) otherwise null,

    // Classifica o vencimento do título num dos buckets do modelo padrão de
    // exportação da Omie: liquidado (pago/recebido), cancelado, vencido em
    // faixas de dias, ou a vencer em faixas de dias.
    fnSituacaoVencimento = (linha as record, natureza as text, hoje as date) as text =>
        let
            cStatus = fnCampo(linha, "cStatus"),
            cLiquidado = fnCampo(linha, "Resumo_cLiquidado"),
            venc = fnParaData(fnCampo(linha, "dDtVenc"))
        in
            if cStatus = "CANCELADO" then "Cancelado"
            else if cLiquidado = "S" then (if natureza = "R" then "Recebido" else "Pago")
            else if venc = null then ""
            else
                let dias = Duration.Days(venc - hoje) in
                    if dias < 0 then
                        let atraso = -dias in
                            if atraso <= 30 then "Vencido até 30 dias"
                            else if atraso <= 60 then "Vencido de 31 a 60 dias"
                            else if atraso <= 90 then "Vencido de 61 a 90 dias"
                            else "Vencido mais de 90 dias"
                    else if dias = 0 then "Vence hoje"
                    else if dias = 1 then "Vence amanhã"
                    else if dias <= 30 then "A vencer até 30 dias"
                    else if dias <= 60 then "A vencer de 31 até 60 dias"
                    else if dias <= 90 then "A vencer de 61 até 90 dias"
                    else "A vencer mais de 90 dias",

    // ---------- Busca paginada: PesquisarLancamentos (1 natureza por vez) ----------
    // PesquisarLancamentos exige "cNatureza" por chamada -- P e R são duas
    // buscas paginadas independentes.
    fnParamPaginaTitulos = (pagina as number, natureza as text) as record =>
        let
            Base = [ nPagina = pagina, nRegPorPagina = 100, cOrdenarPor = "CODIGO", cNatureza = natureza ],
            ComInicio = if DataVencDe <> "" then Record.AddField(Base, "dDtVencDe", DataVencDe) else Base,
            ComFim = if DataVencAte <> "" then Record.AddField(ComInicio, "dDtVencAte", DataVencAte) else ComInicio
        in
            ComFim,

    fnPaginaTitulos = (pagina as number, natureza as text) as record =>
        fnOmieCall("financas/pesquisartitulos", "PesquisarLancamentos", fnParamPaginaTitulos(pagina, natureza)),

    // PesquisarLancamentos usa "nTotPaginas" (igual a ListarMovimentos), NÃO
    // "total_de_paginas" (esse é só dos endpoints geral/*). Mesmo padrão de
    // "next" das outras consultas -- evita buscar a página seguinte quando a
    // atual já indica que era a última.
    fnBuscarTitulosPorNatureza = (natureza as text) as list =>
        let
            Paginas = List.Generate(
                () => [ i = 1, resp = fnPaginaTitulos(1, natureza) ],
                each [resp] <> null,
                each
                    let totalPaginas = Number.RoundUp(Record.FieldOrDefault([resp], "nTotPaginas", 1))
                    in
                        if [i] + 1 > totalPaginas then [ i = [i] + 1, resp = null ]
                        else [ i = [i] + 1, resp = fnPaginaTitulos([i] + 1, natureza) ],
                each [resp]
            )
        in
            List.Combine(List.Transform(Paginas, each Record.FieldOrDefault(_, "titulosEncontrados", {}))),

    // "P" e "R" são buscas de "fnBuscarTitulosPorNatureza" para o MESMO método
    // (PesquisarLancamentos), que a Omie limita por IP+app_key+método (4
    // req/s -- ver fnOmieCall.pq). Ligar as duas como dois passos
    // independentes do "let" não garante ordem: o M pode avaliar bindings sem
    // dependência entre si em paralelo, o que somaria as duas paginações
    // contra o mesmo limite ao mesmo tempo. List.Accumulate força R a só
    // começar depois que P terminar (cada passo depende do acumulado do
    // anterior), serializando as duas buscas.
    TodosTitulos = List.Accumulate(
        {"P", "R"},
        {},
        (estado, natureza) => estado & fnBuscarTitulosPorNatureza(natureza)
    ),

    // ---------- Expande rateio (aCodCateg) -- 1 item por categoria do título ----------
    // Cada título de PesquisarLancamentos pode estar dividido entre várias
    // categorias (aCodCateg[], com percentual e valor por categoria); sem
    // expandir isso, um título rateado apareceria só com uma categoria e o
    // valor cheio, escondendo a divisão real. Quando não há rateio,
    // aCodCateg tem uma única entrada com 100%.
    //
    // "cObsLancPagto" (texto de lancamentos[].cObsLanc, concatenado com " | "
    // quando há mais de um lançamento de baixa) se aplica ao título inteiro,
    // não a uma categoria específica dele -- por isso é copiado igual pra
    // cada linha expandida.
    fnObsPagamento = (item as record) as text =>
        let
            lancs = Record.FieldOrDefault(item, "lancamentos", {}),
            textos = List.Select(
                List.Transform(lancs, each Record.FieldOrDefault(_, "cObsLanc", null)),
                each _ <> null and _ <> ""
            ),
            distintos = List.Distinct(textos)
        in
            Text.Combine(distintos, " | "),

    fnExpandirTitulo = (item as record) as list =>
        let
            cabecBruto = Record.FieldOrDefault(item, "cabecTitulo", null),
            cabecOrig = if cabecBruto = null then [] else cabecBruto,
            resumoBruto = Record.FieldOrDefault(item, "resumo", null),
            resumoOrig = if resumoBruto = null then [] else resumoBruto,
            aCodCategBruto = Record.FieldOrDefault(cabecOrig, "aCodCateg", null),
            aCodCateg = if aCodCategBruto = null or List.IsEmpty(aCodCategBruto) then
                {
                    [
                        cCodCateg = Record.FieldOrDefault(cabecOrig, "cCodCateg", null),
                        nPerc = 100,
                        nValor = Record.FieldOrDefault(cabecOrig, "nValorTitulo", 0)
                    ]
                }
            else
                aCodCategBruto,
            obsPagto = fnObsPagamento(item)
        in
            List.Transform(aCodCateg, each
                let
                    cat = _,
                    percBruto = Record.FieldOrDefault(cat, "nPerc", 100),
                    // nPerc vazio ou 0 assume 100% (categoria única).
                    percentual = (if percBruto = null or percBruto = 0 then 100 else percBruto) / 100,
                    cabecNovo = Record.Combine({
                        cabecOrig,
                        [
                            cCodCateg = Record.FieldOrDefault(cat, "cCodCateg", null),
                            nValorTitulo = Record.FieldOrDefault(cat, "nValor", 0),
                            nValorCOFINS = fnNum(Record.FieldOrDefault(cabecOrig, "nValorCOFINS", 0)) * percentual,
                            nValorCSLL = fnNum(Record.FieldOrDefault(cabecOrig, "nValorCSLL", 0)) * percentual,
                            nValorINSS = fnNum(Record.FieldOrDefault(cabecOrig, "nValorINSS", 0)) * percentual,
                            nValorIR = fnNum(Record.FieldOrDefault(cabecOrig, "nValorIR", 0)) * percentual,
                            nValorISS = fnNum(Record.FieldOrDefault(cabecOrig, "nValorISS", 0)) * percentual,
                            nValorPIS = fnNum(Record.FieldOrDefault(cabecOrig, "nValorPIS", 0)) * percentual,
                            cObsLancPagto = obsPagto
                        ]
                    }),
                    resumoNovo = [
                        cLiquidado = Record.FieldOrDefault(resumoOrig, "cLiquidado", null),
                        nValPago = fnNum(Record.FieldOrDefault(resumoOrig, "nValPago", 0)) * percentual,
                        nValAberto = fnNum(Record.FieldOrDefault(resumoOrig, "nValAberto", 0)) * percentual,
                        nDesconto = fnNum(Record.FieldOrDefault(resumoOrig, "nDesconto", 0)) * percentual,
                        nJuros = fnNum(Record.FieldOrDefault(resumoOrig, "nJuros", 0)) * percentual,
                        nMulta = fnNum(Record.FieldOrDefault(resumoOrig, "nMulta", 0)) * percentual,
                        nValLiquido = fnNum(Record.FieldOrDefault(resumoOrig, "nValLiquido", 0)) * percentual
                    ]
                in
                    [ detalhes = cabecNovo, resumo = resumoNovo ]
            ),

    TitulosExpandidos = List.Combine(List.Transform(TodosTitulos, each fnExpandirTitulo(_))),

    // ---------- ListarMovimentos: só PREVISAO_CONTRATO ----------
    // Previsão de faturamento de contrato não existe em PesquisarLancamentos
    // (o título ainda não foi lançado). cNatureza omitido de propósito: sem
    // ele, uma única busca paginada já cobre contas a pagar e a receber.
    fnParamPaginaPrevisoes = (pagina as number) as record =>
        let
            Base = [ nPagina = pagina, nRegPorPagina = 100, lDadosCad = true ],
            ComInicio = if DataVencDe <> "" then Record.AddField(Base, "dDtVencDe", DataVencDe) else Base,
            ComFim = if DataVencAte <> "" then Record.AddField(ComInicio, "dDtVencAte", DataVencAte) else ComInicio
        in
            ComFim,

    fnPaginaPrevisoes = (pagina as number) as record =>
        fnOmieCall("financas/mf", "ListarMovimentos", fnParamPaginaPrevisoes(pagina)),

    PaginasPrevisoes = List.Generate(
        () => [ i = 1, resp = fnPaginaPrevisoes(1) ],
        each [resp] <> null,
        each
            let totalPaginas = Number.RoundUp(Record.FieldOrDefault([resp], "nTotPaginas", 1))
            in
                if [i] + 1 > totalPaginas then [ i = [i] + 1, resp = null ]
                else [ i = [i] + 1, resp = fnPaginaPrevisoes([i] + 1) ],
        each [resp]
    ),
    TodosMovimentos = List.Combine(List.Transform(PaginasPrevisoes, each Record.FieldOrDefault(_, "movimentos", {}))),

    // Retém só os itens de previsão de contrato -- os demais tipos de
    // registro do endpoint (títulos já lançados, baixas bancárias) ficam de
    // fora. "cGrupo" fica dentro de "detalhes", não é campo irmão de "resumo".
    PrevisoesContrato = List.Select(
        TodosMovimentos,
        each Record.FieldOrDefault(Record.FieldOrDefault(_, "detalhes", []), "cGrupo", null) = "PREVISAO_CONTRATO"
    ),

    // ---------- Junta as duas fontes já no mesmo shape {detalhes, resumo} ----------
    TodosItens = List.Combine({TitulosExpandidos, PrevisoesContrato}),

    // ---------- Achatamento ----------
    TabelaBruta = Table.FromList(TodosItens, Splitter.SplitByNothing(), null, null, ExtraValues.Error),
    TabelaExpandidaTopo = Table.ExpandRecordColumn(TabelaBruta, "Column1", {"detalhes", "resumo"}, {"detalhes", "resumo"}),

    // Expande "detalhes" e "resumo" dinamicamente -- os nomes de campo variam
    // entre um título de PesquisarLancamentos e uma previsão de contrato de
    // ListarMovimentos.
    CamposDetalhes = List.Distinct(List.Combine(List.Transform(TabelaExpandidaTopo[detalhes], each Record.FieldNames(_)))),
    TabelaComDetalhes = Table.ExpandRecordColumn(TabelaExpandidaTopo, "detalhes", CamposDetalhes, CamposDetalhes),

    // Campos de "resumo" sempre levam o prefixo "Resumo_" (a API repete algum
    // código/data de "detalhes" dentro de "resumo" para certos tipos de
    // movimento, o que quebraria Table.ExpandRecordColumn sem isso) -- nome
    // estável e previsível, usado pelas colunas derivadas mais abaixo.
    CamposResumo = List.Distinct(List.Combine(List.Transform(TabelaComDetalhes[resumo], each Record.FieldNames(_)))),
    CamposResumoRenomeados = List.Transform(CamposResumo, each "Resumo_" & _),
    TabelaMovimentos = Table.ExpandRecordColumn(TabelaComDetalhes, "resumo", CamposResumo, CamposResumoRenomeados),

    // ---------- Junção com os cadastros de enriquecimento ----------
    // Categoria própria do título -- traz descrição, sufixo de inatividade, e
    // o código do grupo (categoria_superior) pro segundo lookup logo abaixo.
    ComCategoria = Table.NestedJoin(TabelaMovimentos, {"cCodCateg"}, CategoriaMap, {"codigo"}, "CategoriaInfo", JoinKind.LeftOuter),
    ComCategoriaExpandida = Table.ExpandTableColumn(ComCategoria, "CategoriaInfo",
        {"Categoria_Descricao", "categoria_superior", "conta_inativa", "nao_exibir", "transferencia", "totalizadora"},
        {"Categoria_Descricao", "Categoria_Superior", "Categoria_Inativa", "Categoria_NaoExibir", "Categoria_Transferencia", "Categoria_Totalizadora"}
    ),

    // Grupo (categoria_superior) -- segundo lookup encadeado no MESMO
    // CategoriaMap, agora pelo código do grupo, pra pegar a descrição dele.
    ComGrupo = Table.NestedJoin(ComCategoriaExpandida, {"Categoria_Superior"}, CategoriaMap, {"codigo"}, "GrupoInfo", JoinKind.LeftOuter),
    ComGrupoExpandida = Table.ExpandTableColumn(ComGrupo, "GrupoInfo", {"Categoria_Descricao"}, {"Grupo_Descricao"}),

    ComContaCorrente = Table.NestedJoin(ComGrupoExpandida, {"nCodCC"}, ContaCorrenteMap, {"nCodCC"}, "ContaCorrenteInfo", JoinKind.LeftOuter),
    ComContaCorrenteExpandida = Table.ExpandTableColumn(ComContaCorrente, "ContaCorrenteInfo", {"ContaCorrente_Descricao"}, {"ContaCorrente_Descricao"}),

    ComCliente = Table.NestedJoin(ComContaCorrenteExpandida, {"nCodCliente"}, ClienteMap, {"codigo_cliente_omie"}, "ClienteInfo", JoinKind.LeftOuter),
    ComClienteExpandida = Table.ExpandTableColumn(ComCliente, "ClienteInfo",
        {"Cliente_RazaoSocial", "Cliente_NomeFantasia"}, {"Cliente_RazaoSocial", "Cliente_NomeFantasia"}
    ),

    // ---------- Colunas derivadas ----------
    Hoje = DateTime.Date(DateTime.LocalNow()),

    ComTipo = Table.AddColumn(ComClienteExpandida, "Tipo", each
        let natureza = fnCampo(_, "cNatureza") in
            if natureza = "R" then "1. Contas a Receber"
            else if natureza = "P" then "2. Contas a Pagar"
            else natureza
    , type text),

    // Os dois checks abaixo (Grupo/Categoria) tratam "" igual a null de
    // propósito: quando a descrição do cadastro vier como string vazia, cai
    // pro próprio código, não só quando a chave falta.
    ComGrupoFinal = Table.AddColumn(ComTipo, "Grupo", each
        if [Categoria_Superior] = null or [Categoria_Superior] = "" then ""
        else if [Grupo_Descricao] <> null and [Grupo_Descricao] <> "" then [Grupo_Descricao]
        else [Categoria_Superior]
    , type text),

    ComCategoriaFinal = Table.AddColumn(ComGrupoFinal, "Categoria", each
        let
            desc = if [Categoria_Descricao] <> null and [Categoria_Descricao] <> "" then [Categoria_Descricao] else fnCampo(_, "cCodCateg"),
            sufixo = if [Categoria_Inativa] = "S" then " (inativa)" else ""
        in
            Text.From(desc) & sufixo
    , type text),

    ComObservacaoConta = Table.AddColumn(ComCategoriaFinal, "Observação da Conta", each fnObservacao(_), type text),

    ComDataRegistro = Table.AddColumn(ComObservacaoConta, "Data de Registro (completa)", each fnParaData(fnCampo(_, "dDtRegistro")), type date),
    ComDataEmissao = Table.AddColumn(ComDataRegistro, "Data de Emissão (completa)", each fnParaData(fnCampo(_, "dDtEmissao")), type date),
    ComNcNfe = Table.AddColumn(ComDataEmissao, "NC/Nfe", each fnCampo(_, "cNumDocFiscal"), type any),
    ComDataVenc = Table.AddColumn(ComNcNfe, "Data de Vencimento (completa)", each fnParaData(fnCampo(_, "dDtVenc")), type date),

    ComSituacao = Table.AddColumn(ComDataVenc, "Situação do Vencimento", each fnSituacaoVencimento(_, fnCampo(_, "cNatureza"), Hoje), type text),

    ComValorConta = Table.AddColumn(ComSituacao, "Valor da Conta", each fnNum(fnCampo(_, "nValorTitulo")), type number),
    ComPago = Table.AddColumn(ComValorConta, "Pago ou Recebido", each fnNum(fnCampo(_, "Resumo_nValPago")), type number),
    // Uma previsão de contrato só retorna "nValLiquido" no resumo (sem
    // "nValAberto"/"nValPago") -- sem esse fallback ela apareceria com
    // "A Pagar ou Receber" zerado mesmo tendo um valor previsto.
    ComAberto = Table.AddColumn(ComPago, "A Pagar ou Receber", each
        let aberto = fnCampo(_, "Resumo_nValAberto") in
            fnNum(if aberto <> null then aberto else fnCampo(_, "Resumo_nValLiquido"))
    , type number),

    ComContaCorrenteFinal = Table.AddColumn(ComAberto, "Conta Corrente", each
        if [ContaCorrente_Descricao] <> null then [ContaCorrente_Descricao] else fnCampo(_, "nCodCC")
    , type any),

    ComClienteFinal = Table.AddColumn(ComContaCorrenteFinal, "Cliente ou Fornecedor (Nome Fantasia)", each
        let nf = [Cliente_NomeFantasia], rs = [Cliente_RazaoSocial] in
            if nf <> null and nf <> "" then nf
            else if rs <> null and rs <> "" then rs
            else ""
    , type text),

    // Lê "cObsLancPagto" (texto da baixa bancária, preenchido em
    // fnExpandirTitulo a partir de lancamentos[].cObsLanc). Fica em branco
    // pra previsões de contrato (não têm baixa bancária associada) e pra
    // títulos cuja baixa não tem texto registrado na Omie.
    ComObsPagto = Table.AddColumn(ComClienteFinal, "Observação do Pagto ou Recbto", each
        let v = fnCampo(_, "cObsLancPagto") in if v <> null then v else ""
    , type text),
    ComDataPagto = Table.AddColumn(ComObsPagto, "Data de Pagto", each fnParaData(fnCampo(_, "dDtPagamento")), type date),

    ComCofins = Table.AddColumn(ComDataPagto, "COFINS Retido", each fnNumRetido(_, "nValorCOFINS", "cRetCOFINS"), type number),
    ComCsll = Table.AddColumn(ComCofins, "CSLL Retido", each fnNumRetido(_, "nValorCSLL", "cRetCSLL"), type number),
    ComInss = Table.AddColumn(ComCsll, "INSS Retido", each fnNumRetido(_, "nValorINSS", "cRetINSS"), type number),
    ComIr = Table.AddColumn(ComInss, "IR Retido", each fnNumRetido(_, "nValorIR", "cRetIR"), type number),
    ComIss = Table.AddColumn(ComIr, "ISS Retido", each fnNumRetido(_, "nValorISS", "cRetISS"), type number),
    ComPis = Table.AddColumn(ComIss, "PIS Retido", each fnNumRetido(_, "nValorPIS", "cRetPIS"), type number),

    ComDesconto = Table.AddColumn(ComPis, "Desconto", each fnNum(fnCampo(_, "Resumo_nDesconto")), type number),
    ComJuros = Table.AddColumn(ComDesconto, "Juros", each fnNum(fnCampo(_, "Resumo_nJuros")), type number),

    ComDre = Table.AddColumn(ComJuros, "DRE", each
        if fnCampo(_, "cStatus") = "ATRASADO" then "Não"
        else if [Categoria_NaoExibir] = "S" or [Categoria_Transferencia] = "S" or [Categoria_Totalizadora] = "S" then "Não"
        else "Sim"
    , type text),

    ComCodFcx = Table.AddColumn(ComDre, "cod.fcx", each "", type text),
    ComX = Table.AddColumn(ComCodFcx, "x", each "", type text),

    // ---------- Layout final: mesmas colunas/ordem/nomes da aba "Geral" ----------
    ColunasGeral = {
        "x", "Tipo", "Grupo", "Categoria", "Observação da Conta",
        "Data de Registro (completa)", "Data de Emissão (completa)", "NC/Nfe",
        "Data de Vencimento (completa)", "Situação do Vencimento",
        "Valor da Conta", "Pago ou Recebido", "A Pagar ou Receber", "Conta Corrente",
        "Cliente ou Fornecedor (Nome Fantasia)", "Observação do Pagto ou Recbto", "Data de Pagto",
        "COFINS Retido", "CSLL Retido", "INSS Retido", "IR Retido", "ISS Retido", "PIS Retido",
        "Desconto", "Juros", "DRE", "cod.fcx"
    },
    TabelaGeral = Table.SelectColumns(ComX, ColunasGeral),
    TabelaOrdenada = Table.Sort(TabelaGeral, {{"Data de Vencimento (completa)", Order.Ascending}, {"Tipo", Order.Ascending}})
in
    TabelaOrdenada
