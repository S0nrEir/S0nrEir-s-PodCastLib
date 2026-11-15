#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通过 SMB 把部署产物上传到极空间个人空间（连接信息读 config.local.json）"""
import time

from impacket.smbconnection import SMBConnection

from zspace_config import load_config

_cfg = load_config()
HOST = _cfg["nas_host"]
USER = _cfg["ssh_user"]
PASS = _cfg["ssh_password"]
SHARE = _cfg["smb_share"]
ROOT = os.path.dirname(os.path.abspath(__file__))
WS = os.path.join(ROOT, "..")

c = SMBConnection(HOST, HOST, sess_port=445)
c.login(USER, PASS)


def mkdirs(path):
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    cur = ""
    for p in parts:
        cur = f"{cur}/{p}"
        try:
            c.createDirectory(SHARE, cur)
        except Exception:
            pass  # 已存在


def put(local, remote, show_progress=False):
    total = os.path.getsize(local)
    t0 = time.time()
    tmp = remote + ".part"
    with open(local, "rb") as f:
        c.putFile(SHARE, tmp, f.read)
    try:
        c.deleteFile(SHARE, remote)
    except Exception:
        pass
    c.rename(SHARE, tmp, remote)
    dt = time.time() - t0
    speed = total / max(dt, 0.1) / 1024 / 1024
    print(f"上传完成: {remote} ({total/1024/1024:.1f}MB, {dt:.0f}s, {speed:.1f}MB/s)")


print("创建目录 ...")
mkdirs("播客选题库/app/static")
mkdirs("播客选题库/data")

print("上传镜像 tar ...")
put(os.path.join(WS, "build", "podcast-topics.tar"), "/播客选题库/podcast-topics.tar", show_progress=True)

print("上传源码备份 ...")
put(os.path.join(WS, "app", "server.py"), "/播客选题库/app/server.py")
for fn in os.listdir(os.path.join(WS, "app", "static")):
    put(os.path.join(WS, "app", "static", fn), f"/播客选题库/app/static/{fn}")

print("核对远端文件 ...")
for p in ["/播客选题库/podcast-topics.tar", "/播客选题库/app/server.py",
          "/播客选题库/app/static/index.html", "/播客选题库/app/static/style.css",
          "/播客选题库/app/static/app.js"]:
    try:
        info = c.listPath(SHARE, p)[0]
        print(f"  {p} {info.get_filesize()/1024:.0f}KB")
    except Exception as e:
        print(f"  {p} 缺失! {e}")
c.logoff()
print("全部完成")
