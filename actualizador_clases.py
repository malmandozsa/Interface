import pandas as pd
import requests
from datetime import datetime
import pytz
import os

ZONA_HORARIA = "Europe/Madrid" 
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ARCHIVO_HOY = os.path.join(BASE_DIR, "clases_hoy.csv")
IDS_AULAS = [1282, 1283, 1284, 1285, 1286, 1287, 1531, 1535]
URL_WU = "https://unav.webuntis.com/WebUntis/api/public/timetable/weekly/data"

FRANJAS_REALES = [
    {"inicio": 900,  "fin": 1020}, {"inicio": 1030, "fin": 1150},
    {"inicio": 1205, "fin": 1325}, {"inicio": 1500, "fin": 1620},
    {"inicio": 1630, "fin": 1750}, {"inicio": 1805, "fin": 1925}
]

def a_minutos(hora_wu):
    return (hora_wu // 100) * 60 + (hora_wu % 100)

def extraer_datos_hoy():
    print("🚀 Iniciando extracción de WebUntis para el día de hoy...")
    hoy = datetime.now(pytz.timezone(ZONA_HORARIA)).date()
    fecha_str = hoy.strftime("%Y-%m-%d")
    fecha_int = int(hoy.strftime("%Y%m%d"))
    headers = {'User-Agent': 'Mozilla/5.0'}
    eventos = []
    
    for aula_id in IDS_AULAS:
        try:
            res = requests.get(URL_WU, params={"elementType": "4", "elementId": aula_id, "date": fecha_str}, headers=headers)
            if res.status_code == 200:
                clases = res.json().get("data", {}).get("result", {}).get("data", {}).get("elementPeriods", {}).get(str(aula_id), [])
                for c in clases:
                    if c.get('date') == fecha_int:
                        eventos.append({'aula_id': aula_id, 'inicio': c.get('startTime'), 'fin': c.get('endTime')})
        except: pass
            
    datos_hoy = []
    rango_tiempo = pd.date_range(start=f"{fecha_str} 08:00", end=f"{fecha_str} 20:00", freq='10min')
    
    if eventos:
        df_ev = pd.DataFrame(eventos).groupby(['aula_id']).agg({'inicio': 'min', 'fin': 'max'}).reset_index()
        for tiempo in rango_tiempo:
            minutos_actuales = tiempo.hour * 60 + tiempo.minute
            aulas_ocupadas = 0
            for f in FRANJAS_REALES:
                if a_minutos(f["inicio"]) <= minutos_actuales < a_minutos(f["fin"]):
                    aulas_ocupadas = sum(1 for _, c in df_ev.iterrows() if a_minutos(c["inicio"]) < a_minutos(f["fin"]) and a_minutos(c["fin"]) > a_minutos(f["inicio"]))
                    break
            datos_hoy.append({'time_10m': tiempo, 'Aulas_Ocupadas': aulas_ocupadas})
    else:
        for tiempo in rango_tiempo:
            datos_hoy.append({'time_10m': tiempo, 'Aulas_Ocupadas': 0})
            
    # Al final de tu script actualizador_clases.py
    # ... (todo tu código anterior igual)
    
    # 1. Convertimos la lista 'datos_hoy' en un DataFrame real
    df_final = pd.DataFrame(datos_hoy)
    
    # 2. Usamos la ruta fija que hemos comprobado que es la correcta
    ruta_fija = ARCHIVO_HOY
    
    # 3. Guardamos el DataFrame 'df_final' (antes ponía df y por eso fallaba)
    df_final.to_csv(ruta_fija, index=False)
    
    print(f"✅ Archivo guardado físicamente en: {ruta_fija}")

if __name__ == "__main__":
    extraer_datos_hoy()
    