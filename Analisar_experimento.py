#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Analisa os 6 resultados do experimento completo (3 trafegos x 2 condicoes)
gerados por rodar_experimento_completo.py.

Calcula metricas de eficiencia (tempo medio de viagem, atraso) a partir dos
tripinfos, e metricas de seguranca a partir dos conflitos SSM. O device SSM
grava conflitos da REDE INTEIRA (qualquer veiculo, qualquer lugar), entao
este script LOCALIZA cada conflito (via sumolib, usando a coordenada x,y)
e separa em:
  - total geral da rede (como antes)
  - "local": apenas os conflitos dentro dos edges monitorados pelo sistema
             (onde os sensores/zonas de atuacao realmente podem influenciar)

A metrica "local" e a que importa para avaliar o efeito do sistema — a
"total" e mantida so como referencia/contexto.

Uso:
    python analisar_experimento.py
"""
import xml.etree.ElementTree as ET
import statistics as st
import os
import sys
import gzip
import shutil
import tempfile

CENARIOS = ["baixo_fluxo", "medio_fluxo", "alto_fluxo"]
CONDICOES = [("base", "Sem sensores"), ("painel", "Com sensores")]

NET_FILE = "osm.net.xml.gz"
RAIO_BUSCA = 15.0  # metros: raio de busca do edge mais proximo do conflito

# edges monitorados por cada ponto (sensor + zona de atuacao + painel).
# um conflito e "local" se acontecer em qualquer um destes edges.
EDGES_MONITORADOS = {
    "813996133#0", "813996133#1",        # posto_vai (zona + sensor)
    "152471518#0", "152471518#1",        # posto_vem (zona + sensor)
    "152471509#1", "978745925",          # tunel (zona + sensor)
    "978745921", "1414165818#3",         # descida (zona + sensor)
}

# ---------------------------------------------------------------------
# tripinfo
# ---------------------------------------------------------------------
def analisar_tripinfo(path):
    if not os.path.exists(path):
        print(f"[aviso] {path} nao encontrado.")
        return None
    root = ET.parse(path).getroot()
    dur, tloss = [], []
    for t in root.iter('tripinfo'):
        dur.append(float(t.get('duration', 0)))
        tloss.append(float(t.get('timeLoss', 0)))
    if not dur:
        return None
    return {
        "n_veiculos": len(dur),
        "tempo_medio_s": st.mean(dur),
        "tempo_mediana_s": st.median(dur),
        "atraso_medio_s": st.mean(tloss),
        "atraso_mediana_s": st.median(tloss),
    }

# ---------------------------------------------------------------------
# rede (sumolib) — usada so para localizar os conflitos
# ---------------------------------------------------------------------
def carregar_rede():
    if "SUMO_HOME" not in os.environ:
        print("[aviso] SUMO_HOME nao definido — nao sera possivel localizar conflitos por edge. "
              "Somente a contagem total (rede inteira) sera usada.")
        return None
    sys.path.append(os.path.join(os.environ["SUMO_HOME"], "tools"))
    try:
        import sumolib
    except Exception as e:
        print(f"[aviso] nao foi possivel importar sumolib ({e}). Usando so a contagem total.")
        return None
    if not os.path.exists(NET_FILE):
        print(f"[aviso] {NET_FILE} nao encontrado. Usando so a contagem total.")
        return None

    caminho = NET_FILE
    if caminho.endswith(".gz"):
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".net.xml")
        with gzip.open(caminho, "rb") as fin, open(tmp.name, "wb") as fout:
            shutil.copyfileobj(fin, fout)
        caminho = tmp.name

    return sumolib.net.readNet(caminho)

def localizar_edge(net, x, y):
    """Retorna o ID do edge mais proximo do ponto (x,y), ou None."""
    try:
        vizinhos = net.getNeighboringEdges(x, y, r=RAIO_BUSCA)
    except Exception:
        return None
    if not vizinhos:
        return None
    edge_mais_perto = min(vizinhos, key=lambda par: par[1])[0]
    return edge_mais_perto.getID()

def extrair_posicao(conflito_el):
    """Procura um atributo 'position' (formato 'x,y') em qualquer sub-elemento do conflito."""
    for sub in conflito_el.iter():
        pos = sub.attrib.get("position")
        if pos and "," in pos:
            try:
                x_str, y_str = pos.split(",")[:2]
                return float(x_str), float(y_str)
            except ValueError:
                continue
    return None

def contar_conflitos(path, net):
    """Retorna (total_rede, total_local) para o arquivo de conflitos SSM."""
    if not os.path.exists(path):
        return None, None
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return None, None

    conflitos = list(root)
    total = len(conflitos)
    if net is None:
        return total, None

    local = 0
    for c in conflitos:
        pos = extrair_posicao(c)
        if pos is None:
            continue
        edge_id = localizar_edge(net, pos[0], pos[1])
        if edge_id in EDGES_MONITORADOS:
            local += 1
    return total, local

# ---------------------------------------------------------------------
# execucao
# ---------------------------------------------------------------------
print("Carregando rede para localizar conflitos (pode levar alguns segundos)...")
net = carregar_rede()
if net:
    print(f"Rede carregada. Filtrando conflitos nos {len(EDGES_MONITORADOS)} edges monitorados.\n")

linhas = []
for cenario in CENARIOS:
    for sufixo, label in CONDICOES:
        trip = analisar_tripinfo(f"tripinfos_{cenario}_{sufixo}.xml")
        total, local = contar_conflitos(f"conflitos_{cenario}_{sufixo}.xml", net)
        if trip is None:
            continue
        trip.update({"cenario": cenario, "condicao": label, "sufixo": sufixo,
                      "n_conflitos_total": total, "n_conflitos_local": local})
        linhas.append(trip)

if not linhas:
    print("Nenhum resultado encontrado. Rode primeiro: python rodar_experimento_completo.py")
    raise SystemExit(1)

# ---------------------------------------------------------------------
# tabela no terminal
# ---------------------------------------------------------------------
def fmt(v):
    return "-" if v is None else str(v)

print(f"\n{'Cenario':<13}{'Condicao':<16}{'Veic':>6}{'T.medio(s)':>12}{'Atraso(s)':>11}"
      f"{'Confl.total':>13}{'Confl.local':>13}")
print('-'*90)
por_cenario = {}
for l in linhas:
    por_cenario.setdefault(l['cenario'], {})[l['sufixo']] = l
    print(f"{l['cenario']:<13}{l['condicao']:<16}{l['n_veiculos']:>6}"
          f"{l['tempo_medio_s']:>12.1f}{l['atraso_medio_s']:>11.1f}"
          f"{fmt(l['n_conflitos_total']):>13}{fmt(l['n_conflitos_local']):>13}")

# ---------------------------------------------------------------------
# comparacao base x painel, por nivel de trafego
# ---------------------------------------------------------------------
print(f"\n{'='*90}\nEFEITO DO SISTEMA (base -> painel), por nivel de trafego\n{'='*90}")
for cenario in CENARIOS:
    par = por_cenario.get(cenario, {})
    if 'base' not in par or 'painel' not in par:
        continue
    b, p = par['base'], par['painel']
    delta_atraso = p['atraso_medio_s'] - b['atraso_medio_s']
    pct = (delta_atraso / b['atraso_medio_s'] * 100) if b['atraso_medio_s'] else float('nan')
    print(f"{cenario:<13} atraso: {b['atraso_medio_s']:.1f}s -> {p['atraso_medio_s']:.1f}s "
          f"({pct:+.1f}%)   |  veiculos concluidos: {b['n_veiculos']} -> {p['n_veiculos']}")

    bl, pl = b['n_conflitos_local'], p['n_conflitos_local']
    if bl is not None and pl is not None:
        sinal = "menos" if pl < bl else ("mais" if pl > bl else "igual numero de")
        pct_str = f"({(pl-bl)/bl*100:+.1f}%)" if bl > 0 else "(base=0)"
        print(f"{'':<13} conflitos NOS PONTOS MONITORADOS: {bl} -> {pl} {pct_str}  [{sinal} conflitos com sensores]")
    bt, pt = b['n_conflitos_total'], p['n_conflitos_total']
    if bt is not None and pt is not None:
        print(f"{'':<13} conflitos na rede toda (referencia): {bt} -> {pt}")

# ---------------------------------------------------------------------
# salva CSV
# ---------------------------------------------------------------------
with open("resultados_experimento.csv", "w", encoding="utf-8") as f:
    f.write("cenario,condicao,n_veiculos,tempo_medio_s,tempo_mediana_s,atraso_medio_s,atraso_mediana_s,"
            "n_conflitos_total,n_conflitos_local\n")
    for l in linhas:
        f.write(f"{l['cenario']},{l['condicao']},{l['n_veiculos']},{l['tempo_medio_s']:.2f},"
                f"{l['tempo_mediana_s']:.2f},{l['atraso_medio_s']:.2f},{l['atraso_mediana_s']:.2f},"
                f"{fmt(l['n_conflitos_total'])},{fmt(l['n_conflitos_local'])}\n")
print(f"\nTabela salva em resultados_experimento.csv")

# ---------------------------------------------------------------------
# graficos
# ---------------------------------------------------------------------
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    x = np.arange(len(CENARIOS))
    largura = 0.35
    nomes_x = ['Baixo fluxo', 'Médio fluxo', 'Alto fluxo']

    # --- grafico 1: atraso medio (eficiencia) ---
    atraso_base   = [por_cenario.get(c, {}).get('base',   {}).get('atraso_medio_s', 0) for c in CENARIOS]
    atraso_painel = [por_cenario.get(c, {}).get('painel', {}).get('atraso_medio_s', 0) for c in CENARIOS]

    fig1, ax1 = plt.subplots(figsize=(7, 4.5))
    b1 = ax1.bar(x - largura/2, atraso_base,   largura, label='Sem sensores', color='#c0392b')
    b2 = ax1.bar(x + largura/2, atraso_painel, largura, label='Com sensores', color='#27ae60')
    ax1.set_ylabel('Atraso médio por viagem (s)')
    ax1.set_title('Efeito do sistema no atraso médio, por nível de tráfego')
    ax1.set_xticks(x); ax1.set_xticklabels(nomes_x)
    ax1.legend()
    ax1.bar_label(b1, fmt='%.0f'); ax1.bar_label(b2, fmt='%.0f')
    fig1.tight_layout()
    fig1.savefig("grafico_atraso.png", dpi=150)
    print("Grafico salvo em grafico_atraso.png")

    # --- grafico 2: conflitos LOCAIS (pontos monitorados) — a metrica que importa ---
    confl_base   = [por_cenario.get(c, {}).get('base',   {}).get('n_conflitos_local') or 0 for c in CENARIOS]
    confl_painel = [por_cenario.get(c, {}).get('painel', {}).get('n_conflitos_local') or 0 for c in CENARIOS]

    fig2, ax2 = plt.subplots(figsize=(7, 4.5))
    c1 = ax2.bar(x - largura/2, confl_base,   largura, label='Sem sensores', color='#c0392b')
    c2 = ax2.bar(x + largura/2, confl_painel, largura, label='Com sensores', color='#27ae60')
    ax2.set_ylabel('Nº de conflitos nos pontos monitorados')
    ax2.set_title('Efeito do sistema no número de conflitos, por nível de tráfego\n(apenas nos edges com sensor/atuação)')
    ax2.set_xticks(x); ax2.set_xticklabels(nomes_x)
    ax2.legend()
    ax2.bar_label(c1, fmt='%.0f'); ax2.bar_label(c2, fmt='%.0f')
    fig2.tight_layout()
    fig2.savefig("grafico_conflitos.png", dpi=150)
    print("Grafico salvo em grafico_conflitos.png")

except ImportError:
    print("[aviso] matplotlib nao instalado — graficos nao gerados. "
          "Instale com: python -m pip install matplotlib numpy")