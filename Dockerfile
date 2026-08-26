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

# Sem isso, o stdout do Python fica bufferizado em bloco (não bufferiza linha a
# linha como faz num terminal de verdade) - se o processo travar/crashar antes do
# buffer encher, a saída nunca chega ao Cloud Logging, mesmo que o Python já
# tenha logado alguma coisa internamente antes de morrer. Um dos jeitos mais
# comuns de um container "crashar sem deixar rastro nenhum".
ENV PYTHONUNBUFFERED=1

# python:3.11-slim não vem com locale UTF-8 configurado (fica em "C"/ASCII por
# padrão) - isso quebra qualquer texto com acento (ç, ã, é...) em algum ponto
# da stack de rede/arquivo que dependa do locale do sistema (bug real visto em
# produção: 'ascii' codec can't encode character 'ç' - derrubava a extração via
# Gemini pra qualquer aula com título ou nome de arquivo acentuado, ou seja,
# quase todas). PYTHONUTF8 força o Python em si a tratar tudo como UTF-8,
# independente do locale do sistema.
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV PYTHONUTF8=1

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
