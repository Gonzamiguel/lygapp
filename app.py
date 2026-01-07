import json
import os
import re
from typing import Dict, List, Optional, Tuple

import google.generativeai as genai
import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials

# --- CONFIGURACIÓN DE L&G ---
SPREADSHEET_ID = "16ebPbHF_KIsSDgxX39diN04_69-osZDJey_OMAefLU8"

SHEET_CONFIG = {
    "factura": ("Facturas", ["Fecha", "Proveedor", "CUIT", "Nro Factura", "Producto/Servicio", "Cantidad", "Precio Unitario Neto", "IVA %", "Subtotal c/IVA", "Total Factura"]),
    "orden_compra": ("Orden de compra", ["Fecha", "Proveedor", "Nro OC", "Detalle", "Monto Total", "Condición de Pago"]),
    "orden_pago": ("Orden de pago", ["Fecha", "Proveedor", "Nro OP", "Facturas Canceladas", "Importe Bruto", "Retenciones", "Neto Pagado", "Medio de Pago"]),
}

def load_api_key():
    # Intenta obtener la clave de secrets o variable de entorno
    key = os.getenv("GOOGLE_API_KEY") or st.secrets.get("GOOGLE_API_KEY")
    if not key:
        st.error("Falta GOOGLE_API_KEY en .streamlit/secrets.toml")
        st.stop()
    return key

def init_models():
    api_key = load_api_key()
    # Forzamos la configuración estable para evitar el error v1beta
    genai.configure(api_key=api_key)
    return genai.GenerativeModel("gemini-1.5-flash")

@st.cache_resource
def get_gspread_client():
    if not os.path.exists("service_account.json"):
        st.error("No se encontró el archivo service_account.json en la carpeta.")
        return None
    try:
        creds = Credentials.from_service_account_file(
            "service_account.json",
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        return gspread.authorize(creds)
    except Exception as e:
        st.error(f"Error de credenciales: {e}")
        return None

def call_ai(model, pdf_bytes):
    # Prompt optimizado para los archivos de L&G
    prompt = """
    Eres un experto contable argentino. Analiza el PDF y extrae los datos en JSON.
    Identifica si es FACTURA (ej. Elipis/Cuper), ORDEN DE COMPRA o ORDEN DE PAGO.
    
    Reglas importantes para L&G:
    - En ORDEN DE PAGO: Extrae el Importe Bruto, las Retenciones (Ganancias/SUSS) y el Neto Pagado.
    - En FACTURAS: Una fila por cada ítem.
    
    JSON SCHEMA:
    {
      "document_type": "factura" | "orden_compra" | "orden_pago",
      "rows": [ { "Columna": "Valor" } ]
    }
    """
    
    # Usamos la llamada estándar que Gemini soporta en v1 estable
    response = model.generate_content([
        prompt,
        {"mime_type": "application/pdf", "data": pdf_bytes}
    ], generation_config={"response_mime_type": "application/json"})
    
    return json.loads(response.text)

def main():
    st.set_page_config(page_title="L&G Logística - Carga IA", layout="wide")
    st.title("Sistema de Carga Automática L&G")
    
    if "extractions" not in st.session_state:
        st.session_state["extractions"] = []

    uploaded_files = st.file_uploader("Subir comprobantes PDF", type="pdf", accept_multiple_files=True)

    if st.button("Procesar con Gemini", type="primary", disabled=not uploaded_files):
        model = init_models()
        results = []
        
        for file in uploaded_files:
            with st.spinner(f"Analizando {file.name}..."):
                try:
                    raw_data = call_ai(model, file.read())
                    doc_type = raw_data["document_type"]
                    
                    if doc_type not in SHEET_CONFIG:
                        st.warning(f"{file.name} no parece un comprobante válido.")
                        continue
                        
                    sheet_name, headers = SHEET_CONFIG[doc_type]
                    clean_rows = [[str(r.get(h, "")) for h in headers] for r in raw_data["rows"]]
                    
                    results.append({
                        "sheet": sheet_name, 
                        "headers": headers, 
                        "rows": clean_rows, 
                        "file": file.name
                    })
                    st.success(f"✓ {file.name} listo para {sheet_name}")
                except Exception as e:
                    st.error(f"Error en {file.name}: {e}")
        
        st.session_state["extractions"] = results

    if st.session_state["extractions"]:
        st.divider()
        for idx, item in enumerate(st.session_state["extractions"]):
            st.subheader(f"Archivo: {item['file']} → Hoja: {item['sheet']}")
            df = pd.DataFrame(item["rows"], columns=item["headers"])
            # Editor para corregir montos si es necesario
            edited_df = st.data_editor(df, key=f"editor_{idx}")
            st.session_state["extractions"][idx]["rows"] = edited_df.values.tolist()

        if st.button("Confirmar y Subir a Google Sheets"):
            client = get_gspread_client()
            if client:
                try:
                    sh = client.open_by_key(SPREADSHEET_ID)
                    for item in st.session_state["extractions"]:
                        ws = sh.worksheet(item["sheet"])
                        # Si la hoja está vacía, ponemos encabezados
                        if not ws.row_values(1):
                            ws.insert_row(item["headers"], index=1)
                        ws.append_rows(item["rows"], value_input_option="USER_ENTERED")
                    st.success("¡Datos cargados correctamente en Google Sheets!")
                    st.balloons()
                    st.session_state["extractions"] = []
                except Exception as e:
                    st.error(f"Error al subir: {e}")

if __name__ == "__main__":
    main()