---
name: mysql-tunnel
description: "通过 mysql_tunnel 连接 MySQL、执行 SQL 或输出 TSV，使用系统 OpenSSH 隧道和钥匙串，并维护独立连接的清理。"
---

与 [connection-operation-review](../connection-operation-review/SKILL.md) 一起安装，先按实际 SSH 和数据库目标分类。读取元数据也属于连接操作。profile 统一在 `~/.my.cnf`（或 `MYSQL_TUNNEL_PROFILES_FILE` 指定路径）中通过 `[mysql_tunnel_<名称>]` 配置组定义，由 `my_print_defaults` 读取，可按需为使用 `secure-ecdsa` 的 profile 指定 `pkcs11-dir`（或 `pkcs11-module` + `pkcs11-pubkey`）。

在 Zsh 中加载本技能的 `scripts/mysql-tunnel.zsh`。该脚本会根据自身位置寻找凭据脚本。查询用 `mysql_tunnel PROFILE`，SQL 文件用 `mysql_tunnel PROFILE --file=FILE`，TSV 用紧跟 profile 的 `--tsv`。需要文件审阅时，创建一个带必要注释的 SQL 文件，展示完整 SQL 和带注释的 Bash 调用，再直接执行同一文件。注释说明数据库、表、条件和操作目的。

写入保持在用户授权范围内，适合时使用事务，测试记录应可明确识别，核对受影响行数。只查询任务需要的元数据。新增诊断或验证先按环境规则处理。交互连接仅建立会话时，说明尚待执行具体 SQL，后续语句继续遵循分类规则。

系统 OpenSSH 完成 SSH 认证和隧道建立，客户端连接独立的 Unix socket。`mysql_tunnel` 默认优先使用标准 `mysql` 客户端（无需安装 `mysqlsh`；`--tsv` 自动映射为 `--batch`，`--file=FILE` 自动重定向输入），仅在未安装 `mysql`、显式设置 `MYSQL_TUNNEL_CLIENT=mysqlsh` 或使用 `--dump`/`-d`/`--js`/`--py` 时才使用 `mysqlsh`。每次调用使用独立 master 和临时凭据，数据库密码由 [mysql-tunnel-credentials.py](scripts/mysql-tunnel-credentials.py) 通过 macOS 系统 `security` 或 `mysql-secret-store-keychain` 读取，退出时清理本次资源。连接成功不能单独证明指定 SSH 密钥已被使用。

保留原生客户端参数和边界。`--tsv`、`--dump`、`-d` 是紧跟 profile 的选项，SQL 中的同名字符串不得被解释为 wrapper 参数。导出使用 [mysql-dump](../mysql-dump/SKILL.md)，需要同时安装该 skill 时按其说明执行。

修改工具后验证参数传递、认证失败、并发隔离和退出清理。在源码仓库运行 `python3 tests/test_mysql_tunnel.py`，使用本地替身而非正式服务器和真实凭据。

SSH 网关的签名结果通过隧道入口原样返回，见 [共用连接规则](../connection-operation-review/SKILL.md)。
