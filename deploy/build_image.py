#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""无 Docker 环境手工构建 docker-archive 镜像 tar。

流程:
  1. 从国内可用的 registry 镜像源拉取 library/python:3.12-alpine (linux/amd64)
  2. 在其上叠加应用层 (server.py + static)
  3. 组装 docker load 兼容的归档 tar (manifest.json + config + layers)
输出: build/podcast-topics.tar
"""
import gzip
import hashlib
import io
import json
import os
import shutil
import ssl
import sys
import tarfile
import time
import urllib.error
import urllib.request

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "build")
BLOB_DIR = os.path.join(OUT_DIR, "blobs")
APP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app")
OUT_TAR = os.path.join(OUT_DIR, "podcast-topics.tar")

REPO = "library/python"
TAG = "3.12-alpine"
ARCH = os.environ.get("IMAGE_ARCH", "arm64")  # 极空间 Z2Pro = RK3568 (linux/arm64)

MIRRORS = [
    "https://docker.m.daocloud.io",
    "https://dockerproxy.net",
    "https://docker.1ms.run",
    "https://hub.rat.dev",
    "https://docker.rainbond.cc",
    "https://registry-1.docker.io",
]

ACCEPT_MANIFEST = ", ".join([
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
])


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def http_get(url, headers=None, timeout=60):
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read()


class Registry:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.token = None

    def _auth(self, www_auth):
        # Bearer realm="https://auth.docker.io/token",service="registry.docker.io",scope="..."
        _, _, rest = www_auth.partition(" ")  # 剥掉 "Bearer" 前缀
        parts = {}
        for kv in rest.split(","):
            k, _, v = kv.partition("=")
            parts[k.strip()] = v.strip().strip('"')
        realm = parts.get("realm")
        if not realm:
            return
        qs = f"?service={parts.get('service', '')}&scope=repository:{REPO}:pull"
        data = json.loads(http_get(realm + qs, timeout=30))
        self.token = data.get("token") or data.get("access_token")

    def _headers(self, extra=None):
        h = {"User-Agent": "podcast-image-builder/1.0"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        if extra:
            h.update(extra)
        return h

    def get(self, path, extra_headers=None, retries=3):
        url = self.base + path
        last_err = None
        for _ in range(retries):
            try:
                req = urllib.request.Request(url, headers=self._headers(extra_headers))
                ctx = ssl.create_default_context()
                with urllib.request.urlopen(req, timeout=90, context=ctx) as r:
                    return r.read()
            except urllib.error.HTTPError as e:
                if e.code == 401 and e.headers.get("WWW-Authenticate"):
                    self._auth(e.headers.get("WWW-Authenticate"))
                    continue
                last_err = e
            except Exception as e:
                last_err = e
                time.sleep(2)
        raise RuntimeError(f"GET {url} 失败: {last_err}")


def try_mirror(base):
    reg = Registry(base)
    print(f"  尝试 {base} ...", end=" ", flush=True)
    mlist = reg.get(f"/v2/{REPO}/manifests/{TAG}", {"Accept": ACCEPT_MANIFEST})
    mi = json.loads(mlist)
    mtype = mi.get("mediaType", "")
    if mtype.endswith("manifest.list.v2+json") or mtype.endswith("index.v1+json") or "manifests" in mi:
        target = None
        for m in mi.get("manifests", []):
            p = m.get("platform", {})
            if p.get("os") == "linux" and p.get("architecture") == ARCH:
                target = m["digest"]
                break
        if not target:
            raise RuntimeError(f"清单里没有 linux/{ARCH}")
        mbytes = reg.get(f"/v2/{REPO}/manifests/{target}", {"Accept": ACCEPT_MANIFEST})
        if sha256_bytes(mbytes) != target.split(":")[1]:
            raise RuntimeError("manifest 摘要校验失败")
        mi = json.loads(mbytes)
    if "config" not in mi or "layers" not in mi:
        raise RuntimeError("不是单平台 manifest")
    print("OK")
    return reg, mi


def download_all():
    reg = manifest = None
    for base in MIRRORS:
        try:
            reg, manifest = try_mirror(base)
            break
        except Exception as e:
            print(f"失败 ({str(e)[:80]})")
    if not reg:
        print("!! 所有镜像源都不可用")
        sys.exit(1)
    print(f"使用镜像源: {reg.base}")

    os.makedirs(BLOB_DIR, exist_ok=True)

    cfg_digest = manifest["config"]["digest"]
    cfg_path = os.path.join(BLOB_DIR, cfg_digest.replace(":", "-") + ".json")
    if not os.path.exists(cfg_path):
        print("下载 config ...", flush=True)
        b = reg.get(f"/v2/{REPO}/blobs/{cfg_digest}")
        if sha256_bytes(b) != cfg_digest.split(":")[1]:
            raise RuntimeError("config 摘要校验失败")
        open(cfg_path, "wb").write(b)

    layer_tars = []
    for i, layer in enumerate(manifest["layers"]):
        d = layer["digest"]
        tar_path = os.path.join(BLOB_DIR, d.replace(":", "-") + ".tar")
        if os.path.exists(tar_path):
            print(f"layer {i}: 已缓存")
        else:
            print(f"layer {i}: 下载 {d[:20]}... ", end="", flush=True)
            b = reg.get(f"/v2/{REPO}/blobs/{d}")
            if sha256_bytes(b) != d.split(":")[1]:
                raise RuntimeError(f"layer {i} 摘要校验失败")
            # 解压 gzip → 未压缩 tar（diff_ids 需要未压缩摘要）
            raw = gzip.decompress(b) if layer.get("mediaType", "").endswith("gzip") or b[:2] == b"\x1f\x8b" else b
            open(tar_path, "wb").write(raw)
            print(f"{len(raw)//1024//1024}MB")
        layer_tars.append(tar_path)
    return cfg_path, layer_tars


def build_app_layer(out_path):
    """应用层: /app/server.py + /app/static/*"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        def add_dir(name, mtime):
            ti = tarfile.TarInfo(name)
            ti.type = tarfile.DIRTYPE
            ti.mode = 0o755
            ti.mtime = mtime
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = "root"
            tf.addfile(ti)
        def add_file(name, path, mtime):
            data = open(path, "rb").read()
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            ti.mode = 0o644
            ti.mtime = mtime
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = "root"
            tf.addfile(ti, io.BytesIO(data))
        mt = int(time.time())
        add_dir("app", mt)
        add_dir("app/static", mt)
        add_file("app/server.py", os.path.join(APP_DIR, "server.py"), mt)
        for fn in os.listdir(os.path.join(APP_DIR, "static")):
            add_file(f"app/static/{fn}", os.path.join(APP_DIR, "static", fn), mt)
    raw = buf.getvalue()
    open(out_path, "wb").write(raw)
    return out_path


def assemble(cfg_path, layer_tars, app_layer):
    base_cfg = json.loads(open(cfg_path, "rb").read().decode("utf-8"))
    app_layer_bytes = open(app_layer, "rb").read()
    app_diff = sha256_bytes(app_layer_bytes)

    cfg = dict(base_cfg)
    cfg["created"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    cfg["architecture"] = ARCH
    cfg["os"] = "linux"
    inner = dict(base_cfg.get("config", {}))
    inner["Cmd"] = ["python", "-u", "server.py"]
    inner["WorkingDir"] = "/app"
    env = list(inner.get("Env") or [])
    if not any(e.startswith("DATA_DIR=") for e in env):
        env.append("DATA_DIR=/data")
    inner["Env"] = env
    inner["ExposedPorts"] = {"8390/tcp": {}}
    labels = dict(inner.get("Labels") or {})
    labels.update({"org.opencontainers.image.title": "podcast-topic-library"})
    inner["Labels"] = labels
    cfg["config"] = inner

    diff_ids = list(base_cfg["rootfs"]["diff_ids"]) + [f"sha256:{app_diff}"]
    cfg["rootfs"] = dict(base_cfg["rootfs"], diff_ids=diff_ids)
    hist = list(base_cfg.get("history", [])) + [
        {"created": cfg["created"], "created_by": "COPY app /app"}
    ]
    cfg["history"] = hist

    cfg_bytes = json.dumps(cfg, separators=(",", ":")).encode("utf-8")
    cfg_sha = sha256_bytes(cfg_bytes)

    if os.path.exists(OUT_TAR):
        os.remove(OUT_TAR)
    print("组装归档 ...", flush=True)
    layers_in_archive = []
    with tarfile.open(OUT_TAR, "w", format=tarfile.USTAR_FORMAT) as tf:
        def put(name, path):
            ti = tarfile.TarInfo(name)
            ti.size = os.path.getsize(path)
            ti.mode = 0o644
            tf.addfile(ti, open(path, "rb"))
        for i, p in enumerate(layer_tars + [app_layer]):
            arc = f"l{i}/layer.tar"
            put(arc, p)
            layers_in_archive.append(arc)
        ti = tarfile.TarInfo(f"{cfg_sha}.json")
        ti.size = len(cfg_bytes)
        tf.addfile(ti, io.BytesIO(cfg_bytes))
        manifest = [{
            "Config": f"{cfg_sha}.json",
            "RepoTags": ["podcast-topics:latest"],
            "Layers": layers_in_archive,
        }]
        mb = json.dumps(manifest).encode("utf-8")
        ti = tarfile.TarInfo("manifest.json")
        ti.size = len(mb)
        tf.addfile(ti, io.BytesIO(mb))
    size = os.path.getsize(OUT_TAR) / 1024 / 1024
    print(f"完成: {OUT_TAR} ({size:.1f}MB)")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    if os.path.exists(BLOB_DIR):
        shutil.rmtree(BLOB_DIR)
    cfg_path, layer_tars = download_all()
    app_layer = os.path.join(OUT_DIR, "app-layer.tar")
    build_app_layer(app_layer)
    assemble(cfg_path, layer_tars, app_layer)


if __name__ == "__main__":
    main()
