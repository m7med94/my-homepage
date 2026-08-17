#!/usr/bin/env python3
"""
Lightweight Mock Server for XiaoZhi ESP32 Data Receiver
Uses standard Python library (no external dependencies required).
"""

import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

PORT = 8000

class DataReceiverHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == '/api/v1/device/data':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            
            try:
                data = json.loads(body.decode('utf-8'))
                print("\n" + "="*50)
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] RECEIVED FROM ESP32:")
                print(f"  Device ID : {data.get('device_id')}")
                print(f"  Category  : {data.get('category')}")
                print(f"  Data      : {data.get('data')}")
                print("="*50 + "\n")
                
                # Send 200 OK Response
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                response = json.dumps({"status": "success", "message": "Data received"}).encode('utf-8')
                self.wfile.write(response)
            except Exception as e:
                print(f"Error parsing request: {e}")
                self.send_response(400)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"status":"healthy"}')
        else:
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'XiaoZhi Mock Server is running!')

def run():
    server = HTTPServer(('0.0.0.0', PORT), DataReceiverHandler)
    print(f"[*] XiaoZhi Mock Server running on http://0.0.0.0:{PORT}")
    print(f"[*] Endpoint: POST http://<YOUR_PC_IP>:{PORT}/api/v1/device/data")
    print("[*] Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] Server stopped.")

if __name__ == '__main__':
    run()
