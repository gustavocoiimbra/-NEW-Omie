"""Ponto de entrada (fonte ListarMovimentos): python main_movimentos.py --data-inicio 01/07/2026 --data-fim 31/07/2026

Gera o mesmo relatório de main.py, mas buscando os dados via ListarMovimentos
(financas/mf) em vez de PesquisarLancamentos — veja src/movimentos.py.
"""
from src.cli_movimentos import main

if __name__ == "__main__":
    raise SystemExit(main())
