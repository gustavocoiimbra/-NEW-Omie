// geral/categorias · ListarCategorias -- {codigo -> descricao, categoria_superior,
// codigo_dre, conta_inativa, nao_exibir, transferencia, totalizadora}.
// Totalmente independente: chamada, paginação e parsing embutidos aqui, sem
// depender de nenhuma outra consulta além dos Parâmetros de credencial.
let
    fnPagina = (pagina as number) as record =>
        let
            _Espera = Function.InvokeAfter(() => 0, #duration(0, 0, 0, 0.34)),
            Url = "https://app.omie.com.br/api/v1/geral/categorias/",
            Corpo = [
                call = "ListarCategorias",
                app_key = OmieAppKey,
                app_secret = OmieAppSecret,
                param = { [ pagina = pagina, registros_por_pagina = 100 ] }
            ],
            // "if _Espera = 0" força este passo a depender de "_Espera" -- sem
            // isso o M não teria motivo pra avaliar a espera (ver
            // fnOmieCall.pq para o detalhe). "_Espera" sempre é 0.
            Resposta = if _Espera = 0 then Web.Contents(Url, [Headers = [#"Content-Type" = "application/json"], Content = Json.FromValue(Corpo)]) else error "unreachable",
            Json = Json.Document(Resposta)
        in
            Json,

    // geral/categorias usa "pagina"/"registros_por_pagina" e devolve o total
    // de páginas em "total_de_paginas" -- nomenclatura diferente de
    // financas/mf (ListarMovimentos), que usa "nPagina"/"nRegPorPagina"/"nTotPaginas".
    // "next" só busca a próxima página quando a página atual (já buscada) diz
    // que ela existe -- sem isso, List.Generate sempre buscaria mais uma
    // página além da última pra poder avaliar "condition" e descartaria essa
    // chamada, desperdiçando 1 requisição por paginação completa.
    Paginas = List.Generate(
        () => [ i = 1, resp = fnPagina(1) ],
        each [resp] <> null,
        each
            let totalPaginas = Number.RoundUp(Record.FieldOrDefault([resp], "total_de_paginas", 1))
            in
                if [i] + 1 > totalPaginas then [ i = [i] + 1, resp = null ]
                else [ i = [i] + 1, resp = fnPagina([i] + 1) ],
        each [resp]
    ),
    Itens = List.Combine(List.Transform(Paginas, each Record.FieldOrDefault(_, "categoria_cadastro", {}))),
    Tabela = Table.FromList(Itens, Splitter.SplitByNothing(), null, null, ExtraValues.Error),
    Expandida = Table.ExpandRecordColumn(Tabela, "Column1",
        {"codigo", "descricao", "categoria_superior", "codigo_dre", "conta_inativa", "nao_exibir", "transferencia", "totalizadora"},
        {"codigo", "Categoria_Descricao", "categoria_superior", "codigo_dre", "conta_inativa", "nao_exibir", "transferencia", "totalizadora"}
    )
in
    Expandida
