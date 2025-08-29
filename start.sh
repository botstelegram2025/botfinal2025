#!/usr/bin/env bash
set -euo pipefail

echo "🚀 Iniciando BOT (sem WhatsApp local)…"

# --- Logs/execução ideais para Railway ---
export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1
export TZ="${TZ:-America/Sao_Paulo}"

# --- Pré-checagens de ambiente ---

# 1) WhatsApp Service externo obrigatório
if ! python - << 'PY'
import os, sys
url = (os.getenv("WHATSAPP_SERVICE_URL") or "").strip().rstrip("/")
assert url, "WHATSAPP_SERVICE_URL vazio"
print("OK")
PY
then
  echo "❌ WHATSAPP_SERVICE_URL não está configurado. Defina a URL do serviço WhatsApp (ex.: https://seuservico.railway.app)."
  exit 2
fi

# 2) (Opcional) Mostrar se há token/sessão (sem vazar token)
if [ -n "${WHATSAPP_API_TOKEN:-}" ]; then
  echo "🔐 WHATSAPP_API_TOKEN definido"
fi
if [ -n "${WHATSAPP_SESSION_ID:-}" ]; then
  echo "🪪 WHATSAPP_SESSION_ID=${WHATSAPP_SESSION_ID}"
fi

# 3) (Opcional) Ping leve no /health (não bloqueante)
WURL="${WHATSAPP_SERVICE_URL%/}"
if command -v curl >/dev/null 2>&1; then
  echo "🩺 Verificando WhatsApp Service em ${WURL}/health (best-effort)…"
  if [ -n "${WHATSAPP_API_TOKEN:-}" ]; then
    curl -fsS --max-time 5 -H "x-api-token: ${WHATSAPP_API_TOKEN}" "${WURL}/health" || true
  else
    curl -fsS --max-time 5 "${WURL}/health" || true
  fi
else
  echo "ℹ️ curl não disponível; pulando verificação HTTP."
fi

echo "🤖 Iniciando Telegram bot…"
# Use exec para tornar o bot o PID 1 no container
exec python main.py
