FROM python:3.11-slim

WORKDIR /app
EXPOSE 8000

# ffmpeg is required by pydub (finalize_audio_podcast) to decode/encode/concat MP3 segments
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Install supervisor to run multiple processes
RUN pip install supervisor

# Create supervisor log directories
RUN mkdir -p /var/log/supervisor /var/run/supervisor

COPY docker/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
