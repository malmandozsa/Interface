import os
import json
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
from datetime import datetime

# 1. Configurar conexión con Google usando el secreto de GitHub
scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"]) # Lee de la nube
creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
client = gspread.authorize(creds)

# 2. Abrir la hoja (Usa el nombre exacto de tu archivo en Drive)
spreadsheet = client.open("Tecnun_Flow_Database")
worksheet = spreadsheet.worksheet("Historial")

def obtener_datos_hoy():
    """Aquí pones tu lógica actual que saca las clases"""
    # Ejemplo de lo que debería devolver (Fecha, Hora, Aulas)
    hoy = datetime.now().strftime('%Y-%m-%d')
    # ESTO ES UN EJEMPLO: Sustituye por tu lógica de scraping o cálculo
    datos = [
        [hoy, "09:00", 3],
        [hoy, "10:30", 5],
        [hoy, "12:00", 2],
        [hoy, "15:00", 4]
    ]
    return datos

# 3. Ejecutar y subir
nuevos_datos = obtener_datos_hoy()
worksheet.append_rows(nuevos_datos)
print(f"✅ ¡{len(nuevos_datos)} filas añadidas con éxito!")
