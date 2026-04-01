import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt
import xml.etree.ElementTree as ET
import os

ARQUIVO_XML = 'conflitos_br101.xml'

if not os.path.exists(ARQUIVO_XML):
    print(f"ERRO: O arquivo '{ARQUIVO_XML}' não foi encontrado.")
else:
    try:
        tree = ET.parse(ARQUIVO_XML)
        root = tree.getroot()
        print("Arquivo XML lido com sucesso! Processando posições...")

        data = []
        # No seu XML, a posição está dentro de minTTC ou maxDRAC
        for minTTC in root.iter('minTTC'):
            pos = minTTC.get('position')
            
            if pos and pos != "NA":
                try:
                    # O SUMO gera "x,y". Vamos separar pela vírgula
                    coords = pos.split(',')
                    x = float(coords[0])
                    y = float(coords[1])
                    data.append([x, y])
                except (ValueError, IndexError):
                    continue

        if len(data) > 0:
            df = pd.DataFrame(data, columns=['X', 'Y'])
            
            plt.figure(figsize=(12, 8))
            
            # Criando o Heatmap
            sns.kdeplot(data=df, x='X', y='Y', fill=True, cmap='Reds', thresh=0, levels=100)
            
            # Adiciona os pontos pretos para referência
            plt.scatter(df['X'], df['Y'], s=5, color='black', alpha=0.3, label='Conflitos Detectados')

            plt.title("MAPA DE CALOR DE CONFLITOS (BASELINE)\nIdentificação de Pontos Críticos para Sensores IoT", fontsize=14)
            plt.grid(True, linestyle='--', alpha=0.3)
            plt.legend()
            
            print(f"Sucesso! {len(data)} pontos de conflito processados.")
            plt.show()
        else:
            print("AVISO: Nenhum conflito com posição válida encontrado.")

    except ET.ParseError as e:
        print(f"ERRO ao ler o XML: {e}")