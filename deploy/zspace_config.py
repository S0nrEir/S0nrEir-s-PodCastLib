#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""极空间 NAS 本地配置加载器。

读取 deploy/config.local.json（已被 .gitignore 排除，不会入库）。
同名环境变量优先于配置文件，方便临时覆盖。

复制 deploy/config.example.json 为 deploy/config.local.json，
再在本地填写 NAS 地址、账号、密码和个人空间共享名。
只提交示例文件，不要把真实连接信息写回源码或文档。
"""
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_HERE, "config.local.json")

DEFAULTS = {
    "nas_host": "",
    "site_url": "",
    "ssh_user": "",
    "ssh_password": "",
    "smb_share": "",
}

# 环境变量名 -> 配置键
ENV_MAP = {
    "SSH_HOST": "nas_host",
    "SSH_USER": "ssh_user",
    "SSH_PASS": "ssh_password",
    "SMB_SHARE": "smb_share",
    "SITE_URL": "site_url",
}


def load_config(allow_missing=False):
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
    elif not allow_missing:
        raise FileNotFoundError(
            f"缺少配置文件 {CONFIG_PATH}，请复制 deploy/config.example.json 后填写"
        )
    for env, key in ENV_MAP.items():
        if os.environ.get(env):
            cfg[key] = os.environ[env]
    return cfg


if __name__ == "__main__":
    c = load_config()
    masked = {k: ("***" if k == "ssh_password" and v else v) for k, v in c.items()}
    print(json.dumps(masked, ensure_ascii=False, indent=2))
