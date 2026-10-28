'use strict';
// Response-vector consumer with an explicit Brotli decoder.
//
// GET /proxy?url=<http url>: the app fetches the attacker URL over HTTP,
// pipes the raw response body into zlib.createBrotliDecompress() and streams
// the decoded bytes back to the caller.  This is the same response-vector
// shape as fetch-consumer.js, but decoding is explicit (default flush
// operation) instead of undici's automatic Content-Encoding handling.
const http = require('http');
const zlib = require('zlib');

const port = Number(process.env.PORT || 3000);

http.createServer((req, res) => {
  const url = new URL(req.url, 'http://x');
  if (url.pathname !== '/proxy') {
    res.writeHead(200, {'content-type': 'text/plain'});
    return res.end('ok');
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
  process.stdout.write('fetch-zlib on ' + port + ' node ' + process.version + '\n');
});
