#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Controlador de sinalizacao inteligente (TraCI + SUMO)
Trecho BR-101 Ilhota-Balneario Camboriu.

Agora com 4 PONTOS DE MONITORAMENTO INDEPENDENTES, cada um com seu proprio
sensor, painel (POI + topico MQTT proprio) e estado de congestionamento:
  1) Posto policial - sentido ida  (quem vai)
  2) Posto policial - sentido volta (quem vem)
  3) Entrada do tunel (aproximacao, do lado de fora)
  4) Descida do morro

Cada ponto liga/desliga seu proprio painel de forma independente dos demais.

Rode duas vezes para o experimento:
  1) PAINEL_ATIVO = False  -> cenario base (sem intervencao)
  2) PAINEL_ATIVO = True   -> cenario com os paineis atuando
"""

import os
import sys
import random
from collections import deque

# ----------------------------------------------------------------------
# CONFIGURACAO GERAL
# ----------------------------------------------------------------------
PAINEL_ATIVO = True
SEMENTE      = 42

SUMO_BINARY  = "sumo-gui"        # "sumo-gui" p/ ver rodando, "sumo" p/ o experimento
CONFIG       = "osm.sumocfg"

CENARIO      = "alto_fluxo"  # "baixo_fluxo" | "medio_fluxo" | "alto_fluxo"
ROUTE_FILE   = f"cenario_{CENARIO}.rou.xml"

TEMPO_FIM    = 3600.0

# --- reacao dos motoristas ao "ler" o painel (comum aos 4 pontos) ---
OBEDIENCIA = 0.75
V_REACAO   = 13.9
DURACAO    = 8.0
JANELA     = 30    # passos p/ suavizar a leitura (30*0.1s = 3s)

# ----------------------------------------------------------------------
# OS 4 PONTOS DE MONITORAMENTO
# ----------------------------------------------------------------------
PONTOS = [
    {
        "nome":       "posto_vai",
        "sensores":   ["sensor_posto_vai_0", "sensor_posto_vai_1"],
        "painel":     "painel_posto_vai",
        "topico":     "rodovia/painel/posto_vai",
        "zona_edges": ["813996133#0"],
        "v_entra": 11.0, "v_sai": 15.0,   # m/s (~40 / ~54 km/h)
    },
    {
        "nome":       "posto_vem",
        "sensores":   ["sensor_posto_vem_0", "sensor_posto_vem_1"],
        "painel":     "painel_posto_vem",
        "topico":     "rodovia/painel/posto_vem",
        "zona_edges": ["152471518#0"],
        "v_entra": 11.0, "v_sai": 15.0,
    },
    {
        "nome":       "tunel",
        "sensores":   ["sensor_tunel_0", "sensor_tunel_1"],
        "painel":     "painel_tunel",
        "topico":     "rodovia/painel/tunel",
        "zona_edges": ["152471509#1"],
        "v_entra": 9.7, "v_sai": 13.0,    # via ja e 80km/h -> limiares um pouco mais baixos
    },
    {
        "nome":       "descida",
        "sensores":   ["sensor_descida_0", "sensor_descida_1", "sensor_descida_2"],
        "painel":     "painel_descida",
        "topico":     "rodovia/painel/descida",
        "zona_edges": ["978745921"],
        "v_entra": 8.3, "v_sai": 11.1,    # via ja e 60km/h -> limiares mais baixos ainda
    },
]

# inicializa o estado (historico, led, decisoes de obediencia) de cada ponto
for p in PONTOS:
    p["hist"] = deque(maxlen=JANELA)
    p["led_ligado"] = False
    p["decisao"] = {}   # vid -> obedece?

# ----------------------------------------------------------------------
# TraCI / SUMO
# ----------------------------------------------------------------------
if "SUMO_HOME" not in os.environ:
    sys.exit("Defina a variavel de ambiente SUMO_HOME apontando p/ sua instalacao do SUMO.")
sys.path.append(os.path.join(os.environ["SUMO_HOME"], "tools"))
import traci  # noqa: E402

sufixo    = "painel" if PAINEL_ATIVO else "base"
ssm_file  = f"conflitos_{CENARIO}_{sufixo}.xml"
trip_file = f"tripinfos_{CENARIO}_{sufixo}.xml"

sumo_cmd = [
    SUMO_BINARY, "-c", CONFIG,
    "--route-files", ROUTE_FILE,
    "--seed", str(SEMENTE),
    "--device.ssm.file", ssm_file,
    "--tripinfo-output", trip_file,
    "--start", "--quit-on-end",
]

# ----------------------------------------------------------------------
# MQTT (opcional)
# ----------------------------------------------------------------------
mqtt_client = None
try:
    import paho.mqtt.client as mqtt
    mqtt_client = mqtt.Client()
    mqtt_client.connect("broker.hivemq.com", 1883, 60)
    mqtt_client.loop_start()
    print("MQTT conectado.")
except Exception as e:
    print(f"MQTT indisponivel ({e}). Seguindo so com os POIs na GUI.")

def publica(topico, estado):
    if mqtt_client:
        try:
            mqtt_client.publish(topico, estado)
        except Exception:
            pass

# ----------------------------------------------------------------------
# FUNCOES
# ----------------------------------------------------------------------
def leitura_sensor(lista_sensores):
    soma_v, soma_n = 0.0, 0
    for d in lista_sensores:
        n = traci.lanearea.getLastStepVehicleNumber(d)
        v = traci.lanearea.getLastStepMeanSpeed(d)
        if n > 0 and v >= 0:
            soma_v += v * n
            soma_n += n
    if soma_n == 0:
        return None, 0
    return soma_v / soma_n, soma_n

# ----------------------------------------------------------------------
# LOOP PRINCIPAL
# ----------------------------------------------------------------------
random.seed(SEMENTE)
traci.start(sumo_cmd)

# ----------------------------------------------------------------------
# ZONA DE VELOCIDADE REGULAMENTADA (FIXA) — postos policiais
# Diferente da logica reativa do painel: isto e um limite permanente da
# via (como uma placa de velocidade maxima), nao depende de congestionamento.
# Por isso e aplicado sempre, nas DUAS rodadas do experimento (base e painel),
# para nao contaminar a comparacao — a reducao do posto e igual nos dois casos,
# so o efeito do painel varia.
# ----------------------------------------------------------------------
ZONAS_VEL_FIXA = {
    "813996133#1": 60,   # posto policial - sentido ida (quem vai)
    "152471518#1": 60,   # posto policial - sentido volta (quem vem)
}
for edge_id, vel_kmh in ZONAS_VEL_FIXA.items():
    n_lanes = traci.edge.getLaneNumber(edge_id)
    for i in range(n_lanes):
        traci.lane.setMaxSpeed(f"{edge_id}_{i}", vel_kmh / 3.6)
    print(f"Zona de velocidade fixa aplicada: {edge_id} -> {vel_kmh} km/h ({n_lanes} faixas)")

print(f"Iniciando cenario: {CENARIO} | {'COM paineis' if PAINEL_ATIVO else 'BASE (sem paineis)'}")
print(f"Monitorando {len(PONTOS)} pontos: {', '.join(p['nome'] for p in PONTOS)}")

while traci.simulation.getMinExpectedNumber() > 0 and traci.simulation.getTime() < TEMPO_FIM:
    traci.simulationStep()
    t = traci.simulation.getTime()

    for p in PONTOS:
        # --- CAMADA 1: leitura ---
        v_media, n = leitura_sensor(p["sensores"])
        if v_media is not None:
            p["hist"].append(v_media)
        v_suave = sum(p["hist"]) / len(p["hist"]) if p["hist"] else 99.0

        # diagnostico a cada 10s, por ponto
        if abs(t % 10.0) < 0.05:
            vk = v_suave * 3.6 if v_suave < 90 else float('nan')
            print(f"[{t:7.1f}s] {p['nome']:<11} sensor: {n:3d} veic | v = {vk:5.1f} km/h "
                  f"| painel: {'VERMELHO' if p['led_ligado'] else 'verde'}")

        # --- CAMADA 2: logica (histerese, independente por ponto) ---
        if not p["led_ligado"] and v_suave < p["v_entra"] and n > 0:
            p["led_ligado"] = True
            traci.poi.setColor(p["painel"], (255, 0, 0, 255))
            publica(p["topico"], "CONGESTIONADO")
            print(f"    >>> [{t:.1f}s] PAINEL '{p['nome']}' LIGADO (v={v_suave*3.6:.1f} km/h) <<<")
        elif p["led_ligado"] and v_suave > p["v_sai"]:
            p["led_ligado"] = False
            traci.poi.setColor(p["painel"], (0, 255, 0, 255))
            publica(p["topico"], "LIVRE")
            print(f"    >>> [{t:.1f}s] PAINEL '{p['nome']}' DESLIGADO (v={v_suave*3.6:.1f} km/h) <<<")

        # --- CAMADA 3: atuacao (so no cenario tratado) ---
        if PAINEL_ATIVO and p["led_ligado"]:
            for e in p["zona_edges"]:
                for vid in traci.edge.getLastStepVehicleIDs(e):
                    if vid not in p["decisao"]:
                        p["decisao"][vid] = random.random() < OBEDIENCIA
                    if p["decisao"][vid]:
                        traci.vehicle.slowDown(vid, V_REACAO, DURACAO)

traci.close()
if mqtt_client:
    mqtt_client.loop_stop()
print(f"Concluido. Tripinfo: {trip_file} | Conflitos: {ssm_file}")