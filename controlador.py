#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Controlador de sinalizacao inteligente (TraCI + SUMO)
Trecho BR-101 Ilhota-Balneario Camboriu.

Fluxo:  sensor E2 (cauda da fila)  ->  logica de estado  ->  painel (POI + MQTT)  ->  reacao dos veiculos

Rode duas vezes para o experimento:
  1) PAINEL_ATIVO = False  -> cenario base (sem intervencao)
  2) PAINEL_ATIVO = True   -> cenario com o painel atuando
Cada rodada grava seu proprio arquivo de conflitos (SSM) para a Fase 4.
"""

import os
import sys
import random
from collections import deque

# ----------------------------------------------------------------------
# CONFIGURACAO
# ----------------------------------------------------------------------
PAINEL_ATIVO = True          # <-- False para o cenario base, True para o tratado
SEMENTE      = 42            # mesma semente nas duas rodadas = mesmo transito

SUMO_BINARY  = "sumo-gui"        # "sumo-gui" p/ ver rodando, "sumo" (sem GUI) p/ o experimento valendo
CONFIG       = "osm.sumocfg"

# --- cenario de demanda (gerado por gerar_cenarios.py) ---
CENARIO      = "alto_fluxo"  # "baixo_fluxo" | "medio_fluxo" | "alto_fluxo"
ROUTE_FILE   = f"cenario_{CENARIO}.rou.xml"

# --- elementos declarados no sensores_painel.add.xml ---
SENSORES   = ["sensor_t0", "sensor_t1"]                # detectores no gargalo (2 faixas)
PAINEL     = "painel_led"                               # POI que representa o PMV
ZONA_EDGES = ["977998188#0", "977998188#1"]            # trecho a montante onde os veiculos reduzem

# --- limiares da logica (com histerese p/ nao piscar) ---
V_ENTRA = 11.0   # m/s (~40 km/h): abaixo disso, liga o painel (congestionado)
V_SAI   = 15.0   # m/s (~54 km/h): so desliga quando volta a subir
JANELA  = 30     # passos p/ suavizar a leitura (30 * 0.1s = 3 s)

# --- reacao dos motoristas ao "ler" o painel ---
OBEDIENCIA = 0.75   # 75% dos motoristas obedecem (realismo p/ a banca)
V_REACAO   = 13.9   # m/s (~50 km/h): velocidade-alvo de quem reduz
DURACAO    = 8.0    # segundos p/ desacelerar suavemente (evita a onda de choque)

# ----------------------------------------------------------------------
# TraCI / SUMO
# ----------------------------------------------------------------------
if "SUMO_HOME" not in os.environ:
    sys.exit("Defina a variavel de ambiente SUMO_HOME apontando p/ sua instalacao do SUMO.")
sys.path.append(os.path.join(os.environ["SUMO_HOME"], "tools"))
import traci  # noqa: E402

# arquivos de saida separados por cenario+condicao, p/ nao sobrescrever
sufixo    = "painel" if PAINEL_ATIVO else "base"
ssm_file  = f"conflitos_{CENARIO}_{sufixo}.xml"
trip_file = f"tripinfos_{CENARIO}_{sufixo}.xml"

sumo_cmd = [
    SUMO_BINARY, "-c", CONFIG,
    "--route-files", ROUTE_FILE,       # sobrescreve as rotas p/ usar o cenario escolhido
    "--seed", str(SEMENTE),
    "--device.ssm.file", ssm_file,     # sobrescreve o nome do arquivo de conflitos
    "--tripinfo-output", trip_file,
    "--start", "--quit-on-end",
]

# ----------------------------------------------------------------------
# MQTT (opcional): acende o painel virtual no navegador.
# Se paho nao estiver instalado ou sem rede, o script roda mesmo assim.
# ----------------------------------------------------------------------
mqtt_client = None
try:
    import paho.mqtt.client as mqtt
    mqtt_client = mqtt.Client()
    mqtt_client.connect("broker.hivemq.com", 1883, 60)
    mqtt_client.loop_start()
    print("MQTT conectado.")
except Exception as e:
    print(f"MQTT indisponivel ({e}). Seguindo so com o POI na GUI.")

def publica(estado):
    if mqtt_client:
        try:
            mqtt_client.publish("rodovia/painel_led", estado)
        except Exception:
            pass

# ----------------------------------------------------------------------
# FUNCOES
# ----------------------------------------------------------------------
def leitura_sensor():
    """Velocidade media ponderada pelo nº de veiculos nos 3 detectores."""
    soma_v, soma_n = 0.0, 0
    for d in SENSORES:
        n = traci.lanearea.getLastStepVehicleNumber(d)
        v = traci.lanearea.getLastStepMeanSpeed(d)   # -1 quando vazio
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

hist       = deque(maxlen=JANELA)
led_ligado = False
decisao    = {}   # vid -> obedece? (decidido uma vez por veiculo)

print(f"Iniciando cenario: {CENARIO} | {'COM painel' if PAINEL_ATIVO else 'BASE (sem painel)'}")

TEMPO_FIM = 3600.0   # encerra exatamente aqui (ignora veiculos residuais na fila)
while traci.simulation.getMinExpectedNumber() > 0 and traci.simulation.getTime() < TEMPO_FIM:
    traci.simulationStep()

    # --- CAMADA 1: leitura do sensor ---
    v_media, n = leitura_sensor()
    if v_media is not None:
        hist.append(v_media)
    v_suave = sum(hist) / len(hist) if hist else 99.0

    # --- DIAGNOSTICO: imprime a leitura do sensor a cada 10 s ---
    t = traci.simulation.getTime()
    if abs(t % 10.0) < 0.05:
        vk = v_suave * 3.6 if v_suave < 90 else float('nan')
        print(f"[{t:7.1f}s] sensor: {n:3d} veic | v_media = {vk:5.1f} km/h")

    # --- CAMADA 2: logica de estado (com histerese) ---
    if not led_ligado and v_suave < V_ENTRA and n > 0:
        led_ligado = True
        traci.poi.setColor(PAINEL, (255, 0, 0, 255))      # vermelho
        publica("CONGESTIONADO")
        print(f"[{traci.simulation.getTime():7.1f}s] PAINEL LIGADO  (v={v_suave*3.6:.1f} km/h)")
    elif led_ligado and v_suave > V_SAI:
        led_ligado = False
        traci.poi.setColor(PAINEL, (0, 255, 0, 255))      # verde
        publica("LIVRE")
        print(f"[{traci.simulation.getTime():7.1f}s] PAINEL DESLIGADO (v={v_suave*3.6:.1f} km/h)")

    # --- CAMADA 3: atuacao (so no cenario tratado) ---
    if PAINEL_ATIVO and led_ligado:
        for e in ZONA_EDGES:
            for vid in traci.edge.getLastStepVehicleIDs(e):
                if vid not in decisao:
                    decisao[vid] = random.random() < OBEDIENCIA
                if decisao[vid]:
                    traci.vehicle.slowDown(vid, V_REACAO, DURACAO)
                    # --- alternativa mais simples (troque o loop acima por 1 linha): ---
                    # traci.lane.setMaxSpeed(e + "_0", V_REACAO)

traci.close()
if mqtt_client:
    mqtt_client.loop_stop()
print(f"Concluido. Conflitos gravados em: {ssm_file}")