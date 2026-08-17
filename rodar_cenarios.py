#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Roda os 3 cenarios de fluxo (baixo/medio/alto) no SUMO e extrai metricas
de tempo de trajeto (tripinfo) para comparacao no capitulo de resultados.

PRE-REQUISITO: os arquivos cenario_baixo_fluxo.rou.xml, cenario_medio_fluxo.rou.xml
e cenario_alto_fluxo.rou.xml devem estar na mesma pasta do osm.sumocfg.

Uso:
    python rodar_cenarios.py           # roda os 3 e gera o relatorio
    python rodar_cenarios.py --so-analise   # so reprocessa tripinfos ja existentes
"""
import os
import sys
import subprocess
import xml.etree.ElementTree as ET
import statistics as st

CENARIOS = {
    "baixo_fluxo": "cenario_baixo_fluxo.rou.xml",
    "medio_fluxo": "cenario_medio_fluxo.rou.xml",
    "alto_fluxo":  "cenario_alto_fluxo.rou.xml",
}

CONFIG_BASE = "osm.sumocfg"   # config original, so trocamos o route-files na chamada
SUMO_BIN = "sumo"             # sem GUI = mais rapido para rodar os 3 cenarios

def rodar_cenario(nome, rou_file):
    tripinfo_out = f"tripinfos_{nome}.xml"
    ssm_out = f"conflitos_{nome}.xml"
    print(f"\n{'='*60}\nRodando cenario: {nome}  ({rou_file})\n{'='*60}")

    cmd = [
        SUMO_BIN, "-c", CONFIG_BASE,
        "--route-files", rou_file,          # sobrescreve as rotas do cenario base
        "--tripinfo-output", tripinfo_out,
        "--device.ssm.file", ssm_out,
        "--seed", "42",
        "--no-step-log", "true",
        "--duration-log.statistics", "true",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("ERRO ao rodar", nome)
        print(result.stderr[-2000:])
        return None
    print(f"Concluido. Tripinfo salvo em: {tripinfo_out}")
    return tripinfo_out

def analisar_tripinfo(path):
    if not os.path.exists(path):
        print(f"[aviso] {path} nao encontrado, pulando.")
        return None
    root = ET.parse(path).getroot()
    duracoes, timeloss, waiting = [], [], []
    for t in root.iter('tripinfo'):
        duracoes.append(float(t.get('duration', 0)))
        timeloss.append(float(t.get('timeLoss', 0)))
        waiting.append(float(t.get('waitingTime', 0)))

    if not duracoes:
        return None

    return {
        "n_veiculos": len(duracoes),
        "tempo_medio_s": st.mean(duracoes),
        "tempo_mediana_s": st.median(duracoes),
        "atraso_medio_s": st.mean(timeloss),
        "atraso_max_s": max(timeloss),
        "espera_media_s": st.mean(waiting),
    }

def main():
    so_analise = "--so-analise" in sys.argv

    resultados = {}
    for nome, rou_file in CENARIOS.items():
        tripinfo_path = f"tripinfos_{nome}.xml"
        if not so_analise:
            if not os.path.exists(rou_file):
                print(f"[erro] {rou_file} nao encontrado nesta pasta. Pulando {nome}.")
                continue
            rodar_cenario(nome, rou_file)
        resultados[nome] = analisar_tripinfo(tripinfo_path)

    # ---- relatorio comparativo ----
    print(f"\n{'='*72}")
    print(f"{'Cenario':<14}{'Veiculos':>10}{'T.medio(s)':>13}{'Mediana(s)':>13}{'Atraso(s)':>12}{'Espera(s)':>12}")
    print('-'*72)
    for nome, r in resultados.items():
        if r is None:
            print(f"{nome:<14}  (sem dados)")
            continue
        print(f"{nome:<14}{r['n_veiculos']:>10}{r['tempo_medio_s']:>13.1f}"
              f"{r['tempo_mediana_s']:>13.1f}{r['atraso_medio_s']:>12.1f}{r['espera_media_s']:>12.1f}")
    print('='*72)

    # salva tambem em CSV para o Excel/gráfico do TCC
    with open("resultados_cenarios.csv", "w", encoding="utf-8") as f:
        f.write("cenario,n_veiculos,tempo_medio_s,tempo_mediana_s,atraso_medio_s,atraso_max_s,espera_media_s\n")
        for nome, r in resultados.items():
            if r is None:
                continue
            f.write(f"{nome},{r['n_veiculos']},{r['tempo_medio_s']:.2f},{r['tempo_mediana_s']:.2f},"
                    f"{r['atraso_medio_s']:.2f},{r['atraso_max_s']:.2f},{r['espera_media_s']:.2f}\n")
    print("\nResultados salvos em resultados_cenarios.csv")

if __name__ == "__main__":
    main()
