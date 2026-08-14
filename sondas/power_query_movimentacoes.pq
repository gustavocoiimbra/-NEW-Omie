// Pacote de consultas Power Query (linguagem M) para trazer movimentações
// financeiras e os cadastros de enriquecimento (categoria/conta corrente/
// cliente) da API da Omie DIRETO no Excel -- sem passar pelo pipeline Python.
//
// ATENÇÃO: app_key/app_secret ficam gravados dentro deste arquivo .xlsx (nos
// parâmetros da consulta). Nunca compartilhe/envie/suba essa planilha depois
// de configurar as credenciais -- é equivalente a compartilhar a senha da
// conta Omie. Se isso for um risco no seu fluxo, prefira main_planilha.py
// (gera uma planilha sem nenhuma credencial embutida).
//
// Estrutura: cada consulta de enriquecimento (CategoriaMap, ContaCorrenteMap,
// ClienteMap) é TOTALMENTE independente -- tem sua própria lógica de chamada/
// paginação embutida, sem depender de uma função compartilhada entre elas.
// Só "Movimentacoes" ainda reaproveita "fnOmieCall" (o envelope genérico de
// chamada, igual pra qualquer endpoint da Omie -- não é lógica "de um
// endpoint" específico) e faz a junção com os 3 mapas de enriquecimento,
// replicando as colunas derivadas de src/report_builder.py::montar_geral
// (Grupo, Categoria com sufixo de inatividade, DRE, Situação do Vencimento,
// retenções) -- ver comentário no início do bloco "Movimentacoes" para o
// detalhe de cada uma e as diferenças conhecidas em relação ao Python.
//
// O que NÃO está implementado aqui (fica só no Python, por pedido explícito):
// - a reconciliação de contratos (src/contratos.py) -- continua só em
//   main_dashboard.py.
//
// ===================== COMO USAR =====================
// Este arquivo tem 5 blocos, cada um delimitado por "===== QUERY: Nome =====".
// Pra cada um: Power Query > Nova Consulta > Consulta em Branco > renomeie a
// consulta (painel direito) pro nome exato indicado > Editor Avançado > cole
// só o conteúdo daquele bloco > Concluído.
//
// Antes de criar as consultas, configure os Parâmetros (Página Inicial >
// Gerenciar Parâmetros > Novo Parâmetro), tipo Texto:
//   OmieAppKey    = sua app_key
//   OmieAppSecret = sua app_secret
//
// Ordem de criação: "fnOmieCall" e os 3 mapas (CategoriaMap, ContaCorrenteMap,
// ClienteMap) não dependem uns dos outros -- crie em qualquer ordem entre
// eles. "Movimentacoes" depende de TODOS os outros 4 (faz a junção com os 3
// mapas) -- crie por último.
// =======================================================


// ===== QUERY: fnOmieCall =====
// Envelope padrão de toda chamada da Omie: POST com o corpo
// {call, app_key, app_secret, param: [ {...} ]} -- ver src/omie_client.py.
// Usada só por "Movimentacoes" -- as 3 consultas de enriquecimento têm essa
// mesma lógica embutida nelas mesmas, de propósito (ver nota no topo do
// arquivo), não chamam esta função.
// ~0.3s de espera por chamada (~3 req/s) pra respeitar o limite de taxa
// documentado da Omie -- não há retry/backoff automático em HTTP 425 aqui
// (diferente do cliente Python); se aparecer erro de "muitas requisições",
// espere ~30 min antes de tentar de novo.
let
    fnOmieCall = (modulo as text, chamada as text, param as record) as record =>
        let
            _Espera = Function.InvokeAfter(() => 0, #duration(0, 0, 0, 0.34)),
            Url = "https://app.omie.com.br/api/v1/" & modulo & "/",
            Corpo = [
                call = chamada,
                app_key = OmieAppKey,
                app_secret = OmieAppSecret,
                param = { param }
            ],
            Resposta = Web.Contents(
                Url,
                [
                    Headers = [#"Content-Type" = "application/json"],
                    Content = Json.FromValue(Corpo)
                ]
            ),
            Json = Json.Document(Resposta)
        in
            Json
in
    fnOmieCall


// ===== QUERY: CategoriaMap =====
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
            Resposta = Web.Contents(Url, [Headers = [#"Content-Type" = "application/json"], Content = Json.FromValue(Corpo)]),
            Json = Json.Document(Resposta)
        in
            Json,

    // geral/categorias usa "pagina"/"registros_por_pagina" e devolve o total
    // de páginas em "total_de_paginas" -- nomenclatura diferente de
    // financas/mf (ListarMovimentos), que usa "nPagina"/"nRegPorPagina"/"nTotPaginas".
    Paginas = List.Generate(
        () => [ i = 1, resp = fnPagina(1) ],
        each [i] <= Number.RoundUp(Record.FieldOrDefault([resp], "total_de_paginas", 1)),
        each [ i = [i] + 1, resp = fnPagina([i] + 1) ],
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


// ===== QUERY: ContaCorrenteMap =====
// geral/contacorrente · ListarContasCorrentes -- {nCodCC -> descricao}.
// Totalmente independente (ver nota em CategoriaMap).
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

    Paginas = List.Generate(
        () => [ i = 1, resp = fnPagina(1) ],
        each [i] <= Number.RoundUp(Record.FieldOrDefault([resp], "total_de_paginas", 1)),
        each [ i = [i] + 1, resp = fnPagina([i] + 1) ],
        each [resp]
    ),
    // Nome do campo de lista é igual ao nome da chamada mesmo
    // ("ListarContasCorrentes") -- confirmado em src/enrichment.py.
    Itens = List.Combine(List.Transform(Paginas, each Record.FieldOrDefault(_, "ListarContasCorrentes", {}))),
    Tabela = Table.FromList(Itens, Splitter.SplitByNothing(), null, null, ExtraValues.Error),
    Expandida = Table.ExpandRecordColumn(Tabela, "Column1", {"nCodCC", "descricao"}, {"nCodCC", "ContaCorrente_Descricao"})
in
    Expandida


// ===== QUERY: ClienteMap =====
// geral/clientes · ListarClientes -- {codigo_cliente_omie -> razao_social,
// nome_fantasia, cnpj_cpf}. Totalmente independente (ver nota em CategoriaMap).
let
    fnPagina = (pagina as number) as record =>
        let
            _Espera = Function.InvokeAfter(() => 0, #duration(0, 0, 0, 0.34)),
            Url = "https://app.omie.com.br/api/v1/geral/clientes/",
            Corpo = [
                call = "ListarClientes",
                app_key = OmieAppKey,
                app_secret = OmieAppSecret,
                param = { [ pagina = pagina, registros_por_pagina = 100 ] }
            ],
            Resposta = Web.Contents(Url, [Headers = [#"Content-Type" = "application/json"], Content = Json.FromValue(Corpo)]),
            Json = Json.Document(Resposta)
        in
            Json,

    Paginas = List.Generate(
        () => [ i = 1, resp = fnPagina(1) ],
        each [i] <= Number.RoundUp(Record.FieldOrDefault([resp], "total_de_paginas", 1)),
        each [ i = [i] + 1, resp = fnPagina([i] + 1) ],
        each [resp]
    ),
    Itens = List.Combine(List.Transform(Paginas, each Record.FieldOrDefault(_, "clientes_cadastro", {}))),
    Tabela = Table.FromList(Itens, Splitter.SplitByNothing(), null, null, ExtraValues.Error),
    Expandida = Table.ExpandRecordColumn(Tabela, "Column1",
        {"codigo_cliente_omie", "razao_social", "nome_fantasia", "cnpj_cpf"},
        {"codigo_cliente_omie", "Cliente_RazaoSocial", "Cliente_NomeFantasia", "Cliente_CnpjCpf"}
    )
in
    Expandida


// ===== QUERY: Movimentacoes =====
// financas/mf · ListarMovimentos, paginado, enriquecido com CategoriaMap/
// ContaCorrenteMap/ClienteMap e com as colunas derivadas de
// src/report_builder.py::montar_geral -- resultado final no mesmo layout,
// nomes e ordem de coluna da aba "Geral" (modelo "bdContas"), uma linha por
// título, ordenada por vencimento.
//
// Diferenças conhecidas em relação ao Python (aceitas, não são bug -- avise
// se algum caso real divergir de forma relevante):
// - "Situação do Vencimento" usa a data local do computador no momento do
//   refresh (DateTime.LocalNow()), não uma data "congelada";
// - no desempate de datas de vencimento não-preenchidas na ordenação, nulos
//   vêm primeiro aqui (padrão do Power Query); no pandas (Python) eles vão
//   para o final;
// - "Observação da Conta" decodifica só as entidades HTML mais comuns
//   (&amp; &lt; &gt; &quot; &#39;), não é um unescape genérico como o
//   html.unescape do Python -- suficiente pro caso real documentado
//   (observações de NFS-e só usam &amp;);
// - "cod.fcx" e "Observação do Pagto ou Recbto" ficam sempre em branco, igual
//   ao Python (ver comentário de _COLUNAS_GERAL em report_builder.py sobre
//   por que essas duas não têm fonte de dados confiável).
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

    // Réplica de report_builder._num_retido: zera o valor quando o flag de
    // retenção (cRetXXX) é explicitamente "N" (o valor calculado pode vir
    // preenchido mesmo com a retenção desativada).
    fnNumRetido = (linha as record, campoValor as text, campoFlag as text) as number =>
        if fnCampo(linha, campoFlag) = "N" then 0 else fnNum(fnCampo(linha, campoValor)),

    // Réplica de report_builder._observacao: "|" vira espaço; decodifica só as
    // entidades HTML mais comuns (ver nota no topo do bloco).
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

    // Réplica de report_builder._situacao_vencimento.
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

    // ---------- Busca paginada ----------
    // Monta o param de UMA página de ListarMovimentos, incluindo dDtVencDe/
    // dDtVencAte só se preenchidos (igual à lógica de src/movimentos.py --
    // enviar só um dos dois faz a Omie devolver zero previsões de contrato).
    fnParamPagina = (pagina as number) as record =>
        let
            Base = [ nPagina = pagina, nRegPorPagina = 100, lDadosCad = true ],
            ComInicio = if DataVencDe <> "" then Record.AddField(Base, "dDtVencDe", DataVencDe) else Base,
            ComFim = if DataVencAte <> "" then Record.AddField(ComInicio, "dDtVencAte", DataVencAte) else ComInicio
        in
            ComFim,

    fnPaginaMovimentos = (pagina as number) as record =>
        fnOmieCall("financas/mf", "ListarMovimentos", fnParamPagina(pagina)),

    // Pagina até nTotPaginas (busca a 1ª página pra descobrir quantas existem,
    // depois segue pedindo as próximas). financas/mf usa "nTotPaginas", NÃO
    // "total_de_paginas" (esse é só dos endpoints geral/*).
    Paginas = List.Generate(
        () => [ i = 1, resp = fnPaginaMovimentos(1) ],
        each [i] <= Number.RoundUp(Record.FieldOrDefault([resp], "nTotPaginas", 1)),
        each [ i = [i] + 1, resp = fnPaginaMovimentos([i] + 1) ],
        each [resp]
    ),

    TodosMovimentos = List.Combine(List.Transform(Paginas, each Record.FieldOrDefault(_, "movimentos", {}))),

    // ---------- Filtro por cGrupo (allow-list) e achatamento ----------
    // Allow-list igual à de src/movimentos.py: só títulos de verdade e
    // previsões de contrato -- descarta as baixas bancárias
    // (CONTA_CORRENTE_PAG/REC), que duplicariam valor se somadas junto.
    // "cGrupo" NÃO é campo irmão de "detalhes"/"resumo" no item bruto -- fica
    // DENTRO de "detalhes" (confirmado em src/movimentos.py::_filtrar_e_adaptar),
    // por isso só expandimos "detalhes"/"resumo" aqui e acessamos cGrupo por
    // dentro do record pra filtrar.
    TabelaBruta = Table.FromList(TodosMovimentos, Splitter.SplitByNothing(), null, null, ExtraValues.Error),
    TabelaExpandidaTopo = Table.ExpandRecordColumn(TabelaBruta, "Column1", {"detalhes", "resumo"}, {"detalhes", "resumo"}),
    TabelaFiltrada = Table.SelectRows(
        TabelaExpandidaTopo,
        each List.Contains({"CONTA_A_PAGAR", "CONTA_A_RECEBER", "PREVISAO_CONTRATO"}, Record.FieldOrDefault([detalhes], "cGrupo", null))
    ),

    // Expande "detalhes" e "resumo" dinamicamente (nomes de campo variam entre
    // título confirmado e previsão de contrato -- ver src/movimentos.py).
    CamposDetalhes = List.Distinct(List.Combine(List.Transform(TabelaFiltrada[detalhes], each Record.FieldNames(_)))),
    TabelaComDetalhes = Table.ExpandRecordColumn(TabelaFiltrada, "detalhes", CamposDetalhes, CamposDetalhes),

    // Campos de "resumo" SEMPRE levam o prefixo "Resumo_" (a API repete algum
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
    // CategoriaMap, agora pelo código do grupo, pra pegar a descrição dele
    // (replica report_builder._grupo_descricao).
    ComGrupo = Table.NestedJoin(ComCategoriaExpandida, {"Categoria_Superior"}, CategoriaMap, {"codigo"}, "GrupoInfo", JoinKind.LeftOuter),
    ComGrupoExpandida = Table.ExpandTableColumn(ComGrupo, "GrupoInfo", {"Categoria_Descricao"}, {"Grupo_Descricao"}),

    ComContaCorrente = Table.NestedJoin(ComGrupoExpandida, {"nCodCC"}, ContaCorrenteMap, {"nCodCC"}, "ContaCorrenteInfo", JoinKind.LeftOuter),
    ComContaCorrenteExpandida = Table.ExpandTableColumn(ComContaCorrente, "ContaCorrenteInfo", {"ContaCorrente_Descricao"}, {"ContaCorrente_Descricao"}),

    ComCliente = Table.NestedJoin(ComContaCorrenteExpandida, {"nCodCliente"}, ClienteMap, {"codigo_cliente_omie"}, "ClienteInfo", JoinKind.LeftOuter),
    ComClienteExpandida = Table.ExpandTableColumn(ComCliente, "ClienteInfo",
        {"Cliente_RazaoSocial", "Cliente_NomeFantasia"}, {"Cliente_RazaoSocial", "Cliente_NomeFantasia"}
    ),

    // ---------- Colunas derivadas (replica src/report_builder.py::montar_geral) ----------
    Hoje = DateTime.Date(DateTime.LocalNow()),

    ComTipo = Table.AddColumn(ComClienteExpandida, "Tipo", each
        let natureza = fnCampo(_, "cNatureza") in
            if natureza = "R" then "1. Contas a Receber"
            else if natureza = "P" then "2. Contas a Pagar"
            else natureza
    , type text),

    ComGrupoFinal = Table.AddColumn(ComTipo, "Grupo", each
        if [Categoria_Superior] = null or [Categoria_Superior] = "" then ""
        else if [Grupo_Descricao] <> null then [Grupo_Descricao]
        else [Categoria_Superior]
    , type text),

    ComCategoriaFinal = Table.AddColumn(ComGrupoFinal, "Categoria", each
        let
            desc = if [Categoria_Descricao] <> null then [Categoria_Descricao] else fnCampo(_, "cCodCateg"),
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
    ComAberto = Table.AddColumn(ComPago, "A Pagar ou Receber", each fnNum(fnCampo(_, "Resumo_nValAberto")), type number),

    ComContaCorrenteFinal = Table.AddColumn(ComAberto, "Conta Corrente", each
        if [ContaCorrente_Descricao] <> null then [ContaCorrente_Descricao] else fnCampo(_, "nCodCC")
    , type any),

    ComClienteFinal = Table.AddColumn(ComContaCorrenteFinal, "Cliente ou Fornecedor (Nome Fantasia)", each
        let nf = [Cliente_NomeFantasia], rs = [Cliente_RazaoSocial] in
            if nf <> null and nf <> "" then nf
            else if rs <> null and rs <> "" then rs
            else ""
    , type text),

    ComObsPagto = Table.AddColumn(ComClienteFinal, "Observação do Pagto ou Recbto", each "", type text),
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
