// geral/contacorrente · ListarContasCorrentes -- {nCodCC -> descricao}.
// Totalmente independente (ver nota em CategoriaMap.pq).
let
    fnPagina = (pagina as number) as record =>
        let
            _Espera = Function.InvokeAfter(() => 0, #duration(0, 0, 0, 0.34)),
            Url = "https://app.omie.com.br/api/v1/geral/contacorrente/",
            Corpo = [
                call = "ListarContasCorrentes",
                app_key = OmieAppKey,
                app_secret = OmieAppSecret,
                param = { [ pagina = pagina, registros_por_pagina = 100 ] }
            ],
            Resposta = Web.Contents(Url, [Headers = [#"Content-Type" = "application/json"], Content = Json.FromValue(Corpo)]),
            Json = Json.Document(Resposta)
        in
            Json,

    // Mesmo padrão de "next" do CategoriaMap -- evita buscar a página seguinte
    // quando a atual já indica que era a última.
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
    // Nome do campo de lista é igual ao nome da chamada mesmo ("ListarContasCorrentes").
    Itens = List.Combine(List.Transform(Paginas, each Record.FieldOrDefault(_, "ListarContasCorrentes", {}))),
    Tabela = Table.FromList(Itens, Splitter.SplitByNothing(), null, null, ExtraValues.Error),
    Expandida = Table.ExpandRecordColumn(Tabela, "Column1", {"nCodCC", "descricao"}, {"nCodCC", "ContaCorrente_Descricao"})
in
    Expandida
