// Extrato diário das contas configuradas em "SaldoCaixaBase": uma linha por
// lançamento (Conciliado ou Previsto) e, ao final de cada data, uma linha
// "Saldo do Dia" com o saldo de fechamento (real e previsto) -- ver
// comentário em "SaldoCaixaBase" para o detalhe da lógica e das chamadas à
// API. Cobre os últimos "DiasHistorico" dias (configurável lá). Depende de
// "SaldoCaixaBase" -- crie essa antes.
let
    Resultado = SaldoCaixaBase[ExtratoDiario]
in
    Resultado
