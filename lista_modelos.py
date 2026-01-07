import google.generativeai as genai
import streamlit as st

genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])

for m in genai.list_models():
    if 'generateContent' in m.supported_generation_methods:
        print(f"Nombre: {m.name}")

        