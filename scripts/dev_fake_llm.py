"""Tiny OpenAI-compatible server for trying the app WITHOUT an API key (development only).

It answers by quoting the first retrieved source, with a [1] citation, and supports streaming, so the real
streaming code path in the API is exercised. Usage:
    python scripts/dev_fake_llm.py            # listens on :9100
    HOSTED_BASE_URL=http://host.docker.internal:9100/v1  HOSTED_API_KEY=anything
"""
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def reply_for(messages: list[dict]) -> str:
    system, last = messages[0]["content"], messages[-1]["content"]
    if system.startswith("Rewrite the user's latest question"):
        return last.split("Latest question:", 1)[-1].strip()
    m = re.search(r'<source id="1"[^>]*>\n(.*?)\n</source>', last, re.S)
    if not m:
        return "I could not find this in your documents."
    return f"According to your documents: {' '.join(m.group(1).split())[:160]} [1]"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep the console quiet
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        text = reply_for(body["messages"])
        usage = {"prompt_tokens": 42, "completion_tokens": len(text.split())}
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for word in text.split(" "):
                chunk = {"choices": [{"delta": {"content": word + " "}}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.flush()
            self.wfile.write(f"data: {json.dumps({'choices': [], 'usage': usage})}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            payload = json.dumps({"choices": [{"message": {"content": text}}], "usage": usage}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9100
    print(f"fake LLM on :{port}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
