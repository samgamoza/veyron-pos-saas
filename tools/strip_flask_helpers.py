from pathlib import Path

p = Path(__file__).resolve().parents[1] / "app/core/flask_app.py"
lines = p.read_text(encoding="utf-8").splitlines()


def find(pred):
    for i, L in enumerate(lines):
        if pred(L):
            return i
    raise RuntimeError("marker not found")


i_sql = find(lambda L: L.strip() == "def sql_now() -> str:")
i_init = find(lambda L: L.strip() == "def init_db() -> None:")
i_gpi = find(lambda L: L.strip().startswith("def get_product_image_url"))
i_health = find(lambda L: L.strip().startswith('@app.get("/healthz")'))

new_lines = lines[:i_sql] + lines[i_init:i_gpi] + lines[i_health:]

out: list[str] = []
inserted = False
for L in new_lines:
    out.append(L)
    if L.strip().startswith("DBIntegrityError =") and not inserted:
        out.append("")
        out.append("    from app.modules.web import queries as web_queries")
        inserted = True

if not inserted:
    raise SystemExit("no DBIntegrityError insert point")

p.write_text("\n".join(out) + "\n", encoding="utf-8")
print("ok", len(lines), "->", len(out))
