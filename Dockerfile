# Imagem do pipeline MedStudy Automator para Cloud Run Jobs.
#
# Sem Playwright/Chromium de propósito: a doc do notebooklm-py diz que o navegador
# só é necessário UMA VEZ, no bootstrap do master-token (`notebooklm login`) - no
# dia a dia o pipeline autentica via master-token (headless) + curl_cffi (extra
# [impersonate], impersona fingerprint de navegador real - mitigação recomendada
# pela própria doc pro risco de bloqueio por "IP de datacenter"). A reautenticação,
# quando precisar, continua sendo feita no Windows local - nunca dentro do container
# (ver runbook de deploy).
FROM python:3.11-slim

# ffmpeg: usado por core/multimodal_processor.py pra recomprimir áudio antes do
# upload pro Gemini (reduz payload = menor chance de 503 em arquivos pesados).
# Opcional em tempo de execução (o código já cai de volta pro áudio original se
# o binário não existir), mas instalar aqui evita esse fallback sem necessidade.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir "notebooklm-py[headless,impersonate]>=0.8.1"

COPY . .
# .env não vai na imagem (fica de fora via .dockerignore) - credenciais reais
# chegam em runtime via Secret Manager (--set-secrets no deploy), nunca embutidas
# na imagem.

RUN chmod +x entrypoint.sh

# Cloud Run Jobs não expõe porta nem espera requisição HTTP - só roda o comando
# até terminar (ou falhar) e desliga.
ENTRYPOINT ["./entrypoint.sh"]
