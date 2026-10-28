'use strict';
// HTTPS request-vector sink: same semantics as lab/apps/sink on plain
// node:https (no framework), with the decoder held open and decoded bytes
// streamed back over TLS.
const fs = require('fs');
const https = require('https');
const path = require('path');
const zlib = require('zlib');

const port = Number(process.env.PORT || 3444);
const certPath = process.env.TLS_CERT || path.join(__dirname, 'certs', 'cert.pem');
const keyPath = process.env.TLS_KEY || path.join(__dirname, 'certs', 'key.pem');
const held = [];

https.createServer({
  cert: fs.readFileSync(certPath),
  key: fs.readFileSync(keyPath),
}, (request, response) => {
  if (request.url === '/health') {
    response.writeHead(200, {'content-type': 'application/json'});
    return response.end(JSON.stringify({ok: true, node: process.version, held: held.length}));
  }
  const decoder = zlib.createBrotliDecompress();
  held.push(decoder);
  response.writeHead(200, {'content-type': 'application/octet-stream'});
  request.on('data', (chunk) => {
    if (!decoder.destroyed) {
      try { decoder.write(chunk); } catch (error) {}
    }
  });
  request.on('error', () => {});
  request.on('end', () => {});
  decoder.on('data', (chunk) => {
    try { response.write(chunk); } catch (error) {}
  });
  decoder.on('error', () => {});
}).listen(port, '0.0.0.0', () => {
  process.stdout.write('https-sink on ' + port + ' node ' + process.version + '\n');
});
