#!/usr/bin/env python3
"""
Planeador-Aula-Rick-Bot - VERSIÓN RENDER WEB SERVICE GRATUITO
Adaptado para funcionar como web service en lugar de worker
Incluye webhook para Telegram y endpoint de health check
"""

import asyncio
import json
import os
import logging
import tempfile
import re
from datetime import datetime
from typing import Dict, List, Any, Optional
from dotenv import load_dotenv

# Importaciones para web service
from flask import Flask, request, jsonify
import threading

# Importaciones del bot original
import google.generativeai as genai
import pandas as pd
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes

# Configuración de logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Cargar variables de entorno
load_dotenv()

# Configuración
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
WEBHOOK_URL = os.getenv('WEBHOOK_URL')  # Nueva variable para webhook

# Validación de variables de entorno
if not TELEGRAM_BOT_TOKEN:
    logger.error("❌ TELEGRAM_BOT_TOKEN no encontrado")
    exit(1)

if not GOOGLE_API_KEY:
    logger.error("❌ GOOGLE_API_KEY no encontrado")
    exit(1)

# Configurar Google AI
genai.configure(api_key=GOOGLE_API_KEY)

# Crear aplicación Flask
app = Flask(__name__)

# Variable global para la aplicación de Telegram
telegram_app = None

# [Aquí iría todo el código del bot original - clases y funciones]
# Por brevedad, incluyo solo la estructura principal

class PlaneadorEducativo:
    """Clase principal del planeador educativo"""
    
    def __init__(self):
        self.model = genai.GenerativeModel('gemini-pro')
        # Cargar estándares MEN
        self.estandares_men = self.cargar_estandares_men()
    
    def cargar_estandares_men(self):
        """Cargar estándares del MEN desde archivo"""
        try:
            with open('estandares_men_detailed.txt', 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            logger.warning("⚠️ Archivo de estándares no encontrado")
            return "Estándares básicos de educación colombiana"

# Instancia global del planeador
planeador = PlaneadorEducativo()

# Handlers del bot (simplificados)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /start"""
    welcome_text = """
🎓 ¡Bienvenido al Planeador Educativo Rick! 🎓

Soy tu asistente para crear planeaciones educativas siguiendo los estándares del MEN de Colombia.

📚 **¿Qué puedo hacer por ti?**
• Crear planeaciones por competencias
• Generar actividades pedagógicas
• Adaptar contenidos por grados
• Exportar en PDF y Excel

🚀 **Comandos disponibles:**
/planear - Crear nueva planeación
/ayuda - Ver ayuda detallada

¡Empecemos a planear juntos! 📝✨
"""
    await update.message.reply_text(welcome_text)

async def planear_comando(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /planear"""
    keyboard = [
        [InlineKeyboardButton("📚 Matemáticas", callback_data="materia_matematicas")],
        [InlineKeyboardButton("🔬 Ciencias", callback_data="materia_ciencias")],
        [InlineKeyboardButton("📖 Lenguaje", callback_data="materia_lenguaje")],
        [InlineKeyboardButton("🌍 Sociales", callback_data="materia_sociales")],
        [InlineKeyboardButton("🎨 Artística", callback_data="materia_artistica")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "📚 **Selecciona la materia para tu planeación:**",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

# Configurar aplicación de Telegram
def setup_telegram_app():
    """Configurar la aplicación de Telegram"""
    global telegram_app
    
    telegram_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Agregar handlers
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("planear", planear_comando))
    
    return telegram_app

# Rutas Flask
@app.route('/')
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "ok",
        "service": "Planeador Educativo Rick Bot",
        "timestamp": datetime.now().isoformat()
    })

@app.route('/webhook', methods=['POST'])
def webhook():
    """Endpoint para recibir updates de Telegram"""
    try:
        update = Update.de_json(request.get_json(), telegram_app.bot)
        asyncio.run(telegram_app.process_update(update))
        return jsonify({"status": "ok"})
    except Exception as e:
        logger.error(f"Error procesando webhook: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/set_webhook', methods=['POST'])
def set_webhook():
    """Configurar webhook de Telegram"""
    try:
        webhook_url = request.json.get('webhook_url')
        if not webhook_url:
            return jsonify({"error": "webhook_url requerido"}), 400
        
        # Configurar webhook
        asyncio.run(telegram_app.bot.set_webhook(webhook_url))
        return jsonify({"status": "webhook configurado", "url": webhook_url})
    except Exception as e:
        logger.error(f"Error configurando webhook: {e}")
        return jsonify({"error": str(e)}), 500

def main():
    """Función principal"""
    logger.info("🚀 Iniciando Planeador Educativo Rick Bot (Web Service)")
    
    # Configurar aplicación de Telegram
    setup_telegram_app()
    
    # Configurar webhook si está definido
    if WEBHOOK_URL:
        logger.info(f"📡 Configurando webhook: {WEBHOOK_URL}")
        asyncio.run(telegram_app.bot.set_webhook(f"{WEBHOOK_URL}/webhook"))
    
    # Iniciar servidor Flask
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"🌐 Iniciando servidor web en puerto {port}")
    app.run(host='0.0.0.0', port=port, debug=False)

if __name__ == '__main__':
    main()

