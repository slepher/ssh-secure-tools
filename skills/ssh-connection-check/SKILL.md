---
name: ssh-connection-check
description: "诊断 SSH 别名、客户端和跳板机版本、实际认证身份，以及 Secure Enclave 和 Touch ID 是否参与登录。"
---

与 [connection-operation-review](../connection-operation-review/SKILL.md) 一起安装，远端检查前先应用它。本地 `ssh -G ALIAS` 用于确认实际主机、用户、端口和身份，`ssh -V` 查询客户端版本。

没有远端 shell 的跳板机可读取握手日志。无认证示例为 `ssh -v -N -o BatchMode=yes -o PreferredAuthentications=none TARGET`，添加连接超时或有限运行时。按目标类别先准备和展示一个只完成该检查的文件。

区分 `Remote protocol version 2.0` 和 `remote software version`。后者可能只是 `Go` 或管理员修改的标识，描述的是当前 SSH 入口，不能推断后端服务器版本。`ssh -Q` 只查询客户端能力。

`sk-ecdsa-sha2-nistp256@openssh.com` 的 OpenSSH 协议支持从 8.2 开始，Mac 原生使用还依赖 CTK 和 Apple provider；标准 `ecdsa-sha2-nistp256` 的 Secure Enclave 路径则通过 `keychain-pkcs11` 与独立 `ssh-agent` 完成签名。分别检查 `IdentityFile`、`SecurityKeyProvider`、`PKCS11Provider` 与 `IdentityAgent` 的实际配置。

验证指定密钥时区分两种路径：
- 普通文件密钥与原生 `sk-ecdsa`：使用显式身份、`IdentitiesOnly=yes`、`IdentityAgent=none`、`ControlPath=none` 及仅公钥认证。
- `secure-ecdsa`（`keychain-pkcs11`）：通过 [ssh-connect](../connection-operation-review/scripts/ssh-connect) 的 `--secure-ecdsa-dir DIR`（或 `--pkcs11-module` / `--pkcs11-pubkey`）自动拉起独立 `ssh-agent`、过滤无关 CTK 身份并注入 `IdentitiesOnly=yes`、`ForwardAgent=no`、`ControlPath=none` 与临时 `IdentityAgent`，不要额外传 `IdentityAgent=none`。

仅在已授权目标执行无业务副作用的验证命令。日志确认目标身份被接受且新认证成功后才能报告该密钥可用。

`ssh-add` 加载成功不能证明后续程序使用了该密钥。Touch ID 需要在实际签名时验证。用户要求检查拒绝行为时，让用户取消本机认证，核对连接失败及后续操作未执行。

实际登录与远端 Bash 使用 [ssh-connect](../connection-operation-review/scripts/ssh-connect)，参数沿用 ssh。
