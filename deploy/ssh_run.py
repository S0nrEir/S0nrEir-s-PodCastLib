#!/usr/bin/env python3
"""极空间 NAS SSH 执行助手 - 用法:
  python ssh_run.py "命令"            # 执行命令
  python ssh_run.py --put 本地 远程    # 上传文件

连接信息读 deploy/config.local.json（环境变量可覆盖）。
"""
import sys

import paramiko

from zspace_config import load_config

_cfg = load_config()
HOST = _cfg["nas_host"]
USER = _cfg["ssh_user"]
PASS = _cfg["ssh_password"]


def client():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, port=22, username=USER, password=PASS,
              timeout=20, banner_timeout=20, auth_timeout=20,
              allow_agent=False, look_for_keys=False)
    return c


def main():
    c = client()
    try:
        if sys.argv[1] == "--put":
            local, remote = sys.argv[2], sys.argv[3]
            sftp = c.open_sftp()
            sftp.put(local, remote)
            sftp.close()
            print(f"uploaded {local} -> {remote}")
        else:
            cmd = sys.argv[1]
            _, out, err = c.exec_command(cmd, timeout=600)
            o = out.read().decode("utf-8", "replace")
            e = err.read().decode("utf-8", "replace")
            rc = out.channel.recv_exit_status()
            if o:
                print(o, end="")
            if e:
                print("[stderr]", e, end="", file=sys.stderr)
            sys.exit(rc)
    finally:
        c.close()


if __name__ == "__main__":
    main()
