import json, os, glob, subprocess
out = []
root = os.path.join(os.environ["LOCALAPPDATA"], "NovelDB")
out.append(root)
for dp, dn, fn in os.walk(root):
    if dp.count(os.sep) - root.count(os.sep) > 1: continue
    out.append(dp + " :: " + ", ".join(fn[:15]))
for f in glob.glob(os.path.join(root, "*.json")):
    try: d = json.load(open(f, encoding="utf-8"))
    except Exception as e: out.append(f"{f} {e}"); continue
    def walk(x, p=""):
        if isinstance(x, dict):
            for k, v in x.items(): walk(v, p + "." + k)
        elif any(s in p.lower() for s in ("dir", "folder", "theme", "accent", "library", "output")) and "secret" not in p.lower():
            out.append(f"{os.path.basename(f)} {p} {repr(x)[:150]}")
    walk(d)
out.append(subprocess.run("tasklist /v /fi \"imagename eq python*\"", shell=True, capture_output=True, text=True).stdout[-3000:])
open(os.path.join(os.path.dirname(__file__), "probe.txt"), "w", encoding="utf-8").write("\n".join(out))
