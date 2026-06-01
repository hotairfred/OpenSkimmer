#!/usr/bin/env python3
"""capture_cluster.py HOST PORT CALL OUTFILE
Self-reconnecting cluster/RBN tee for blacklist mining. Logs in with CALL,
prepends a UTC HH:MM:SS stamp to every line (mine_blacklist.py SPOT_RE needs
a leading timestamp), appends line-buffered, reconnects on any drop.
"""
import socket, sys, time, datetime
host, port, call, out = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
while True:
    try:
        s = socket.create_connection((host, port), timeout=30)
        s.sendall((call + "\r\n").encode())
        f = s.makefile("r", errors="replace")
        with open(out, "a", buffering=1) as o:
            for line in f:
                line = line.rstrip("\r\n")
                if not line:
                    continue
                ts = datetime.datetime.now(datetime.timezone.utc).strftime("%H:%M:%S")
                o.write(ts + " " + line + "\n")
    except Exception:
        pass
    time.sleep(5)
