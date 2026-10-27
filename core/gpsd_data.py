# core/gpsd_data.py
import socket
import json
import time


def get_data(host="127.0.0.1", port=2947, timeout=5.0, extra_listen_sec=3.0):
    """Читает TPV и SKY от gpsd.

    Особенности gpsd:
      - SKY приходит в двух вариантах:
        * с "satellites" (список спутников),
        * без "satellites", но с DOP (gdop, hdop, ...).
        Нужен ПЕРВЫЙ — поэтому ждём SKY, у которого есть непустой "satellites".
      - TPV идёт отдельными сообщениями: mode, lat/lon, jam, antPwr...
        Поэтому все TPV сливаем в один dict (последнее значение побеждает).
      - jam/antPwr — не в каждом TPV, а периодически. После получения
        SKY+TPV читаем extra_listen_sec, чтобы поймать их.
    """
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.send(b'?WATCH={"enable":true,"json":true};\n')
        s.settimeout(0.5)

        tpv_merged = {}
        sky_with_sats = None
        sky_dop = None
        buffer = b""
        deadline = time.time() + timeout

        # ── Фаза 1: ждём SKY со спутниками и хотя бы один TPV ──
        while time.time() < deadline:
            try:
                chunk = s.recv(4096)
            except socket.timeout:
                continue
            if not chunk:
                break
            buffer += chunk
            lines = buffer.split(b"\n")
            buffer = lines[-1]
            for line in lines[:-1]:
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line.decode())
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue

                cls = obj.get("class")
                if cls == "TPV":
                    tpv_merged.update(obj)
                elif cls == "SKY":
                    sats = obj.get("satellites")
                    if isinstance(sats, list) and len(sats) > 0:
                        sky_with_sats = obj      # ← нужный SKY
                    else:
                        sky_dop = obj             # ← SKY только с DOP

            # Выходим, только когда есть и SKY со спутниками, и TPV
            if sky_with_sats is not None and tpv_merged:
                break

        # ── Фаза 2: добрать TPV (jam/antPwr приходят периодически) ──
        if sky_with_sats is not None and tpv_merged and extra_listen_sec > 0:
            tail_deadline = time.time() + extra_listen_sec
            while time.time() < tail_deadline:
                try:
                    chunk = s.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                buffer += chunk
                lines = buffer.split(b"\n")
                buffer = lines[-1]
                for line in lines[:-1]:
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line.decode())
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    cls = obj.get("class")
                    if cls == "TPV":
                        tpv_merged.update(obj)
                    elif cls == "SKY":
                        sats = obj.get("satellites")
                        if isinstance(sats, list) and len(sats) > 0:
                            sky_with_sats = obj
                        else:
                            sky_dop = obj

        s.close()

        # Возвращаем: TPV (merged), SKY со спутниками.
        # DOP-часть SKY (без satellites) приклеиваем к sky_with_sats,
        # чтобы hdop/pdop были доступны в analyze().
        if sky_with_sats is not None and sky_dop is not None:
            for k in ("gdop", "hdop", "pdop", "tdop", "xdop", "ydop", "vdop"):
                if k not in sky_with_sats and k in sky_dop:
                    sky_with_sats[k] = sky_dop[k]

        tpv_out = tpv_merged if tpv_merged else None
        return tpv_out, sky_with_sats
    except Exception:
        return None, None
