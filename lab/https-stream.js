'use strict';
const fs = require('fs');
const https = require('https');
const path = require('path');
const express = require('express');
const zlib = require('zlib');

const port = Number(process.env.PORT || 3443);
const certPath = process.env.TLS_CERT || path.join(__dirname, 'certs', 'cert.pem');
const keyPath = process.env.TLS_KEY || path.join(__dirname, 'certs', 'key.pem');
const held = [];
const app = express();

app.post('/decode', (request, response) => {
  const decoder = zlib.createBrotliDecompress();
  held.push(decoder);
  response.writeHead(200, {'content-type': 'application/octet-stream'});
  request.on('data', (chunk) => {
    if (!decoder.destroyed) {
      decoder.write(chunk);
    }
  });
  request.on('error', () => {});
  request.on('end', () => {});
  decoder.on('data', (chunk) => {
    response.write(chunk);
  });
  decoder.on('error', () => {});
});

app.get('/', (request, response) => {
  response.type('text/plain').send('ok');
});

https.createServer({
  cert: fs.readFileSync(certPath),
  key: fs.readFileSync(keyPath),
}, app).listen(port, '0.0.0.0', () => {
  process.stdout.write('https-stream on ' + port + ' node ' + process.version + '\n');
});
