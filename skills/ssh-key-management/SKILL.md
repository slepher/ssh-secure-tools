---
name: ssh-key-management
description: "创建 Ed25519 或 macOS Secure Enclave SSH 密钥，为普通私钥添加口令，归档和轮换密钥，并按授权注册远端公钥。"
---

与 [connection-operation-review](../connection-operation-review/SKILL.md) 一起安装。涉及远端连接和授权变更时先应用它。纯本地创建按用户指定的类型、路径、注释和口令需求执行。

使用 [create-ssh-key.py](scripts/create-ssh-key.py)。普通密钥默认无口令，`--prompt-passphrase` 让 `ssh-keygen` 在用户终端读取口令。原生 Secure Enclave 使用 `--type secure-enclave --label LABEL`，创建非导出、受生物识别保护的 P-256 CTK identity，并保存 `sk-ecdsa-sha2-nistp256@openssh.com` 句柄。目标平台不支持 `sk-*` 密钥类型时，使用 `--type secure-enclave-ecdsa --label LABEL --pkcs11-module PATH`，同样创建受生物识别保护的 CTK identity，并通过 PKCS#11 模块仅导出标准 `ecdsa-sha2-nistp256` 公钥文件（无本地私钥文件）。`--comment` 指定公钥末尾注释，密钥类型名称固定。

已有密钥文件只有显式指定空的 `--archive-dir` 才能替换。按用户偏好选择有语义的目录名，例如 `legacy-ed25519`，避免自动添加 `backup` 或日期。现有密钥文件一起归档，目录权限 `700`、私钥 `600`。脚本先验证并完成旧文件归档，再创建新密钥并发布新文件，避免归档失败留下孤儿 CTK identity。失败后先检查现状再重试，尤其是可能已创建的 CTK identity。

为现有普通私钥加口令使用 `ssh-keygen -p -f PRIVATE_KEY`，由用户在自己的终端输入。公钥保持同一身份。多个私钥副本分别有自己的文件口令，明确当前使用文件与归档文件；修改文件口令不会清除 SSH agent 已加载的副本。

远端注册先核对目标用户和新旧公钥指纹。保存可恢复的 `authorized_keys` 原内容，加入新公钥，用新身份单独验证登录，再按授权移除对应旧公钥。保留其他用途的公钥。验证时使用显式身份，禁用 agent、其他身份和连接复用，避免把其他凭据的成功当作新密钥成功。

普通文件密钥可复制和归档。Mac 原生路径不支持把已有 Ed25519 私钥导入 Secure Enclave。硬件签名受本机交互和执行环境限制，遇到沙箱阻止访问时报告具体失败并使用已授权的执行路径。
