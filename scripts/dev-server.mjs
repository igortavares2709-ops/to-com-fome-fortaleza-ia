import http from "node:http";
import worker from "../dist/server/index.js";

const port = Number(process.env.PORT || 8787);
const server = http.createServer(async (request, response) => {
  const origin = `http://${request.headers.host || `127.0.0.1:${port}`}`;
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  const body = chunks.length ? Buffer.concat(chunks) : undefined;
  const webRequest = new Request(new URL(request.url || "/", origin), {
    method: request.method,
    headers: request.headers,
    body: ["GET", "HEAD"].includes(request.method || "GET") ? undefined : body,
  });
  const webResponse = await worker.fetch(webRequest, process.env);
  response.writeHead(webResponse.status, Object.fromEntries(webResponse.headers));
  response.end(Buffer.from(await webResponse.arrayBuffer()));
});
server.listen(port, "127.0.0.1", () => console.log(`Tô com Fome Fortaleza IA: http://127.0.0.1:${port}`));
