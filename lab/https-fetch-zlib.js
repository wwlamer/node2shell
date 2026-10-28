'use strict';
// HTTPS response-vector consumer: HTTPS endpoint GET /proxy?url= fetches an
// attacker URL over plain HTTP, decodes the body with an explicit Brotli
// decoder (default flush) and streams the decoded bytes over TLS.
const fs = require('fs');
const http = require('http');
const https = require('https');
const path = require('path');
const zlib = require('zlib');

const port = Number(process.env.PORT || 3445);
const certPath = process.env.TLS_CERT || path.join(__dirname, 'certs', 'cert.pem');
const keyPath = process.env.TLS_KEY || path.join(__dirname, 'certs', 'key.pem');

https.createServer({
  cert: fs.readFileSync(certPath),
  key: fs.readFileSync(keyPath),
}, (req, res) => {
  const url = new URL(req.url, 'https://x');
  if (url.pathname === '/health') {
    res.writeHead(200, {'content-type': 'application/json'});
    return res.end(JSON.stringify({ok: true, node: process.version}));
  }
  if (url.pathname !== '/proxy') {
    res.writeHead(404, {'content-type': 'text/plain'});
    return res.end('not found');
  }
  const target = url.searchParams.get('url');
  if (!target) {
    res.writeHead(400, {'content-type': 'text/plain'});
    return res.end('url is required');
  }
  http.get(target, (upstream) => {
    res.writeHead(200, {'content-type': 'application/octet-stream'});
    res.flushHeaders();
    const decoder = zlib.createBrotliDecompress();
    upstream.on('error', () => {});
    decoder.on('error', () => {});
    upstream.pipe(decoder).on('data', (chunk) => {
      try { res.write(chunk); } catch (error) {}
    }).on('end', () => {
      try { res.end(); } catch (error) {}
    });
  }).on('error', (error) => {
    res.writeHead(502, {'content-type': 'text/plain'});
    res.end('upstream error: ' + error.message);
  });
}).listen(port, '0.0.0.0', () => {
  process.stdout.write('https-fetch-zlib on ' + port + ' node ' + process.version + '\n');
});
