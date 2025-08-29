#!/usr/bin/env python3
"""
Bot Telegram - Sistema de Gestão de Clientes - VERSÃO COMPLETA
Sistema completo com cobrança Baileys, mensagens automáticas e templates editáveis
"""

import os
import sys
import logging
from datetime import datetime, timedelta
import pytz
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ConversationHandler, CallbackQueryHandler
from telegram import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from database import DatabaseManager
from scheduler import MessageScheduler
from templates import TemplateManager

try:
    from baileys_api import BaileysAPI  # original project class
except Exception:
    import os, requests, time, base64, io
    class BaileysAPI:
        """Fallback wrapper that talks to the external WhatsApp service (Baileys HTTP)."""
        def __init__(self, base_url: str | None = None):
            self.base_url = (base_url or os.getenv("WHATSAPP_SERVICE_URL", "http://127.0.0.1:8080")).rstrip("/")
            self.session_id = os.getenv("WHATSAPP_SESSION_ID", "1")
            self.timeout = int(os.getenv("WHATSAPP_HTTP_TIMEOUT", "30"))
            self.headers = {"Content-Type": "application/json"}
        def get_config(self):
            return {"auto_reconnect": True, "message_timeout": self.timeout, "delivery_receipts": True}
        def _status(self):
            try:
                r = requests.get(f"{self.base_url}/status/{self.session_id}", timeout=self.timeout)
                return r.json() if r.status_code == 200 else {"success": False}
            except Exception as e:
                return {"success": False, "error": str(e)}
        def get_status(self, user_id=None):
            s = self._status()
            return {
                "connected": bool(s.get("connected")),
                "state": s.get("state"),
                "qr_available": bool(s.get("qrCode")),
                "raw": s
            }
        def qr_code_needed(self, user_id=None):
            s = self._status()
            return not bool(s.get("connected")) and bool(s.get("qrCode"))
        def generate_qr_code(self, user_id=None):
            # Force a new QR and poll status for up to 20s
            import requests, time, base64, io
            try:
                requests.post(f"{self.base_url}/force-qr/{self.session_id}", timeout=self.timeout)
            except Exception:
                pass
            deadline = time.time() + 20
            last = None
            while time.time() < deadline:
                s = self._status()
                last = s
                qr = s.get("qrCode")
                if qr:
                    # qr is expected to be a data URL; convert to bytes for Telegram
                    if isinstance(qr, str) and qr.startswith("data:image"):
                        b64 = qr.split(",",1)[1]
                        try:
                            png = base64.b64decode(b64)
                            return {"success": True, "qr_code": png}
                        except Exception:
                            return {"success": True, "qr_code": qr}  # fallback to URL
                    return {"success": True, "qr_code": qr}
                time.sleep(2)
            return {"success": False, "error": "QR not available", "details": last}
        def send_message(self, phone: str, message: str):
            import requests, json
            payload = {"number": phone, "message": message}
            r = requests.post(f"{self.base_url}/send/{self.session_id}", json=payload, timeout=self.timeout)
            try:
                j = r.json()
            except Exception:
                j = {"success": False, "error": r.text, "code": r.status_code}
            return j

from utils import *
import asyncio

# Configurar timezone brasileiro
TIMEZONE_BR = pytz.timezone('America/Sao_Paulo')

# Configurar logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO)
logger = logging.getLogger(__name__)

# Estados da conversação para cadastro de cliente
NOME, TELEFONE, PACOTE, VALOR, SERVIDOR, VENCIMENTO, CONFIRMAR = range(7)

# Estados para edição de cliente
EDIT_NOME, EDIT_TELEFONE, EDIT_PACOTE, EDIT_VALOR, EDIT_SERVIDOR, EDIT_VENCIMENTO = range(7, 13)

# Estados para configurações
CONFIG_EMPRESA, CONFIG_PIX, CONFIG_SUPORTE = range(13, 16)

# Estados para edição de templates
TEMPLATE_EDIT_CONTENT = 16

# Estados para criação de novos templates
TEMPLATE_NEW_NAME, TEMPLATE_NEW_CONTENT = 17, 18

# Estados para envio manual de mensagens
SEND_MESSAGE_SELECT_CLIENT, SEND_MESSAGE_TYPE, SEND_MESSAGE_CUSTOM = range(19, 22)

# Instâncias globais - serão inicializadas no main
db = None
scheduler = None
template_manager = None
baileys_api = None

def verificar_admin(func):
    """Decorator para verificar se é admin"""
    async def wrapper(update, context):
        admin_id = int(os.getenv('ADMIN_CHAT_ID', '0'))
        if update.effective_chat.id != admin_id:
            await update.message.reply_text(
                "❌ Acesso negado. Apenas o admin pode usar este bot.")
            return
        return await func(update, context)
    return wrapper

@verificar_admin
async def start(update, context):
    """Comando /start"""
    nome_admin = update.effective_user.first_name
    
    try:
        total_clientes = len(db.listar_clientes(apenas_ativos=True))
        # Admin vê todos os clientes (sem filtro de usuário)
        clientes_vencendo = len(db.listar_clientes_vencendo(dias=7, chat_id_usuario=None))
    except Exception as e:
        logger.error(f"Erro ao buscar estatísticas: {e}")
        total_clientes = 0
        clientes_vencendo = 0

    mensagem = f"""🤖 *Bot de Gestão de Clientes*

Olá *{nome_admin}*! 

✅ Sistema inicializado com sucesso!
📊 Total de clientes: {total_clientes}
⚠️ Vencimentos próximos (7 dias): {clientes_vencendo}

Use os botões abaixo para navegar:
👥 *Gestão de Clientes* - Gerenciar clientes
📱 *WhatsApp/Baileys* - Sistema de cobrança
📄 *Templates* - Gerenciar mensagens
⏰ *Agendador* - Mensagens automáticas
📊 *Relatórios* - Estatísticas do sistema

🚀 Sistema 100% operacional!"""

    await update.message.reply_text(mensagem,
                                    parse_mode='Markdown',
                                    reply_markup=criar_teclado_principal())

# === SISTEMA DE CADASTRO ESCALONÁVEL ===

@verificar_admin
async def iniciar_cadastro(update, context):
    """Inicia o processo de cadastro de cliente"""
    await update.message.reply_text(
        "📝 *Cadastro de Novo Cliente*\n\n"
        "Vamos cadastrar um cliente passo a passo.\n\n"
        "**Passo 1/6:** Digite o *nome completo* do cliente:",
        parse_mode='Markdown',
        reply_markup=criar_teclado_cancelar())
    return NOME

async def receber_nome(update, context):
    """Recebe o nome do cliente"""
    if update.message.text == "❌ Cancelar":
        return await cancelar_cadastro(update, context)

    nome = update.message.text.strip()
    if len(nome) < 2:
        await update.message.reply_text(
            "❌ Nome muito curto. Digite um nome válido:",
            reply_markup=criar_teclado_cancelar())
        return NOME

    context.user_data['nome'] = nome

    await update.message.reply_text(
        f"✅ Nome: *{nome}*\n\n"
        "**Passo 2/6:** Digite o *telefone* (apenas números):\n\n"
        "*Exemplo:* 11999999999",
        parse_mode='Markdown',
        reply_markup=criar_teclado_cancelar())
    return TELEFONE

async def receber_telefone(update, context):
    """Recebe o telefone do cliente"""
    if update.message.text == "❌ Cancelar":
        return await cancelar_cadastro(update, context)

    telefone = update.message.text.strip().replace(' ', '').replace('-', '').replace('(', '').replace(')', '')

    if not telefone.isdigit() or len(telefone) < 10:
        await update.message.reply_text(
            "❌ Telefone inválido. Digite apenas números (ex: 11999999999):",
            reply_markup=criar_teclado_cancelar())
        return TELEFONE

    # Verificar se telefone já existe
    cliente_existente = db.buscar_cliente_por_telefone(telefone)
    if cliente_existente:
        await update.message.reply_text(
            f"❌ Já existe um cliente cadastrado com este telefone:\n"
            f"*{cliente_existente['nome']}*\n\n"
            "Digite outro telefone ou cancele:",
            parse_mode='Markdown',
            reply_markup=criar_teclado_cancelar())
        return TELEFONE

    context.user_data['telefone'] = telefone

    await update.message.reply_text(
        f"✅ Telefone: *{telefone}*\n\n"
        "**Passo 3/6:** Escolha o *plano de duração*:\n\n"
        "Selecione uma das opções ou digite um plano personalizado:",
        parse_mode='Markdown',
        reply_markup=criar_teclado_planos())
    return PACOTE

async def receber_pacote(update, context):
    """Recebe o pacote do cliente"""
    if update.message.text == "❌ Cancelar":
        return await cancelar_cadastro(update, context)

    texto = update.message.text.strip()

    # Processar botões de planos predefinidos
    if texto == "📅 1 mês":
        pacote = "Plano 1 mês"
    elif texto == "📅 3 meses":
        pacote = "Plano 3 meses"
    elif texto == "📅 6 meses":
        pacote = "Plano 6 meses"
    elif texto == "📅 1 ano":
        pacote = "Plano 1 ano"
    elif texto == "✏️ Personalizado":
        await update.message.reply_text(
            "✏️ Digite o nome do seu plano personalizado:\n\n"
            "*Exemplos:* Netflix Premium, Disney+ 4K, Combo Streaming",
            parse_mode='Markdown',
            reply_markup=criar_teclado_cancelar())
        return PACOTE
    else:
        # Plano personalizado digitado diretamente
        pacote = texto
        if len(pacote) < 2:
            await update.message.reply_text(
                "❌ Nome do pacote muito curto. Digite um nome válido:",
                reply_markup=criar_teclado_planos())
            return PACOTE

    context.user_data['pacote'] = pacote

    # Calcular data de vencimento automática baseada no plano
    hoje = agora_br().replace(tzinfo=None)
    duracao_msg = ""

    if "1 mês" in pacote:
        vencimento_auto = hoje + timedelta(days=30)
        duracao_msg = " (vence em 30 dias)"
    elif "3 meses" in pacote:
        vencimento_auto = hoje + timedelta(days=90)
        duracao_msg = " (vence em 90 dias)"
    elif "6 meses" in pacote:
        vencimento_auto = hoje + timedelta(days=180)
        duracao_msg = " (vence em 180 dias)"
    elif "1 ano" in pacote:
        vencimento_auto = hoje + timedelta(days=365)
        duracao_msg = " (vence em 1 ano)"
    else:
        vencimento_auto = hoje + timedelta(days=30)  # Padrão: 30 dias
        duracao_msg = " (vencimento padrão: 30 dias)"

    # Salvar data calculada automaticamente
    context.user_data['vencimento_auto'] = vencimento_auto.strftime('%Y-%m-%d')

    await update.message.reply_text(
        f"✅ Pacote: *{pacote}*{duracao_msg}\n\n"
        "**Passo 4/6:** Escolha o *valor mensal*:\n\n"
        "Selecione um valor ou digite um personalizado:",
        parse_mode='Markdown',
        reply_markup=criar_teclado_valores())
    return VALOR

async def receber_valor(update, context):
    """Recebe o valor do plano"""
    if update.message.text == "❌ Cancelar":
        return await cancelar_cadastro(update, context)

    texto = update.message.text.strip()

    # Processar botões de valores predefinidos
    if texto == "💰 R$ 30,00":
        valor = 30.00
    elif texto == "💰 R$ 35,00":
        valor = 35.00
    elif texto == "💰 R$ 40,00":
        valor = 40.00
    elif texto == "💰 R$ 45,00":
        valor = 45.00
    elif texto == "💰 R$ 50,00":
        valor = 50.00
    elif texto == "💰 R$ 60,00":
        valor = 60.00
    elif texto == "💰 R$ 70,00":
        valor = 70.00
    elif texto == "💰 R$ 90,00":
        valor = 90.00
    elif texto == "💰 R$ 135,00":
        valor = 135.00
    elif texto == "✏️ Valor personalizado":
        await update.message.reply_text(
            "✏️ Digite o valor personalizado:\n\n"
            "*Exemplos:* 25.50, 80, 120.00",
            reply_markup=criar_teclado_cancelar())
        return VALOR
    else:
        # Valor personalizado digitado
        try:
            valor_texto = texto.replace('R$', '').replace(',', '.').strip()
            valor = float(valor_texto)
            if valor <= 0:
                raise ValueError("Valor deve ser positivo")
        except ValueError:
            await update.message.reply_text(
                "❌ Valor inválido. Digite um número válido (ex: 45.50):",
                reply_markup=criar_teclado_valores())
            return VALOR

    context.user_data['valor'] = valor

    await update.message.reply_text(
        f"✅ Valor: *R$ {valor:.2f}*\n\n"
        "**Passo 5/6:** Digite o *servidor/login* do cliente:\n\n"
        "*Exemplo:* user123, cliente@email.com",
        parse_mode='Markdown',
        reply_markup=criar_teclado_cancelar())
    return SERVIDOR

async def receber_servidor(update, context):
    """Recebe o servidor/login do cliente"""
    if update.message.text == "❌ Cancelar":
        return await cancelar_cadastro(update, context)

    servidor = update.message.text.strip()
    if len(servidor) < 2:
        await update.message.reply_text(
            "❌ Servidor muito curto. Digite um servidor válido:",
            reply_markup=criar_teclado_cancelar())
        return SERVIDOR

    context.user_data['servidor'] = servidor

    # Mostrar data de vencimento calculada automaticamente
    vencimento_auto = context.user_data['vencimento_auto']
    data_formatada = formatar_data_br(vencimento_auto)

    await update.message.reply_text(
        f"✅ Servidor: *{servidor}*\n\n"
        "**Passo 6/6:** Data de vencimento:\n\n"
        f"📅 *Data calculada automaticamente:* {data_formatada}\n\n"
        "Deseja usar esta data ou personalizar?",
        parse_mode='Markdown',
        reply_markup=criar_teclado_vencimento())
    return VENCIMENTO

async def receber_vencimento(update, context):
    """Recebe a data de vencimento"""
    if update.message.text == "❌ Cancelar":
        return await cancelar_cadastro(update, context)

    texto = update.message.text.strip()

    if texto == "✅ Usar data automática":
        vencimento = context.user_data['vencimento_auto']
    elif texto == "📅 Data personalizada":
        await update.message.reply_text(
            "📅 Digite a data de vencimento personalizada:\n\n"
            "*Formato:* DD/MM/AAAA\n"
            "*Exemplo:* 15/02/2024",
            reply_markup=criar_teclado_cancelar())
        return VENCIMENTO
    else:
        # Data personalizada digitada
        try:
            vencimento_dt = datetime.strptime(texto, '%d/%m/%Y')
            hoje = agora_br().replace(tzinfo=None)
            
            if vencimento_dt.date() < hoje.date():
                await update.message.reply_text(
                    "❌ Data não pode ser anterior a hoje. Digite uma data válida:",
                    reply_markup=criar_teclado_vencimento())
                return VENCIMENTO
                
            vencimento = vencimento_dt.strftime('%Y-%m-%d')
        except ValueError:
            await update.message.reply_text(
                "❌ Data inválida. Use o formato DD/MM/AAAA:",
                reply_markup=criar_teclado_vencimento())
            return VENCIMENTO

    context.user_data['vencimento'] = vencimento

    # Mostrar resumo para confirmação
    dados = context.user_data
    resumo = f"""📋 *RESUMO DO CLIENTE*

👤 **Nome:** {dados['nome']}
📞 **Telefone:** {dados['telefone']}
📦 **Pacote:** {dados['pacote']}
💰 **Valor:** R$ {dados['valor']:.2f}
🖥️ **Servidor:** {dados['servidor']}
📅 **Vencimento:** {formatar_data_br(dados['vencimento'])}

Confirma o cadastro?"""

    await update.message.reply_text(resumo,
                                    parse_mode='Markdown',
                                    reply_markup=criar_teclado_confirmar())
    return CONFIRMAR

async def confirmar_cadastro(update, context):
    """Confirma e salva o cadastro do cliente"""
    texto = update.message.text.strip()

    if texto == "❌ Cancelar":
        return await cancelar_cadastro(update, context)
    elif texto == "✏️ Editar":
        await update.message.reply_text(
            "✏️ Qual campo deseja editar?",
            reply_markup=criar_teclado_edicao())
        return CONFIRMAR
    elif texto == "✅ Confirmar":
        try:
            dados = context.user_data
            
            # Cadastrar cliente no banco
            cliente_id = db.cadastrar_cliente(
                nome=dados['nome'],
                telefone=dados['telefone'],
                pacote=dados['pacote'],
                valor=dados['valor'],
                servidor=dados['servidor'],
                vencimento=dados['vencimento']
            )

            # Agendar mensagens automáticas
            scheduler.agendar_mensagens_cliente(cliente_id)

            # Limpar dados da sessão
            context.user_data.clear()

            mensagem_sucesso = f"""✅ *CLIENTE CADASTRADO COM SUCESSO!*

🆔 **ID:** {cliente_id}
👤 **Nome:** {dados['nome']}
📞 **Telefone:** {dados['telefone']}
📦 **Pacote:** {dados['pacote']}
💰 **Valor:** R$ {dados['valor']:.2f}
🖥️ **Servidor:** {dados['servidor']}
📅 **Vencimento:** {formatar_data_br(dados['vencimento'])}

🤖 Mensagens automáticas agendadas!
📱 Cliente será notificado via WhatsApp nos prazos corretos."""

            await update.message.reply_text(mensagem_sucesso,
                                            parse_mode='Markdown',
                                            reply_markup=criar_teclado_principal())
            return ConversationHandler.END

        except Exception as e:
            logger.error(f"Erro ao cadastrar cliente: {e}")
            await update.message.reply_text(
                f"❌ Erro ao cadastrar cliente: {str(e)}\n\n"
                "Tente novamente ou entre em contato com o suporte.",
                reply_markup=criar_teclado_principal())
            return ConversationHandler.END

async def cancelar_cadastro(update, context):
    """Cancela o processo de cadastro"""
    context.user_data.clear()
    await update.message.reply_text(
        "❌ Cadastro cancelado.",
        reply_markup=criar_teclado_principal())
    return ConversationHandler.END

# === SISTEMA DE LISTAGEM E BUSCA ===

@verificar_admin
async def listar_clientes(update, context):
    """Lista todos os clientes"""
    try:
        clientes = db.listar_clientes(apenas_ativos=True)
        
        if not clientes:
            await update.message.reply_text(
                "📭 Nenhum cliente cadastrado ainda.\n\n"
                "Use ➕ *Adicionar Cliente* para começar!",
                parse_mode='Markdown',
                reply_markup=criar_teclado_principal())
            return

        # Dividir em páginas se necessário
        CLIENTES_POR_PAGINA = 10
        total_paginas = (len(clientes) + CLIENTES_POR_PAGINA - 1) // CLIENTES_POR_PAGINA
        
        pagina = int(context.args[0]) if context.args and context.args[0].isdigit() else 1
        pagina = max(1, min(pagina, total_paginas))
        
        inicio = (pagina - 1) * CLIENTES_POR_PAGINA
        fim = inicio + CLIENTES_POR_PAGINA
        clientes_pagina = clientes[inicio:fim]

        mensagem = f"👥 *LISTA DE CLIENTES* (Página {pagina}/{total_paginas})\n\n"
        
        for i, cliente in enumerate(clientes_pagina, start=inicio + 1):
            venc_formatado = formatar_data_br(cliente['vencimento'])
            status_venc = "🔴" if cliente['dias_vencimento'] < 0 else "🟡" if cliente['dias_vencimento'] <= 3 else "🟢"
            
            mensagem += f"{i}. {status_venc} *{cliente['nome']}*\n"
            mensagem += f"   📞 {cliente['telefone']}\n"
            mensagem += f"   📦 {cliente['pacote']}\n"
            mensagem += f"   💰 R$ {cliente['valor']:.2f}\n"
            mensagem += f"   📅 {venc_formatado}\n"
            mensagem += f"   🆔 ID: {cliente['id']}\n\n"

        # Botões de navegação
        keyboard = []
        nav_buttons = []
        
        if pagina > 1:
            nav_buttons.append(InlineKeyboardButton("⬅️ Anterior", callback_data=f"lista_page_{pagina-1}"))
        if pagina < total_paginas:
            nav_buttons.append(InlineKeyboardButton("➡️ Próximo", callback_data=f"lista_page_{pagina+1}"))
            
        if nav_buttons:
            keyboard.append(nav_buttons)
            
        keyboard.append([InlineKeyboardButton("🔍 Buscar Cliente", callback_data="buscar_cliente")])
        keyboard.append([InlineKeyboardButton("➕ Adicionar Cliente", callback_data="adicionar_cliente")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(mensagem,
                                        parse_mode='Markdown',
                                        reply_markup=reply_markup)

    except Exception as e:
        logger.error(f"Erro ao listar clientes: {e}")
        await update.message.reply_text(
            f"❌ Erro ao listar clientes: {str(e)}",
            reply_markup=criar_teclado_principal())

@verificar_admin
async def buscar_cliente(update, context):
    """Busca cliente por nome ou telefone"""
    if not context.args:
        await update.message.reply_text(
            "🔍 *Buscar Cliente*\n\n"
            "Digite o nome ou telefone para buscar:\n\n"
            "*Exemplos:*\n"
            "• `/buscar João`\n"
            "• `/buscar 11999999999`",
            parse_mode='Markdown')
        return

    termo = ' '.join(context.args)
    try:
        clientes = db.buscar_clientes(termo)
        
        if not clientes:
            await update.message.reply_text(
                f"🔍 Nenhum cliente encontrado com: *{termo}*",
                parse_mode='Markdown',
                reply_markup=criar_teclado_principal())
            return

        mensagem = f"🔍 *RESULTADOS DA BUSCA:* {termo}\n\n"
        
        for cliente in clientes[:10]:  # Limitar a 10 resultados
            venc_formatado = formatar_data_br(cliente['vencimento'])
            status_venc = "🔴" if cliente['dias_vencimento'] < 0 else "🟡" if cliente['dias_vencimento'] <= 3 else "🟢"
            
            mensagem += f"{status_venc} *{cliente['nome']}*\n"
            mensagem += f"📞 {cliente['telefone']}\n"
            mensagem += f"📦 {cliente['pacote']}\n"
            mensagem += f"💰 R$ {cliente['valor']:.2f}\n"
            mensagem += f"📅 {venc_formatado}\n"
            mensagem += f"🆔 ID: {cliente['id']}\n\n"

        if len(clientes) > 10:
            mensagem += f"... e mais {len(clientes) - 10} resultado(s)"

        await update.message.reply_text(mensagem,
                                        parse_mode='Markdown',
                                        reply_markup=criar_teclado_principal())

    except Exception as e:
        logger.error(f"Erro ao buscar cliente: {e}")
        await update.message.reply_text(
            f"❌ Erro na busca: {str(e)}",
            reply_markup=criar_teclado_principal())

# === SISTEMA DE RELATÓRIOS ===

@verificar_admin
async def relatorios(update, context):
    """Mostra relatórios e estatísticas"""
    try:
        stats = db.obter_estatisticas()
        
        mensagem = f"""📊 *RELATÓRIOS E ESTATÍSTICAS*

👥 **Clientes:**
• Total ativo: {stats['total_clientes']}
• Novos este mês: {stats['novos_mes']}

💰 **Financeiro:**
• Receita mensal: R$ {stats['receita_mensal']:.2f}
• Receita anual: R$ {stats['receita_anual']:.2f}

⚠️ **Vencimentos:**
• Vencidos: {stats['vencidos']} clientes
• Vencem hoje: {stats['vencem_hoje']} clientes
• Vencem em 3 dias: {stats['vencem_3dias']} clientes
• Vencem esta semana: {stats['vencem_semana']} clientes

📱 **WhatsApp/Baileys:**
• Status: {baileys_api.get_status()}
• Mensagens enviadas hoje: {stats['mensagens_hoje']}
• Fila de mensagens: {stats['fila_mensagens']}

📄 **Templates:**
• Total de templates: {stats['total_templates']}
• Mais usado: {stats['template_mais_usado']}"""

        keyboard = [
            [InlineKeyboardButton("📈 Relatório Detalhado", callback_data="relatorio_detalhado")],
            [InlineKeyboardButton("📊 Exportar Dados", callback_data="exportar_dados")],
            [InlineKeyboardButton("🔄 Atualizar", callback_data="atualizar_stats")]
        ]
        
        await update.message.reply_text(mensagem,
                                        parse_mode='Markdown',
                                        reply_markup=InlineKeyboardMarkup(keyboard))

    except Exception as e:
        logger.error(f"Erro ao gerar relatórios: {e}")
        await update.message.reply_text(
            f"❌ Erro ao gerar relatórios: {str(e)}",
            reply_markup=criar_teclado_principal())

# === SISTEMA DE TEMPLATES ===

@verificar_admin
async def gerenciar_templates(update, context):
    """Gerencia templates de mensagens"""
    try:
        templates = template_manager.listar_templates()
        
        mensagem = "📄 *GERENCIAR TEMPLATES*\n\n"
        
        if not templates:
            mensagem += "📭 Nenhum template criado ainda.\n\n"
        else:
            for template in templates:
                mensagem += f"• *{template['nome']}*\n"
                mensagem += f"  📝 {template['descricao']}\n"
                mensagem += f"  📊 Usado {template['uso_count']} vez(es)\n\n"

        keyboard = [
            [InlineKeyboardButton("➕ Novo Template", callback_data="novo_template")],
            [InlineKeyboardButton("✏️ Editar Template", callback_data="editar_template")],
            [InlineKeyboardButton("🗑️ Excluir Template", callback_data="excluir_template")],
            [InlineKeyboardButton("👀 Visualizar Template", callback_data="visualizar_template")]
        ]
        
        await update.message.reply_text(mensagem,
                                        parse_mode='Markdown',
                                        reply_markup=InlineKeyboardMarkup(keyboard))

    except Exception as e:
        logger.error(f"Erro ao gerenciar templates: {e}")
        await update.message.reply_text(
            f"❌ Erro ao gerenciar templates: {str(e)}",
            reply_markup=criar_teclado_principal())

# === SISTEMA DE AGENDAMENTO ===

@verificar_admin
async def gerenciar_agendador(update, context):
    """Gerencia o sistema de agendamento"""
    try:
        tarefas_pendentes = scheduler.obter_tarefas_pendentes()
        proximas_execucoes = scheduler.obter_proximas_execucoes(limit=5)
        
        mensagem = f"""⏰ *SISTEMA DE AGENDAMENTO*

📊 **Status:**
• Agendador: {'🟢 Ativo' if scheduler.is_running() else '🔴 Inativo'}
• Tarefas pendentes: {len(tarefas_pendentes)}
• Última verificação: {scheduler.ultima_verificacao()}

📅 **Próximas Execuções:**
"""
        
        for execucao in proximas_execucoes:
            mensagem += f"• {execucao['data']} - {execucao['tipo']} - {execucao['cliente']}\n"
            
        if not proximas_execucoes:
            mensagem += "• Nenhuma execução agendada\n"

        keyboard = [
            [InlineKeyboardButton("▶️ Iniciar Agendador", callback_data="start_scheduler")],
            [InlineKeyboardButton("⏸️ Pausar Agendador", callback_data="pause_scheduler")],
            [InlineKeyboardButton("🔄 Reagendar Todos", callback_data="reagendar_todos")],
            [InlineKeyboardButton("📋 Ver Fila Completa", callback_data="ver_fila_completa")]
        ]
        
        await update.message.reply_text(mensagem,
                                        parse_mode='Markdown',
                                        reply_markup=InlineKeyboardMarkup(keyboard))

    except Exception as e:
        logger.error(f"Erro ao gerenciar agendador: {e}")
        await update.message.reply_text(
            f"❌ Erro ao gerenciar agendador: {str(e)}",
            reply_markup=criar_teclado_principal())

# === SISTEMA WHATSAPP/BAILEYS ===

@verificar_admin
async def whatsapp_status(update, context):
    """Mostra status do WhatsApp/Baileys"""
    try:
        status = baileys_api.get_status()
        qr_needed = baileys_api.qr_code_needed()
        
        mensagem = f"""📱 *STATUS WHATSAPP/BAILEYS*

🔗 **Conexão:** {status['status']}
📞 **Número:** {status.get('numero', 'Não conectado')}
🔋 **Bateria:** {status.get('bateria', 'N/A')}%
📶 **Última conexão:** {status.get('ultima_conexao', 'N/A')}

📊 **Estatísticas de hoje:**
• Mensagens enviadas: {status.get('mensagens_enviadas', 0)}
• Mensagens falharam: {status.get('mensagens_falharam', 0)}
• Fila pendente: {status.get('fila_pendente', 0)}

{"📷 QR Code necessário para reconexão!" if qr_needed else "✅ Dispositivo conectado!"}"""

        keyboard = []
        
        if qr_needed:
            keyboard.append([InlineKeyboardButton("📷 Gerar QR Code", callback_data="gerar_qr")])
        
        keyboard.extend([
            [InlineKeyboardButton("🧪 Testar Envio", callback_data="testar_whatsapp")],
            [InlineKeyboardButton("🔄 Reconectar", callback_data="reconectar_whatsapp")],
            [InlineKeyboardButton("📱 Configurações", callback_data="config_whatsapp")]
        ])
        
        await update.message.reply_text(mensagem,
                                        parse_mode='Markdown',
                                        reply_markup=InlineKeyboardMarkup(keyboard))

    except Exception as e:
        logger.error(f"Erro ao verificar status WhatsApp: {e}")
        await update.message.reply_text(
            f"❌ Erro ao verificar status: {str(e)}",
            reply_markup=criar_teclado_principal())

@verificar_admin
async def gerar_qr_code(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """
    Gera um novo QR Code (força regeneração no servidor Baileys HTTP) e envia como imagem no Telegram.
    Compatível com o wrapper BaileysAPI acima.
    """
    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        # força QR e busca a imagem
        data = baileys_api.generate_qr_code(user_id)
        if data and data.get("success"):
            qr_payload = data.get("qr_code")
            # Se for bytes, manda como foto; se for URL, o Telegram baixa
            if isinstance(qr_payload, (bytes, bytearray)):
                from io import BytesIO
                bio = BytesIO(qr_payload)
                bio.name = "qrcode.png"
                await update.effective_chat.send_photo(photo=bio, caption="📲 Escaneie este QR no seu WhatsApp para conectar.")
            else:
                await update.effective_chat.send_photo(photo=qr_payload, caption="📲 Escaneie este QR no seu WhatsApp para conectar.")
            return True, "QR enviado"
        else:
            # como fallback, tente verificar o status e pegar qrCode direto
            status = baileys_api.get_status(user_id)
            raw = status.get("raw", {})
            qr = raw.get("qrCode")
            if qr:
                if isinstance(qr, str) and qr.startswith("data:image"):
                    import base64
                    from io import BytesIO
                    b64 = qr.split(",",1)[1]
                    png = base64.b64decode(b64)
                    bio = BytesIO(png); bio.name="qrcode.png"
                    await update.effective_chat.send_photo(photo=bio, caption="📲 Escaneie este QR no seu WhatsApp para conectar.")
                else:
                    await update.effective_chat.send_photo(photo=qr, caption="📲 Escaneie este QR no seu WhatsApp para conectar.")
                return True, "QR enviado (fallback)"
            await update.effective_chat.send_message("❌ Não foi possível gerar o QR agora. Tente novamente.")
            return False, "sem qr"
    except Exception as e:
        logger.exception("Erro ao gerar/enviar QR: %s", e)
        try:
            await update.effective_chat.send_message(f"❌ Erro ao gerar QR: {e}")
        except Exception:
            pass
        return False, str(e)

async def testar_whatsapp(update, context):
    """Testa envio de mensagem WhatsApp"""
    try:
        # Pegar número do admin
        admin_phone = os.getenv('ADMIN_PHONE')
        if not admin_phone:
            await update.message.reply_text(
                "❌ Número do admin não configurado.\n"
                "Configure a variável ADMIN_PHONE no ambiente.",
                reply_markup=criar_teclado_principal())
            return

        await update.message.reply_text("🧪 Enviando mensagem de teste...")
        
        resultado = baileys_api.send_message(
            phone=admin_phone,
            message="🧪 Teste do Sistema de Cobrança\n\n"
                   "Esta é uma mensagem de teste do bot de gestão de clientes.\n\n"
                   f"✅ Sistema funcionando corretamente!\n"
                   f"🕐 {formatar_datetime_br(agora_br())}"
        )
        
        if resultado['success']:
            await update.message.reply_text(
                "✅ Mensagem de teste enviada com sucesso!\n\n"
                f"📱 Enviado para: {admin_phone}\n"
                f"🆔 ID da mensagem: {resultado.get('message_id', 'N/A')}",
                reply_markup=criar_teclado_principal())
        else:
            await update.message.reply_text(
                f"❌ Falha no envio de teste:\n{resultado.get('error', 'Erro desconhecido')}",
                reply_markup=criar_teclado_principal())

    except Exception as e:
        logger.error(f"Erro no teste WhatsApp: {e}")
        await update.message.reply_text(
            f"❌ Erro no teste: {str(e)}",
            reply_markup=criar_teclado_principal())

# === SISTEMA DE ENVIO MANUAL ===

@verificar_admin
async def envio_manual(update, context):
    """Inicia processo de envio manual de mensagem"""
    try:
        clientes = db.listar_clientes(apenas_ativos=True)
        
        if not clientes:
            await update.message.reply_text(
                "📭 Nenhum cliente cadastrado para envio.",
                reply_markup=criar_teclado_principal())
            return ConversationHandler.END

        # Criar lista de clientes para seleção
        mensagem = "👥 *ENVIO MANUAL DE MENSAGEM*\n\n"
        mensagem += "Selecione o cliente ou digite o ID:\n\n"
        
        keyboard = []
        for i, cliente in enumerate(clientes[:10]):  # Limitar a 10 para não ficar muito grande
            keyboard.append([InlineKeyboardButton(
                f"{cliente['nome']} - {cliente['telefone']}", 
                callback_data=f"select_client_{cliente['id']}"
            )])
        
        if len(clientes) > 10:
            keyboard.append([InlineKeyboardButton("📄 Ver todos os clientes", callback_data="ver_todos_clientes")])
            
        keyboard.append([InlineKeyboardButton("❌ Cancelar", callback_data="cancelar_envio")])
        
        await update.message.reply_text(mensagem,
                                        parse_mode='Markdown',
                                        reply_markup=InlineKeyboardMarkup(keyboard))
        return SEND_MESSAGE_SELECT_CLIENT

    except Exception as e:
        logger.error(f"Erro no envio manual: {e}")
        await update.message.reply_text(
            f"❌ Erro no envio manual: {str(e)}",
            reply_markup=criar_teclado_principal())
        return ConversationHandler.END

# === HANDLERS DE CALLBACK ===

async def handle_callback(update, context):
    """Handle para botões inline"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    try:
        # Navegação da lista de clientes
        if data.startswith('lista_page_'):
            pagina = int(data.split('_')[2])
            context.args = [str(pagina)]
            return await listar_clientes(update, context)
        
        # Seleção de cliente para envio
        elif data.startswith('select_client_'):
            cliente_id = int(data.split('_')[2])
            context.user_data['cliente_selecionado'] = cliente_id
            
            # Mostrar opções de template
            templates = template_manager.listar_templates()
            
            mensagem = "📄 *Escolha o tipo de mensagem:*\n\n"
            
            keyboard = []
            for template in templates:
                keyboard.append([InlineKeyboardButton(
                    template['nome'], 
                    callback_data=f"template_{template['id']}"
                )])
            
            keyboard.append([InlineKeyboardButton("✏️ Mensagem personalizada", callback_data="mensagem_custom")])
            keyboard.append([InlineKeyboardButton("❌ Cancelar", callback_data="cancelar_envio")])
            
            await query.edit_message_text(mensagem,
                                         parse_mode='Markdown',
                                         reply_markup=InlineKeyboardMarkup(keyboard))
            return SEND_MESSAGE_TYPE
        
        # Seleção de template
        elif data.startswith('template_'):
            template_id = int(data.split('_')[1])
            cliente_id = context.user_data['cliente_selecionado']
            
            # Buscar dados do cliente e template
            cliente = db.buscar_cliente_por_id(cliente_id)
            template = template_manager.obter_template(template_id)
            
            # Processar template com dados do cliente
            mensagem_processada = template_manager.processar_template(template['conteudo'], cliente)
            
            # Confirmar envio
            mensagem_confirmacao = f"""📱 *CONFIRMAR ENVIO*

👤 **Cliente:** {cliente['nome']}
📞 **Telefone:** {cliente['telefone']}
📄 **Template:** {template['nome']}

💬 **Mensagem que será enviada:**
{mensagem_processada}

Confirma o envio?"""

            keyboard = [
                [InlineKeyboardButton("✅ Confirmar Envio", callback_data=f"confirm_send_{cliente_id}_{template_id}")],
                [InlineKeyboardButton("❌ Cancelar", callback_data="cancelar_envio")]
            ]
            
            await query.edit_message_text(mensagem_confirmacao,
                                         parse_mode='Markdown',
                                         reply_markup=InlineKeyboardMarkup(keyboard))
        
        # Confirmar envio
        elif data.startswith('confirm_send_'):
            parts = data.split('_')
            cliente_id = int(parts[2])
            template_id = int(parts[3])
            
            cliente = db.buscar_cliente_por_id(cliente_id)
            template = template_manager.obter_template(template_id)
            mensagem_processada = template_manager.processar_template(template['conteudo'], cliente)
            
            # Enviar mensagem
            resultado = baileys_api.send_message(
                phone=cliente['telefone'],
                message=mensagem_processada
            )
            
            if resultado['success']:
                # Registrar envio no banco
                db.registrar_envio_manual(cliente_id, template_id, mensagem_processada)
                
                await query.edit_message_text(
                    f"✅ Mensagem enviada com sucesso!\n\n"
                    f"👤 Cliente: {cliente['nome']}\n"
                    f"📞 Telefone: {cliente['telefone']}\n"
                    f"🆔 ID da mensagem: {resultado.get('message_id', 'N/A')}"
                )
            else:
                await query.edit_message_text(
                    f"❌ Falha no envio:\n{resultado.get('error', 'Erro desconhecido')}"
                )
        
        # Outros callbacks...
        elif data == "cancelar_envio":
            await query.edit_message_text("❌ Envio cancelado.")
        
        elif data == "gerar_qr":
            return await gerar_qr_code(update, context)
        
        elif data == "testar_whatsapp":
            return await testar_whatsapp(update, context)

    except Exception as e:
        logger.error(f"Erro no callback: {e}")
        await query.edit_message_text(f"❌ Erro: {str(e)}")

# === HANDLERS DE TEXTO ===

@verificar_admin
async def handle_message(update, context):
    """Handler para mensagens de texto (botões do teclado)"""
    texto = update.message.text
    
    # Gestão de Clientes
    if texto == "👥 Listar Clientes":
        return await listar_clientes(update, context)
    elif texto == "➕ Adicionar Cliente":
        return await iniciar_cadastro(update, context)
    elif texto == "🔍 Buscar Cliente":
        await update.message.reply_text(
            "🔍 Digite o nome ou telefone para buscar:\n\n"
            "*Exemplo:* João Silva ou 11999999999",
            parse_mode='Markdown')
        return
    elif texto == "📊 Relatórios":
        return await relatorios(update, context)
    
    # Templates
    elif texto == "📄 Templates":
        return await gerenciar_templates(update, context)
    
    # Agendador
    elif texto == "⏰ Agendador":
        return await gerenciar_agendador(update, context)
    elif texto == "📋 Fila de Mensagens":
        return await ver_fila_mensagens(update, context)
    elif texto == "📜 Logs de Envios":
        return await ver_logs_envios(update, context)
    
    # WhatsApp
    elif texto == "📱 WhatsApp Status":
        return await whatsapp_status(update, context)
    elif texto == "🧪 Testar WhatsApp":
        return await testar_whatsapp(update, context)
    elif texto == "📱 QR Code":
        return await gerar_qr_code(update, context)
    elif texto == "⚙️ Gerenciar WhatsApp":
        return await gerenciar_whatsapp(update, context)
    
    # Configurações
    elif texto == "🏢 Empresa":
        return await configurar_empresa(update, context)
    elif texto == "💳 PIX":
        return await configurar_pix(update, context)
    elif texto == "📞 Suporte":
        return await configurar_suporte(update, context)
    
    # Ajuda
    elif texto == "❓ Ajuda":
        return await mostrar_ajuda(update, context)
    
    # Envio manual
    elif texto == "📤 Envio Manual":
        return await envio_manual(update, context)
    
    else:
        # Verificar se é uma busca direta
        if len(texto) > 2:
            context.args = [texto]
            return await buscar_cliente(update, context)

# === FUNÇÕES AUXILIARES ===

async def ver_fila_mensagens(update, context):
    """Mostra fila de mensagens pendentes"""
    try:
        fila = scheduler.obter_fila_mensagens()
        
        if not fila:
            await update.message.reply_text(
                "📋 Fila de mensagens vazia.\n\n"
                "✅ Todas as mensagens foram enviadas!",
                reply_markup=criar_teclado_principal())
            return

        mensagem = f"📋 *FILA DE MENSAGENS* ({len(fila)} pendentes)\n\n"
        
        for item in fila[:10]:  # Mostrar apenas os 10 primeiros
            mensagem += f"⏰ {item['agendado_para']}\n"
            mensagem += f"👤 {item['cliente_nome']}\n"
            mensagem += f"📱 {item['telefone']}\n"
            mensagem += f"📄 {item['tipo_mensagem']}\n"
            mensagem += f"🆔 ID: {item['id']}\n\n"

        if len(fila) > 10:
            mensagem += f"... e mais {len(fila) - 10} mensagens na fila"

        keyboard = [
            [InlineKeyboardButton("🔄 Atualizar", callback_data="atualizar_fila")],
            [InlineKeyboardButton("⏸️ Pausar Fila", callback_data="pausar_fila")],
            [InlineKeyboardButton("🗑️ Limpar Fila", callback_data="limpar_fila")]
        ]

        await update.message.reply_text(mensagem,
                                        parse_mode='Markdown',
                                        reply_markup=InlineKeyboardMarkup(keyboard))

    except Exception as e:
        logger.error(f"Erro ao ver fila: {e}")
        await update.message.reply_text(
            f"❌ Erro ao visualizar fila: {str(e)}",
            reply_markup=criar_teclado_principal())

async def ver_logs_envios(update, context):
    """Mostra logs de envios recentes"""
    try:
        logs = db.obter_logs_envios(limit=20)
        
        if not logs:
            await update.message.reply_text(
                "📜 Nenhum envio registrado ainda.",
                reply_markup=criar_teclado_principal())
            return

        mensagem = f"📜 *LOGS DE ENVIOS* (últimos {len(logs)})\n\n"
        
        for log in logs:
            status_icon = "✅" if log['sucesso'] else "❌"
            mensagem += f"{status_icon} {log['data_envio']}\n"
            mensagem += f"👤 {log['cliente_nome']}\n"
            mensagem += f"📱 {log['telefone']}\n"
            mensagem += f"📄 {log['tipo']}\n"
            if not log['sucesso']:
                mensagem += f"⚠️ Erro: {log['erro']}\n"
            mensagem += "\n"

        await update.message.reply_text(mensagem,
                                        parse_mode='Markdown',
                                        reply_markup=criar_teclado_principal())

    except Exception as e:
        logger.error(f"Erro ao ver logs: {e}")
        await update.message.reply_text(
            f"❌ Erro ao visualizar logs: {str(e)}",
            reply_markup=criar_teclado_principal())

async def gerenciar_whatsapp(update, context):
    """Gerencia configurações avançadas do WhatsApp"""
    try:
        config = baileys_api.get_config()
        
        mensagem = f"""⚙️ *CONFIGURAÇÕES WHATSAPP*

🔧 **Configurações atuais:**
• Auto-reconectar: {config.get('auto_reconnect', False)}
• Timeout mensagens: {config.get('message_timeout', 30)}s
• Máx. tentativas: {config.get('max_retries', 3)}
• Intervalo entre mensagens: {config.get('message_interval', 2)}s

📊 **Estatísticas:**
• Sessão ativa há: {config.get('session_duration', 'N/A')}
• Total de mensagens: {config.get('total_messages', 0)}
• Taxa de sucesso: {config.get('success_rate', 0)}%"""

        keyboard = [
            [InlineKeyboardButton("🔄 Auto-reconectar ON/OFF", callback_data="toggle_auto_reconnect")],
            [InlineKeyboardButton("⏱️ Configurar Timeouts", callback_data="config_timeouts")],
            [InlineKeyboardButton("🔧 Configurações Avançadas", callback_data="config_advanced")],
            [InlineKeyboardButton("🗑️ Limpar Sessão", callback_data="clear_session")]
        ]

        await update.message.reply_text(mensagem,
                                        parse_mode='Markdown',
                                        reply_markup=InlineKeyboardMarkup(keyboard))

    except Exception as e:
        logger.error(f"Erro ao gerenciar WhatsApp: {e}")
        await update.message.reply_text(
            f"❌ Erro ao gerenciar WhatsApp: {str(e)}",
            reply_markup=criar_teclado_principal())

async def configurar_empresa(update, context):
    """Configura dados da empresa"""
    await update.message.reply_text(
        "🏢 *CONFIGURAÇÕES DA EMPRESA*\n\n"
        "Em desenvolvimento...\n\n"
        "Esta funcionalidade permitirá configurar:\n"
        "• Nome da empresa\n"
        "• Logo\n"
        "• Dados de contato\n"
        "• Informações para templates",
        parse_mode='Markdown',
        reply_markup=criar_teclado_principal())

async def configurar_pix(update, context):
    """Configura dados do PIX"""
    await update.message.reply_text(
        "💳 *CONFIGURAÇÕES PIX*\n\n"
        "Em desenvolvimento...\n\n"
        "Esta funcionalidade permitirá configurar:\n"
        "• Chave PIX\n"
        "• QR Code automático\n"
        "• Dados do beneficiário\n"
        "• Templates de cobrança",
        parse_mode='Markdown',
        reply_markup=criar_teclado_principal())

async def configurar_suporte(update, context):
    """Configura dados de suporte"""
    await update.message.reply_text(
        "📞 *CONFIGURAÇÕES DE SUPORTE*\n\n"
        "Em desenvolvimento...\n\n"
        "Esta funcionalidade permitirá configurar:\n"
        "• Telefone de suporte\n"
        "• Email de contato\n"
        "• Horário de atendimento\n"
        "• Links úteis",
        parse_mode='Markdown',
        reply_markup=criar_teclado_principal())

async def mostrar_ajuda(update, context):
    """Mostra ajuda completa do sistema"""
    mensagem = """❓ *AJUDA COMPLETA DO SISTEMA*

🤖 **Bot de Gestão de Clientes**
Sistema completo para gestão de clientes com cobrança automática via WhatsApp.

👥 **GESTÃO DE CLIENTES:**
• `/start` - Iniciar o bot
• `👥 Listar Clientes` - Ver todos os clientes
• `➕ Adicionar Cliente` - Cadastro escalonável
• `🔍 Buscar Cliente` - Buscar por nome/telefone
• `/buscar <termo>` - Busca direta

📱 **WHATSAPP/BAILEYS:**
• `📱 WhatsApp Status` - Ver status da conexão
• `📱 QR Code` - Gerar código para conectar
• `🧪 Testar WhatsApp` - Enviar mensagem teste
• `⚙️ Gerenciar WhatsApp` - Configurações avançadas

📄 **TEMPLATES:**
• `📄 Templates` - Gerenciar templates
• Templates com variáveis: `{nome}`, `{telefone}`, `{valor}`, etc.

⏰ **AGENDAMENTO AUTOMÁTICO:**
• Mensagens 2 dias antes do vencimento
• Mensagem no dia do vencimento
• Mensagem 1 dia após vencimento
• `⏰ Agendador` - Controlar sistema
• `📋 Fila de Mensagens` - Ver pendências

📊 **RELATÓRIOS:**
• `📊 Relatórios` - Estatísticas completas
• `📜 Logs de Envios` - Histórico de mensagens

🔧 **CONFIGURAÇÕES:**
• `🏢 Empresa` - Dados da empresa
• `💳 PIX` - Configurar cobrança
• `📞 Suporte` - Dados de contato

🆘 **SUPORTE:**
Entre em contato com o administrador do sistema para suporte técnico."""

    await update.message.reply_text(mensagem,
                                    parse_mode='Markdown',
                                    reply_markup=criar_teclado_principal())

# === FUNÇÃO PRINCIPAL ===

async def main():
    """Função principal do bot"""
    global db, scheduler, template_manager, baileys_api
    
    # Verificar variáveis de ambiente obrigatórias
    bot_token = os.getenv('BOT_TOKEN')
    admin_chat_id = os.getenv('ADMIN_CHAT_ID')
    
    if not bot_token:
        logger.error("BOT_TOKEN não encontrado nas variáveis de ambiente")
        sys.exit(1)
    
    if not admin_chat_id:
        logger.error("ADMIN_CHAT_ID não encontrado nas variáveis de ambiente")
        sys.exit(1)

    try:
        # Inicializar componentes
        logger.info("Inicializando banco de dados...")
        db = DatabaseManager()
        
        logger.info("Inicializando sistema de templates...")
        template_manager = TemplateManager(db)
        
        logger.info("Inicializando API Baileys...")
        baileys_api = BaileysAPI()
        
        logger.info("Inicializando agendador...")
        scheduler = MessageScheduler(db, baileys_api, template_manager)
        
        # Criar aplicação do bot
        application = Application.builder().token(bot_token).build()

        # Conversation handler para cadastro de cliente
        conv_handler_cadastro = ConversationHandler(
            entry_points=[MessageHandler(filters.Regex("^➕ Adicionar Cliente$"), iniciar_cadastro)],
            states={
                NOME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_nome)],
                TELEFONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_telefone)],
                PACOTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_pacote)],
                VALOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_valor)],
                SERVIDOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_servidor)],
                VENCIMENTO: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_vencimento)],
                CONFIRMAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirmar_cadastro)]
            },
            fallbacks=[CommandHandler('cancel', cancelar_cadastro)]
        )

        # Conversation handler para envio manual
        conv_handler_envio = ConversationHandler(
            entry_points=[MessageHandler(filters.Regex("^📤 Envio Manual$"), envio_manual)],
            states={
                SEND_MESSAGE_SELECT_CLIENT: [CallbackQueryHandler(handle_callback)],
                SEND_MESSAGE_TYPE: [CallbackQueryHandler(handle_callback)],
                SEND_MESSAGE_CUSTOM: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_callback)]
            },
            fallbacks=[CallbackQueryHandler(handle_callback, pattern="^cancelar_envio$")]
        )

        # Adicionar handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("buscar", buscar_cliente))
        application.add_handler(conv_handler_cadastro)
        application.add_handler(conv_handler_envio)
        application.add_handler(CallbackQueryHandler(handle_callback))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        logger.info("Bot iniciado com sucesso!")
        
        # Iniciar agendador
        scheduler.start()
        
        # Executar bot
        await application.run_polling(allowed_updates=['message', 'callback_query'])

    except Exception as e:
        logger.error(f"Erro ao inicializar bot: {e}")
        sys.exit(1)

if __name__ == '__main__':
    asyncio.run(main())
