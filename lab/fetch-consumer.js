'use strict';
const http = require('http');

const port = Number(process.env.PORT || 3000);

http.createServer(async (req, res) => {
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
  try {
    const upstream = await fetch(target);
    res.writeHead(200, {'content-type': 'application/octet-stream'});
    for await (const chunk of upstream.body) {
      if (!res.write(chunk)) {
        await new Promise((resolve) => res.once('drain', resolve));
      }
    }
    res.end();
  } catch (error) {
    res.writeHead(502, {'content-type': 'text/plain'});
    res.end('upstream error: ' + error.message);
  }
}).listen(port, '0.0.0.0', () => {
  process.stdout.write('fetch-consumer on ' + port + ' node ' + process.version + '\n');
});
