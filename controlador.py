#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Controlador de sinalizacao inteligente (TraCI + SUMO)
Trecho BR-101 Ilhota-Balneario Camboriu.

Fluxo:  sensor E2 (cauda da fila)  ->  logica de estado  ->  painel (POI + MQTT)  ->  reacao dos veiculos

Tres estados de operacao:
  LIVRE          (verde)  -> fluxo normal
  LENTO          (ambar)  -> movimento lento, atencao
  CONGESTIONADO  (verm.)  -> parada/quase parada, reduza ja

Rode duas vezes para o experimento:
  1) PAINEL_ATIVO = False  -> cenario base (sem intervencao)
  2) PAINEL_ATIVO = True   -> cenario com o painel atuando

MODO TESTE (TESTE_CONGESTIONAMENTO = True):
  Em vez de esperar uma fila natural, injeta um PERFIL DE LEITURA que passa
  pelos tres estados de forma determinista e SEMPRE volta ao verde:
     LIVRE -> LENTO -> CONGESTIONADO -> LENTO -> LIVRE
  Os veiculos reais continuam reagindo (CAMADA 3) e o POI/MQTT acionam normal.
  DESLIGUE (False) para o experimento de verdade (sensor real no comando).
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

SUMO_BINARY  = "sumo-gui" 
CONFIG       = "osm.sumocfg"

# --- elementos declarados no sensores_painel.add.xml ---
SENSORES   = ["sensor_t0", "sensor_t1"]                # detectores no gargalo
PAINEL     = "painel_led"                               # POI que representa o PMV
ZONA_EDGES = ["977998188#0", "977998188#1"]            # trecho a montante onde os veiculos reduzem

# --- limiares dos 3 estados (com histerese em cada fronteira p/ nao piscar) ---
#   sobe de estado = fluxo melhora | desce = fluxo piora
#   fronteira LIVRE <-> LENTO
V_LIVRE_LENTO_DESCE = 15.0   # m/s (~54 km/h): abaixo disso, LIVRE vira LENTO
V_LENTO_LIVRE_SOBE  = 18.0   # m/s (~65 km/h): acima disso, LENTO volta a LIVRE
#   fronteira LENTO <-> CONGESTIONADO
V_LENTO_CONG_DESCE  =  9.0   # m/s (~32 km/h): abaixo disso, LENTO vira CONGESTIONADO
V_CONG_LENTO_SOBE   = 12.0   # m/s (~43 km/h): acima disso, CONGESTIONADO volta a LENTO

JANELA    = 30     # passos p/ suavizar a leitura (30 * 0.1s = 3 s)
MIN_DWELL = 3.0    # s: tempo minimo em um estado antes de poder trocar de novo
                   #    (garante que o AMBAR fique visivel e evita flapping)

# --- reacao dos motoristas ao "ler" o painel ---
OBEDIENCIA     = 0.75   # 75% dos motoristas obedecem (realismo p/ a banca)
V_REACAO       = 13.9   # m/s (~50 km/h): alvo de quem reduz no CONGESTIONADO
V_REACAO_LENTO = 16.7   # m/s (~60 km/h): alvo (mais suave) no estado LENTO
DURACAO        = 8.0    # segundos p/ desacelerar suavemente (evita a onda de choque)

# ----------------------------------------------------------------------
# TESTE: perfil de leitura sintetico (determinista)
#   Cada janela e longa o bastante p/ o estado ficar bem visivel.
#   Fora dessas janelas a leitura volta a LIVRE, garantindo o verde no fim.
# ----------------------------------------------------------------------
TESTE_CONGESTIONAMENTO = True     # <-- False no experimento real!
T_LENTO_1 = (60.0, 200.0)         # 60-200s : LENTO (12 m/s)
T_CONG    = (200.0, 300.0)        # 120-180s: CONGESTIONADO (5 m/s)
T_LENTO_2 = (300.0, 400.0)        # 180-240s: LENTO de novo (12 m/s)
V_TESTE_LIVRE = 25.0              # m/s (~90 km/h)
V_TESTE_LENTO = 12.0              # m/s (~43 km/h) -> cai na faixa de LENTO
V_TESTE_CONG  =  5.0              # m/s (~18 km/h) -> cai na faixa de CONGESTIONADO

# ----------------------------------------------------------------------
# TraCI / SUMO
# ----------------------------------------------------------------------
if "SUMO_HOME" not in os.environ:
    sys.exit("Defina a variavel de ambiente SUMO_HOME apontando p/ sua instalacao do SUMO.")
sys.path.append(os.path.join(os.environ["SUMO_HOME"], "tools"))
import traci  # noqa: E402

# arquivos de saida separados por cenario, p/ nao sobrescrever
sufixo    = "painel" if PAINEL_ATIVO else "base"
ssm_file  = f"conflitos_{sufixo}.xml"
trip_file = f"tripinfos_{sufixo}.xml"

sumo_cmd = [
    SUMO_BINARY, "-c", CONFIG,
    "--seed", str(SEMENTE),
    "--device.ssm.file", ssm_file,   
    "--tripinfo-output", trip_file,
    "--start", "--quit-on-end",
]

# ----------------------------------------------------------------------
# MQTT (opcional): acende o painel virtual no navegador.
# ----------------------------------------------------------------------
mqtt_client = None
try:
    import paho.mqtt.client as mqtt
    try:
        mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)  # paho >= 2.0
    except AttributeError:
        mqtt_client = mqtt.Client()                                  # paho < 2.0
    mqtt_client.connect("broker.hivemq.com", 1883, 60)
    mqtt_client.loop_start()
    print("MQTT conectado.")
except Exception as e:
    print(f"MQTT indisponivel ({e}). Seguindo so com o POI na GUI.")

def publica(estado):
    if mqtt_client:
        try:
            mqtt_client.publish("rodovia/painel_led", estado, retain=True)
        except Exception:
            pass

# cores do POI na GUI p/ cada estado
CORES_POI = {
    "LIVRE":         (0, 255, 0, 255),      # verde
    "LENTO":         (255, 176, 0, 255),    # ambar
    "CONGESTIONADO": (255, 0, 0, 255),      # vermelho
}

def aplicar_estado(estado, t, v_kmh):
    """Pinta o POI, publica no MQTT e loga a troca de estado."""
    traci.poi.setColor(PAINEL, CORES_POI[estado])
    publica(estado)
    print(f"[{t:7.1f}s] PAINEL -> {estado:<13s} (v={v_kmh:.1f} km/h)")

# ----------------------------------------------------------------------
# FUNCOES
# ----------------------------------------------------------------------
def leitura_sensor():
    """Velocidade media ponderada pelo numero de veiculos nos detectores (real)."""
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

def leitura_teste(t):
    """Perfil sintetico p/ exercitar os 3 estados (so no modo teste)."""
    if   T_LENTO_1[0] <= t < T_LENTO_1[1]: v = V_TESTE_LENTO
    elif T_CONG[0]    <= t < T_CONG[1]:    v = V_TESTE_CONG
    elif T_LENTO_2[0] <= t < T_LENTO_2[1]: v = V_TESTE_LENTO
    else:                                  v = V_TESTE_LIVRE
    return v, 10 

def proximo_estado(estado, v, n):
    """Maquina de 3 estados com histerese. Move um degrau por vez, entao
    o painel sempre passa por LENTO ao ir de LIVRE p/ CONGESTIONADO e vice-versa."""
    if n == 0:
        return estado                    
    if estado == "LIVRE":
        if v < V_LIVRE_LENTO_DESCE:
            return "LENTO"
    elif estado == "LENTO":
        if v < V_LENTO_CONG_DESCE:
            return "CONGESTIONADO"
        elif v > V_LENTO_LIVRE_SOBE:
            return "LIVRE"
    elif estado == "CONGESTIONADO":
        if v > V_CONG_LENTO_SOBE:
            return "LENTO"
    return estado


# LOOP PRINCIPAL
random.seed(SEMENTE)
traci.start(sumo_cmd)

# --- estado inicial explicito: painel nasce verde ("PISTA LIVRE") ---
estado         = "LIVRE"
t_ultima_troca = 0.0
traci.poi.setColor(PAINEL, CORES_POI[estado])
publica(estado)

hist    = deque(maxlen=JANELA)
decisao = {} 

print(f"Iniciando cenario: {'COM painel' if PAINEL_ATIVO else 'BASE (sem painel)'}")
if TESTE_CONGESTIONAMENTO:
    print(">>> MODO TESTE: leitura roteirizada LIVRE->LENTO->CONGESTIONADO->LENTO->LIVRE")

TEMPO_FIM = 3600.0 #-- tempo total de simulação ---
while traci.simulation.getMinExpectedNumber() > 0 and traci.simulation.getTime() < TEMPO_FIM:
    traci.simulationStep()
    t = traci.simulation.getTime()

    # --- CAMADA 1: leitura (teste roteirizado OU sensor real) ---
    if TESTE_CONGESTIONAMENTO:
        v_media, n = leitura_teste(t)
    else:
        v_media, n = leitura_sensor()
    if v_media is not None:
        hist.append(v_media)
    v_suave = sum(hist) / len(hist) if hist else 99.0

    # --- DIAGNOSTICO: imprime a leitura a cada 10 s ---
    if abs(t % 10.0) < 0.05:
        vk = v_suave * 3.6 if v_suave < 90 else float('nan')
        print(f"[{t:7.1f}s] leitura: {n:3d} veic | v = {vk:5.1f} km/h | estado={estado}")

    # --- CAMADA 2: logica de estado (3 estados + histerese + dwell) ---
    novo = proximo_estado(estado, v_suave, n)
    if novo != estado and (t - t_ultima_troca) >= MIN_DWELL:
        estado = novo
        t_ultima_troca = t
        aplicar_estado(estado, t, v_suave * 3.6)

    # --- CAMADA 3: atuacao (so no cenario tratado, quando nao esta LIVRE) ---
    if PAINEL_ATIVO and estado != "LIVRE":
        alvo_v = V_REACAO if estado == "CONGESTIONADO" else V_REACAO_LENTO
        for e in ZONA_EDGES:
            for vid in traci.edge.getLastStepVehicleIDs(e):
                if vid not in decisao:
                    decisao[vid] = random.random() < OBEDIENCIA
                if decisao[vid]:
                    traci.vehicle.slowDown(vid, alvo_v, DURACAO)

traci.close()
if mqtt_client:
    mqtt_client.loop_stop()
print(f"Concluido. Conflitos gravados em: {ssm_file}")