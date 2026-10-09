# core/gpsd_data.py
import socket
import json

def get_data(host="127.0.0.1", port=2947, timeout=5.0):
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.send(b'?WATCH={"enable":true,"json":true};\n')
        s.settimeout(timeout)
        tpv = None
        sky = None
        buffer = b""
        while not (tpv and sky):
            chunk = s.recv(4096)
            if not chunk:
                break
            buffer += chunk
            lines = buffer.split(b"\n")
            buffer = lines[-1]
            for line in lines[:-1]:
                if not line.strip():
                    continue
                obj = json.loads(line.decode())
                if obj.get("class") == "TPV":
                    tpv = obj
                elif obj.get("class") == "SKY":
                    sky = obj
        s.close()
        return tpv, sky
    except Exception:
        return None, None
