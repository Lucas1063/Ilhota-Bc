#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dispara as 6 combinacoes do experimento completo:
  3 niveis de trafego (baixo/medio/alto) x 2 condicoes (sensores desligados/ligados)

Cada rodada chama controlador.py como subprocesso, com os parametros --cenario e --painel.
Ao final, gera: tripinfos_{cenario}_{base|painel}.xml  e  conflitos_{cenario}_{base|painel}.xml
                 para cada uma das 6 combinacoes.

Uso:
    python rodar_experimento_completo.py
"""
import subprocess
import sys
import time

CENARIOS = ["baixo_fluxo", "medio_fluxo", "alto_fluxo"]
CONDICOES = [("0", "SENSORES DESATIVADOS (base)"), ("1", "SENSORES ATIVADOS (painel)")]

combos = [(c, painel, label) for c in CENARIOS for painel, label in CONDICOES]

print(f"Experimento completo: {len(combos)} rodadas\n")

t0 = time.time()
for i, (cenario, painel, label) in enumerate(combos, 1):
    print(f"{'='*70}")
    print(f"[{i}/{len(combos)}] cenario={cenario} | {label}")
    print(f"{'='*70}")
    inicio = time.time()

    resultado = subprocess.run(
        [sys.executable, "controlador.py", "--cenario", cenario, "--painel", painel, "--diag", "0"]
    )

    dur = time.time() - inicio
    if resultado.returncode != 0:
        print(f"\n[ERRO] Rodada {cenario}/{label} terminou com codigo {resultado.returncode}. Abortando.")
        sys.exit(1)
    print(f"--- rodada concluida em {dur:.0f}s ---\n")

total = time.time() - t0
print(f"\nExperimento completo finalizado em {total/60:.1f} min.")
print("Rode agora: python analisar_experimento.py")