import urllib.request

paths = ["/debug/routes", "/superadmin/login"]
for p in paths:
    url = f"http://127.0.0.1:5000{p}"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            print(p, 'OK', r.status)
            print(r.read(200).decode('utf-8', 'replace'))
    except Exception as e:
        print(p, 'ERROR', type(e).__name__, e)
