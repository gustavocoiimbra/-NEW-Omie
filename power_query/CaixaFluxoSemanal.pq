// Fluxo de caixa projetado por semana (entradas/saídas/saldo acumulado),
// cobrindo toda a janela de DiasPrevisao mesmo em semanas sem lançamento --
// ver "SaldoCaixaBase". Depende de "SaldoCaixaBase" -- crie essa antes.
let
    Resultado = SaldoCaixaBase[FluxoSemanal]
in
    Resultado
