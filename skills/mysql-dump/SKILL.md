---
name: mysql-dump
description: "使用 mysql_tunnel --dump/-d 导出 MySQL 表、按 where 过滤和核对 dump；需要传统 SQL 文件时使用 mysqldump_tunnel。"
---

与 [connection-operation-review](../connection-operation-review/SKILL.md) 和 [mysql-tunnel](../mysql-tunnel/SKILL.md) 一起安装。连接前应用环境分类和展示规则。加载同级 `mysql-tunnel/scripts/mysql-tunnel.zsh`，使用既有 profile、系统 OpenSSH 和钥匙串。

原生接口为 `mysql_tunnel PROFILE --dump SCHEMA TABLES --outputUrl=DIR [utility options]`，`-d` 等价（此模式需要安装 `mysqlsh`）。多个表用逗号分隔，保留 shell 参数边界。过滤使用 `--where=SCHEMA.TABLE=条件表达式`，例如 `'--where=app_db.orders=id >= 1000'`，表达式不含 `WHERE` 关键字。未安装 `mysqlsh` 时可直接使用 `mysqldump_tunnel`。

按用户需要使用 `--dryRun=true`。预览和实际导出是两个操作，用户只要求预览时不自动追加正式导出。目标目录需要不存在或为空。原生 dump 包含 DDL、元数据和压缩数据，由 `util.loadDump()` 恢复。需要传统 SQL 文件时使用 `mysqldump_tunnel PROFILE ...`，工具会创建并清理受权限保护的临时密码文件。

需要预展示时，CLI dump 默认使用一个临时 shell 文件。注释写明目标、输出目录、是否 dry run，以及筛选语义的 SQL，例如 `SELECT * FROM app_db.orders WHERE id >= 1000;`。完整展示同一文件后执行。用户选择直接运行 utility 脚本时可用一个 JS 文件，经 `mysql_tunnel PROFILE --js --file=FILE` 执行。复杂文件结构仅在用户明确要求时创建。

`dumpTables()` 以表为单位支持条件过滤，不能直接接收任意 SELECT、JOIN 或聚合。正式导出可能短暂取得一致性相关的锁，提前说明与任务相关的影响，按授权保持一致性语义。

验证退出码和 `@.done.json` 的 `tableRows`。需要核对内容时，用已有 `zstd` 解压本次 `.tsv.zst`，在本地比较行数、目标记录和字段。重新连接源表核对时仍先应用环境规则。避免为了核对再导出整库数据。

检查本次隧道与临时凭据清理，不干扰其他并发连接。保留用户要求的成果，报告文件位置和实际核对结果。
