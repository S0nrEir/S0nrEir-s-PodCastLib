#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验 build/podcast-topics.tar 的结构（模拟 docker load 的读取路径）"""
import hashlib
import io
import json
import os
import tarfile

TAR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "build", "podcast-topics.tar")

ok = True


def check(name, cond, detail=""):
    global ok
    print(("ok   - " if cond else "FAIL - ") + name + (f" {detail}" if detail else ""))
    if not cond:
        ok = False


tf = tarfile.open(TAR, "r")
names = tf.getnames()

# 1. manifest.json 存在且可解析
check("manifest.json 存在", "manifest.json" in names)
manifest = json.loads(tf.extractfile("manifest.json").read().decode("utf-8"))
check("manifest 只有一个条目", len(manifest) == 1)
entry = manifest[0]
check("RepoTags = podcast-topics:latest", entry.get("RepoTags") == ["podcast-topics:latest"], str(entry.get("RepoTags")))

# 2. config 存在且可解析
cfg_name = entry["Config"]
check(f"config {cfg_name[:16]}... 存在", cfg_name in names)
cfg = json.loads(tf.extractfile(cfg_name).read().decode("utf-8"))

# 3. 命令与工作目录
c = cfg.get("config", {})
check("Cmd 启动 server.py", c.get("Cmd") == ["python", "-u", "server.py"], str(c.get("Cmd")))
check("WorkingDir=/app", c.get("WorkingDir") == "/app")
check("Env 含 DATA_DIR=/data", any(e == "DATA_DIR=/data" for e in c.get("Env", [])))
check("ExposedPorts 8390", "8390/tcp" in (c.get("ExposedPorts") or {}))
arch = os.environ.get("IMAGE_ARCH", "arm64")
check(f"架构 {arch}/linux", cfg.get("architecture") == arch and cfg.get("os") == "linux")

# 4. 每层 layer.tar 存在、是合法 tar、diff_id 匹配
layers = entry["Layers"]
diff_ids = cfg["rootfs"]["diff_ids"]
check("层数与 diff_ids 一致", len(layers) == len(diff_ids), f"{len(layers)} 层")
for i, (lname, did) in enumerate(zip(layers, diff_ids)):
    check(f"layer{i} 文件存在", lname in names, lname)
    raw = tf.extractfile(lname).read()
    actual = "sha256:" + hashlib.sha256(raw).hexdigest()
    check(f"layer{i} diff_id 匹配", actual == did, f"{actual[:20]} vs {did[:20]}")
    try:
        lt = tarfile.open(fileobj=io.BytesIO(raw), mode="r")
        n = len(lt.getnames())
        lt.close()
        check(f"layer{i} 是合法 tar（{n} 个条目）", n > 0)
    except Exception as e:
        check(f"layer{i} 是合法 tar", False, str(e))

# 5. 最后一层（应用层）内容
last = tf.extractfile(layers[-1]).read()
lt = tarfile.open(fileobj=io.BytesIO(last), mode="r")
app_names = lt.getnames()
check("应用层含 app/server.py", "app/server.py" in app_names, str(app_names))
check("应用层含 app/static/index.html", "app/static/index.html" in app_names)
server_py = lt.extractfile("app/server.py").read()
check("server.py 内容非空且完整", b"serve_forever" in server_py and len(server_py) > 10000, f"{len(server_py)}B")

print()
print("ALL VALID" if ok else "VALIDATION FAILED")
raise SystemExit(0 if ok else 1)
