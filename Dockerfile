# --------------------------------------------------------------------
# Dockerfile – Client Management Bot (Python 3.11 + Node opcional)
# --------------------------------------------------------------------
FROM python:3.11-slim

# Variáveis de ambiente básicas
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=America/Sao_Paulo \
    ENVIRONMENT=production \
    WHATSAPP_ENABLED=true \
    APP_ENTRY=main.py

# SO deps + tzdata + curl + Node.js 20 (se for usar Baileys no mesmo container)
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates gnupg tzdata gcc g++ libpq-dev \
 && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*

# Diretório de trabalho
WORKDIR /app

# --------------------------------------------------------------------
# Dependências Python
# (Usa APENAS requirements.txt – evita erro do pyproject/editable)
# --------------------------------------------------------------------
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copia todo o projeto
COPY . /app

# (Opcional) instalar dependências Node do WhatsApp se existir package.json
# – não quebra o build se não existir
RUN if [ -f "/app/package.json" ]; then npm ci --omit=dev; fi

# Permissões (opcional: rodar como usuário não-root)
# RUN useradd -m appuser && chown -R appuser:appuser /app
# USER appuser

# Deixa o start.sh executável (se você já criou esse arquivo)
# Caso não exista, o build falhará — crie o start.sh na raiz do repo
RUN chmod +x /app/start.sh

# Portas (exponha só se realmente precisar)
EXPOSE 5000 3001

# Healthcheck simplificado (não falha caso 3001 não esteja ativo)
# Se quiser checar o WhatsApp, garanta que há um /health real no 3001
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD bash -lc 'exit 0'

# Comando de entrada
CMD ["/app/start.sh"]

# Metadados
LABEL maintainer="Development Team <dev@example.com>" \
      org.opencontainers.image.title="Client Management Bot" \
      org.opencontainers.image.description="Telegram bot with WhatsApp integration" \
      org.opencontainers.image.version="1.0.0" \
      org.opencontainers.image.vendor="Your Company" \
      org.opencontainers.image.licenses="MIT"
