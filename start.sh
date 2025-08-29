#!/bin/bash
set -euo pipefail

echo "🚀 Iniciando BOT (sem WhatsApp local)…"

# --- Configurações recomendadas para logs no Railway ---
export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1
export TZ="${TZ:-America/Sao_Paulo}"

# --- Pré-checagens de ambiente ---

# 1) WhatsApp Service externo obrigatório
if ! python - << 'PY'
import os, sys
url = os.getenv("WHATSAPP_SERVICE_URL", "").strip()
if not url:
    print("ERRO: WHATSAPP_SERVICE_URL não definido.", file=sys.stderr)
    sys.exit(2)
print("OK")
PY
then
  echo "❌ WHATSAPP_SERVICE_URL não está configurado. Defina a URL do serviço WhatsApp externo (ex.: https://seuservico.railway.app)."
  exit 2
fi

# 2) (Opcional) Mostra se token está setado (sem vazar valor)
if [ -n "${WHATSAPP_API_TOKEN:-}" ]; then
  echo "🔐 WHATSAPP_API_TOKEN definido"
fi
if [ -n "${WHATSAPP_SESSION_ID:-}" ]; then
  echo "🪪 WHATSAPP_SESSION_ID=${WHATSAPP_SESSION_ID}"
fi

# 3) (Opcional) Tenta pingar /status só para log informativo (não bloqueante)
if command -v curl >/dev/null 2>&1; then
  echo "🩺 Verificando WhatsApp Service em ${WHATSAPP_SERVICE_URL}/status (best-effort)…"
  if [ -n "${WHATSAPP_API_TOKEN:-}" ]; then
    curl -fsS -H "x-api-token: ${WHATSAPP_API_TOKEN}" "${WHATSAPP_SERVICE_URL%/}/status" || true
  else
    curl -fsS "${WHATSAPP_SERVICE_URL%/}/status" || true
  fi
else
  echo "ℹ️ curl não disponível; pulando verificação HTTP."
fi

# --- IMPORTANTE ---
# Não instalamos dependências aqui. Instale tudo no build (Dockerfile ou Nixpacks).
# Nada de 'npm install' ou 'node whatsapp_baileys_multi.js' neste serviço.

echo "🤖 Iniciando Telegram bot…"
# Use exec para que o processo do bot seja o PID 1 (boas práticas em containers)
exec python main.py
