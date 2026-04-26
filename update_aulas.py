import pandas as pd
import requests
from datetime import datetime
import pytz
import os
import json
import gspread
from google.oauth2.service_account import Credentials

# ==========================================
# ⚙️ CONFIGURACIÓN DE IDS
# ==========================================
ID_EXCEL_HOY = "1oe6rvKg1zo-Jv7Nd8FJy0FEXolN4yvg7KnaNAAsIs94"
ID_HISTORIAL = "1obld-nMYrcctyG-yciteVyQJUjLO6X2mpS3ue3dDcdw"

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
    print("📦 Transfiriendo datos de hoy al historial...")
    sheet_hoy = client.open_by_key(ID_EXCEL_HOY).sheet1
    datos = sheet_hoy.get_all_records()
    
    if not datos:
        print("⚠️ No hay datos previos para mover.")
        return

    df = pd.DataFrame(datos)
    # Suponiendo que tus columnas se llaman 'Fecha', 'Hora', 'Aulas_Ocupadas'
    df['Fecha_dt'] = pd.to_datetime(df['Fecha'])
    df['Dia_Semana'] = df['Fecha_dt'].dt.weekday
    
    df_para_historial = df[['Fecha', 'Hora', 'Dia_Semana', 'Aulas_Ocupadas']]
    
    sheet_historial = client.open_by_key(ID_HISTORIAL).sheet1
    sheet_historial.append_rows(df_para_historial.values.tolist(), value_input_option='USER_ENTERED')
    
    # Limpiar la hoja (borrar desde la fila 2 para mantener el encabezado)
    num_filas = len(datos) + 1
    sheet_hoy.delete_rows(2, num_filas)
    print(f"✅ Se han movido {len(datos)} filas y limpiado el Excel temporal.")

def generar_prevision_hoy(client):
    print("🚀 Generando nueva previsión para el día de hoy...")
    tz = pytz.timezone(ZONA_HORARIA)
    hoy = datetime.now(tz).date()
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

    # Rango de 08:00 a 20:00 cada 10 min
    rango_tiempo = pd.date_range(start=f"{fecha_str} 08:00", end=f"{fecha_str} 20:00", freq='10min')
    datos_nuevos = []
    
    df_ev = pd.DataFrame(eventos)
    for tiempo in rango_tiempo:
        minutos_actuales = tiempo.hour * 60 + tiempo.minute
        aulas_ocupadas = 0
        
        if not df_ev.empty:
            df_resumen = df_ev.groupby(['aula_id']).agg({'inicio': 'min', 'fin': 'max'}).reset_index()
            for f in FRANJAS_REALES:
                if a_minutos(f["inicio"]) <= minutos_actuales < a_minutos(f["fin"]):
                    aulas_ocupadas = sum(1 for _, c in df_resumen.iterrows() if a_minutos(c["inicio"]) < a_minutos(f["fin"]) and a_minutos(c["fin"]) > a_minutos(f["inicio"]))
                    break
        
        datos_nuevos.append([fecha_str, tiempo.strftime('%H:%M'), int(aulas_ocupadas)])

    sheet_hoy = client.open_by_key(ID_EXCEL_HOY).sheet1
    sheet_hoy.append_rows(datos_nuevos, value_input_option='USER_ENTERED')
    print(f"✅ Previsión de hoy ({fecha_str}) insertada con éxito.")

if __name__ == "__main__":
    try:
        gc = conectar_google()
        # 1. Primero vaciamos lo de ayer
        mover_a_historial(gc)
        # 2. Luego ponemos lo de hoy
        generar_prevision_hoy(gc)
    except Exception as e:
        print(f"❌ ERROR CRÍTICO: {repr(e)}")
