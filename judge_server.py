from http.server import BaseHTTPRequestHandler, HTTPServer
import json
PORT = 8765
class H(BaseHTTPRequestHandler):
    def _verdict(self):
        ua = self.headers.get("User-Agent","")
        s = {"ua_is_browser": ("Mozilla" in ua and "Chrome" in ua),
             "has_sec_ch_ua": bool(self.headers.get("sec-ch-ua")),
             "has_accept_language": bool(self.headers.get("Accept-Language")),
             "ua_bot_like": any(b in ua.lower() for b in ["python","requests","curl","bot","headless","urllib"])}
        bot = s["ua_bot_like"] or (not s["ua_is_browser"]) or (not s["has_sec_ch_ua"])
        return ("bot" if bot else "human", s)
    def do_GET(self):
        v, sig = self._verdict()
        code = 200 if v=="human" else 403
        body = json.dumps({"verdict":v,"signals":sig}, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self,*a): pass
print("JUDGE up on "+str(PORT), flush=True)
HTTPServer(("127.0.0.1",PORT), H).serve_forever()
