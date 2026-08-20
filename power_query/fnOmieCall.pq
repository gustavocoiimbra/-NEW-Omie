// Envelope padrão de toda chamada da Omie: POST com o corpo
// {call, app_key, app_secret, param: [ {...} ]}.
// Usada só por "Movimentacoes" -- as 3 consultas de enriquecimento têm essa
// mesma lógica embutida nelas mesmas, de propósito (ver README.md), não
// chamam esta função.
// ~0.3s de espera por chamada (~3 req/s) pra respeitar o limite de taxa
// documentado da Omie -- não há retry/backoff automático em HTTP 425 aqui;
// se aparecer erro de "muitas requisições", espere ~30 min antes de tentar
// de novo.
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
            // "if _Espera = 0" força este passo a depender de "_Espera" -- sem
            // isso o M não teria motivo pra avaliar a espera (variável não
            // referenciada por nada no "in" final é só descartada, o
            // Function.InvokeAfter nunca dispara). "_Espera" sempre é 0.
            Resposta = if _Espera = 0 then Web.Contents(
                Url,
                [
                    Headers = [#"Content-Type" = "application/json"],
                    Content = Json.FromValue(Corpo)
                ]
            ) else error "unreachable",
            Json = Json.Document(Resposta)
        in
            Json
in
    fnOmieCall
