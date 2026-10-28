'use strict';
const express = require('express');
const zlib = require('zlib');

const port = Number(process.env.PORT || 3000);
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

app.listen(port, '0.0.0.0', () => {
  process.stdout.write('express-stream on ' + port + ' node ' + process.version + '\n');
});
