'use strict';
// TLS-terminating TCP relay: the exploit speaks HTTPS to this listener; the
// decrypted byte stream is forwarded unchanged to the plain-HTTP vulnerable
// sink.  This models a normal edge TLS terminator in front of the app.
//
// Close handling matters for the exploit client: destroying the TLS socket on
// upstream close sends a bare FIN/RST without close_notify, which Python sees
// as "EOF occurred in violation of protocol" (ssl.SSLEOFError).  The relay
// therefore half-closes each side gracefully and only destroys as a fallback.
const fs = require('fs');
const net = require('net');
const path = require('path');
const tls = require('tls');

const port = Number(process.env.PORT || 3446);
const upstreamHost = process.env.UPSTREAM_HOST || '172.17.0.1';
const upstreamPort = Number(process.env.UPSTREAM_PORT || 19700);
const certPath = process.env.TLS_CERT || path.join(__dirname, 'certs', 'cert.pem');
const keyPath = process.env.TLS_KEY || path.join(__dirname, 'certs', 'key.pem');

function shutdown(socket) {
  if (socket.destroyed) return;
  socket.end();
  const timer = setTimeout(() => socket.destroy(), 1000);
  timer.unref();
  socket.once('close', () => clearTimeout(timer));
}

tls.createServer({
  cert: fs.readFileSync(certPath),
  key: fs.readFileSync(keyPath),
}, (client) => {
  const upstream = net.connect(upstreamPort, upstreamHost);
  client.pipe(upstream);
  upstream.pipe(client);
  client.on('error', () => {});
  upstream.on('error', () => {});
  client.on('close', () => upstream.destroy());
  upstream.on('close', () => shutdown(client));
}).listen(port, '0.0.0.0', () => {
  process.stdout.write('tls-tunnel on ' + port + ' -> ' + upstreamHost + ':' + upstreamPort + '\n');
});
