import pandas as pd
import requests
from datetime import datetime
import pytz
import os
import json
import gspread
from google.oauth2.service_account import Credentials

# ==========================================
# ⚙️ CONFIGURACIÓN DE IDS (Sustituye el ID del Historial)
# ==========================================
ID_EXCEL_HOY = "1oe6rvKg1zo-Jv7Nd8FJy0FEXolN4yvg7KnaNAAsIs94"
ID_HISTORIAL = "1RIsVJYe6PuPZsv7VU2gf4F3jla9P_SzH8UegBQvuf40" # <--- CAMBIA ESTO

ZONA_HORARIA = "Europe/Madrid"
IDS_AULAS = [1282, 1283, 1284, 1285, 1286, 1287, 1531, 1535]
URL_WU = "https://unav.webuntis.com/WebUntis/api/public/timetable/weekly/data"

FRANJAS_REALES = [
    {"inicio": 900,  "fin": 1020}, {"inicio": 1030, "fin": 1150},
    {"inicio": 1205, "fin": 1325}, {"inicio": 1500, "fin": 1620},
    {"inicio": 1630, "fin": 1750}, {"inicio": 1805, "fin": 1925}
]

def a_minutos(hora_wu):
    return (hora_wu // 100) * 60 + (hora_wu % 100)

def conectar_google():
    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
    return gspread.authorize(creds)

def mover_a_historial(client):
    print("📦 Transfiriendo datos de la hoja 'clases_hoy' al historial...")
    try:
        sheet_hoy = client.open_by_key(ID_EXCEL_HOY).worksheet("clases_hoy")
        todos_los_valores = sheet_hoy.get_all_values()
        
        if len(todos_los_valores) <= 1:
            print("⚠️ No hay datos previos para mover.")
            return

        df = pd.DataFrame(todos_los_valores[1:], columns=todos_los_valores[0])
        
        # Detectar formato y convertir
        if 'Fecha' in df.columns:
            df['Fecha_dt'] = pd.to_datetime(df['Fecha'])
        elif 'time_10m' in df.columns:
            print("🔄 Detectado formato antiguo 'time_10m'. Convirtiendo...")
            df['Fecha_dt'] = pd.to_datetime(df['time_10m'], errors='coerce')
            df['Fecha'] = df['Fecha_dt'].dt.strftime('%Y-%m-%d')
            df['Hora'] = df['Fecha_dt'].dt.strftime('%H:%M')
        
        df['Dia_Semana'] = df['Fecha_dt'].dt.weekday
        df_para_historial = df[['Fecha', 'Hora', 'Dia_Semana', 'Aulas_Ocupadas']].dropna()
        
        # Subir a Sheet1 del historial
        sheet_historial = client.open_by_key(ID_HISTORIAL).worksheet("Hoja 1")
        sheet_historial.append_rows(df_para_historial.values.tolist(), value_input_option='USER_ENTERED')
        
        # Limpiar y poner cabeceras nuevas
        sheet_hoy.clear()
        sheet_hoy.append_row(['Fecha', 'Hora', 'Aulas_Ocupadas'])
        print(f"✅ Se han movido {len(df_para_historial)} filas con éxito.")
    except Exception as e:
        print(f"❌ Error al mover datos: {e}")

def generar_prevision_hoy(client):
    print("🚀 Generando nueva previsión en la hoja 'clases_hoy'...")
    tz = pytz.timezone(ZONA_HORARIA)
    hoy = datetime.now(tz).date()
    fecha_str, fecha_int = hoy.strftime("%Y-%m-%d"), int(hoy.strftime("%Y%m%d"))
    
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

    rango_tiempo = pd.date_range(start=f"{fecha_str} 08:00", end=f"{fecha_str} 20:00", freq='10min')
    datos_nuevos = []
    df_ev = pd.DataFrame(eventos)
    for tiempo in rango_tiempo:
        minutos = tiempo.hour * 60 + tiempo.minute
        ocu = 0
        if not df_ev.empty:
            df_res = df_ev.groupby(['aula_id']).agg({'inicio': 'min', 'fin': 'max'}).reset_index()
            for f in FRANJAS_REALES:
                if a_minutos(f["inicio"]) <= minutos < a_minutos(f["fin"]):
                    ocu = sum(1 for _, c in df_res.iterrows() if a_minutos(c["inicio"]) < a_minutos(f["fin"]) and a_minutos(c["fin"]) > a_minutos(f["inicio"]))
                    break
        datos_nuevos.append([fecha_str, tiempo.strftime('%H:%M'), int(ocu)])

    sheet_hoy = client.open_by_key(ID_EXCEL_HOY).worksheet("clases_hoy")
    sheet_hoy.append_rows(datos_nuevos, value_input_option='USER_ENTERED')
    print(f"✅ Nueva previsión para {fecha_str} insertada.")

if __name__ == "__main__":
    try:
        gc = conectar_google()
        mover_a_historial(gc)
        generar_prevision_hoy(gc)
    except Exception as e:
        print(f"❌ ERROR CRÍTICO: {repr(e)}")
