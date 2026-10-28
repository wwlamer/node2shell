#!/bin/sh
set -e
cd "$(dirname "$0")"
mkdir -p certs
if [ ! -s certs/cert.pem ] || [ ! -s certs/key.pem ]; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
    -keyout certs/key.pem -out certs/cert.pem \
    -subj "/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
fi
docker run --rm -v "$PWD":/app -w /app node:26.12.0-bookworm-slim npm install --no-audit --no-fund
docker compose up -d
echo "decode-vuln     http://127.0.0.1:19800/decode"
echo "decode-patched  http://127.0.0.1:19801/decode"
echo "fetch-vuln      http://127.0.0.1:19802/proxy"
echo "https-vuln      https://127.0.0.1:19810/decode (самоподписанный, нужен -k)"
echo "https-patched   https://127.0.0.1:19811/decode (самоподписанный, нужен -k)"
