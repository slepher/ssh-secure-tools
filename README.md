# ssh-secure-tools

面向 macOS 的 SSH 密钥管理、Secure Enclave 硬件认证、MySQL 独立安全隧道与数据导出工具集，以及配套的 Agent Skills。

根目录 `scripts/` 提供命令行入口，其中连接与隧道脚本通过符号链接复用 `skills/` 内的实现。

| 模块 | 入口 | 用途 |
| :--- | :--- | :--- |
| SSH 连接封装 | `scripts/ssh-connect` | 隔离 `ssh-agent` 管理、Touch ID 自定义弹窗文案、硬件签名拒绝精准诊断 |
| MySQL 安全隧道 | `scripts/mysql-tunnel.zsh` | 独立 Unix socket 隧道、自动凭据读取与退出清理（查询 / TSV / Dump） |
| SSH 密钥管理 | `scripts/create-ssh-key.py` | 创建/归档轮换 Ed25519、原生 `sk-ecdsa` 与 PKCS#11 `ecdsa-sha2-nistp256` 密钥 |
| Agent Skills | `skills/*` | 4 个可独立分发的连接审阅、诊断与 MySQL 隧道/导出技能 |

---

## 环境依赖

- **基础工具**：Zsh、Python 3、OpenSSH（`ssh` / `ssh-agent` / `ssh-add` 按 `PATH` 顺序查找，`create-ssh-key.py` 使用 `/usr/bin/ssh-keygen`）以及 macOS 系统 `security`（`/usr/bin/security`）。
- **MySQL 隧道与导出**：标准 `mysql` 客户端、`mysqldump` 与 `my_print_defaults`（通常随 `mysql-client` 一并提供，用于解析 `~/.my.cnf`）即可完成查询、TSV 输出和传统 SQL 导出，**不强依赖 `mysqlsh`**；仅在使用 `mysql_tunnel --dump/-d`（`util.dumpTables()`）或 `--js`/`--py` 时需要安装 MySQL Shell（`mysqlsh` 及其 `mysql-secret-store-keychain`）。
- **Secure Enclave 认证**：支持 `create-ctk-identity` 的 `sc_auth` 与已录入的 Touch ID；原生 `sk-ecdsa` 模式依赖 Apple `/usr/lib/ssh-keychain.dylib`，标准 `ecdsa-sha2-nistp256` 模式依赖 `keychain-pkcs11.dylib`（见下文[获取方式](#keychain-pkcs11-兼容性下载与构建)，可直接下载预编译动态库或从源码编译）。

---

## 快速开始

在当前终端中加载 MySQL 隧道命令（也可将下行加入 `~/.zshrc` 以持久生效）：

```zsh
source "$HOME/project/ssh-secure-tools/scripts/mysql-tunnel.zsh"
```

脚本会根据自身路径自动定位凭据 helper 与 `ssh-connect`。如需独立部署，可将 `mysql-tunnel.zsh`、`mysql-tunnel-credentials.py` 以及具备可执行权限的 `ssh-connect` 一并放入 `~/.zsh/` 后再加载。

---

## SSH 连接（`ssh-connect`）

`scripts/ssh-connect` 封装了系统 OpenSSH，在保留原始诊断输出的同时，准确区分本机硬件签名拒绝与其他连接失败：

```zsh
# 普通 SSH 或原生 Secure Enclave（sk-ecdsa）连接
scripts/ssh-connect TARGET bash -s < FILE

# 使用 keychain-pkcs11 标准 ECDSA（secure-ecdsa）连接
scripts/ssh-connect --secure-ecdsa-dir "$HOME/.ssh/secure-ecdsa" TARGET bash -s < FILE

# 也可分别指定 PKCS#11 模块与公钥路径（支持 id_ecdsa.pub / id_ed25519.pub 等）
scripts/ssh-connect \
  --pkcs11-module "$HOME/.ssh/secure-ecdsa/keychain-pkcs11.dylib" \
  --pkcs11-pubkey "$HOME/.ssh/secure-ecdsa/id_ecdsa.pub" \
  TARGET bash -s < FILE

# 自定义 Touch ID 弹窗提示文案（或通过 SSH_CONNECT_AUTH_REASON 传入）
scripts/ssh-connect --auth-reason "Deploy -> prod_gw" \
  --secure-ecdsa-dir "$HOME/.ssh/secure-ecdsa" TARGET bash -s < FILE
```

### 行为说明

- **自动管理隔离 `ssh-agent`**：使用 `--secure-ecdsa-dir`（或 `--pkcs11-module` + `--pkcs11-pubkey`）时，脚本会自动启动仅允许加载该模块的独立 `ssh-agent -P`，过滤移除本机其他无关 CTK 身份，强制启用 `IdentitiesOnly=yes` 与 `ForwardAgent=no`，并在退出或收到中断信号时自动回收 `ssh-agent`。
- **Touch ID 自定义弹窗文案（`KEYCHAIN_PKCS11_PROMPT`）**：
  - 在 PKCS#11 模式下，脚本会在启动独立 `ssh-agent` 前自动导出 `KEYCHAIN_PKCS11_PROMPT`（由 `ssh-pkcs11-helper` 继承）：
    - 文案以换行开头，依次展示配置、连接方式、用户名和服务器；直接 SSH 连接以目标名作为配置和服务器，仅在显式指定 SSH 用户时展示用户名。
    - 传入 `--auth-reason TEXT` 或 `SSH_CONNECT_AUTH_REASON` 时，会另起一行显示“用途：TEXT”。
    - MySQL 隧道场景以 profile 名作为配置，用户名与服务器展示 SSH 网关信息，随后另列数据库用户名与数据库服务器（`主机:端口`），并在连接方式中区分“MySQL SSH 隧道”与“MySQL 导出 SSH 隧道”。
- **诊断与退出码**：
  - 当 OpenSSH 明确报告本机签名被拒绝或取消（含 `from agent: agent refused operation`）时，在 `stderr` 输出 `SSH_AUTH_REJECTED` 并返回退出码 `77`（`agent refused operation` 仅表示代理拒绝签名，具体原因由本机认证组件掌握）。
  - 其他本机签名失败会输出 `SSH_SIGNING_FAILED` 并保留退出码 `255`。
  - 由于远端命令本身也可能返回 `77`，调用方应同时核对 `stderr` 中的诊断标记。
- **参数限制**：`ssh-connect` 内部使用 `-E` 捕获 OpenSSH 客户端日志，调用时请勿额外传入 `-E`。

### `keychain-pkcs11` 兼容性、下载与构建

| `keychain-pkcs11` 版本 | 自定义弹窗文案 | 加载模块时的 PIN 行为 | 取消授权识别 |
| :--- | :--- | :--- | :--- |
| **原始上游及仅添加 ECDSA / Ed25519 的分支** | 显示系统默认文案 | `ssh-connect` 通过 `SSH_ASKPASS` 提交空输入，非交互加载仍可能报 `pin required` | `agent refused operation` → `SSH_AUTH_REJECTED`（`77`） |
| **[slepher/keychain-pkcs11](https://github.com/slepher/keychain-pkcs11)** | 读取 `KEYCHAIN_PKCS11_PROMPT` 展示连接信息，且禁止 Touch ID 复用 | 未启用 `askPIN` 时可在无 PIN 的情况下加载，签名授权由 macOS 处理 | 明确取消文本（如 `user cancelled` / `Operation cancelled`）与 `agent refused operation` 均识别为 `SSH_AUTH_REJECTED`（`77`） |

获取 `keychain-pkcs11.dylib` 有以下**两种方式，按需任选其一即可**：

#### 方式一：直接下载预编译 `.dylib`（推荐）

前往修改版的 [GitHub Releases](https://github.com/slepher/keychain-pkcs11/releases) 页面，选择所需版本，在 **Assets** 中直接下载 `keychain-pkcs11.dylib`。

该预编译文件为通用动态库（Universal Binary），最低系统要求为 Intel（`x86_64`）macOS 10.14 与 Apple Silicon（`arm64`）macOS 11.0。

#### 方式二：从源码本地编译

如果希望自行构建，可在 [slepher/keychain-pkcs11](https://github.com/slepher/keychain-pkcs11) 页面选择 **Code → Download ZIP** 解压进入源码目录，或直接通过 Git 克隆：

```zsh
git clone git@github.com:slepher/keychain-pkcs11.git
cd keychain-pkcs11
```

在源码目录中编译并执行本地签名，生成 `.libs/keychain-pkcs11.dylib`：

```zsh
./autogen.sh && ./configure && make &&
  codesign --force --sign - .libs/keychain-pkcs11.dylib
```

> **使用说明**：无论是直接下载还是本地编译，得到的 `keychain-pkcs11.dylib` 用法完全一致。使用 `--pkcs11-module` 时传入该动态库的绝对路径；使用 `--secure-ecdsa-dir` 时，将动态库与对应公钥放在同一目录即可。

---

## MySQL 安全隧道与导出

### 配置 Profile

在 `~/.my.cnf` 中为每个 profile 添加一个 `[mysql_tunnel_<名称>]` 分组。脚本通过 `my_print_defaults` 读取指定分组（亦可通过环境变量 `MYSQL_TUNNEL_PROFILES_FILE` 指定其他配置文件），仓库本身不内置具体服务器地址或账号：

```ini
[mysql_tunnel_staging]
gateway=staging_gateway
endpoint=127.0.0.1:3306
db-user=appuser
credential-source=keychain:staging-mysql:appuser
defaults-suffix=_staging

[mysql_tunnel_prod]
gateway=prod_db_gateway
endpoint=db.internal.example:3306
db-user=readonly
credential-source=readonly@127.0.0.1:13306
defaults-suffix=_prod
# 可选，也可分别设置 pkcs11-module 和 pkcs11-pubkey（如配合 id_ed25519.pub）
pkcs11-dir=~/.ssh/secure-ecdsa
```

使用 `keychain:SERVICE` 或 `keychain:SERVICE:ACCOUNT` 凭据源时，可通过 macOS 系统 `security` 命令预先录入密码（交互式输入，不会落盘到 shell 历史记录）：

```zsh
security add-generic-password -s staging-mysql -a appuser -w
```

环境类别由执行上下文、全局 `AGENTS.md` 或项目级 `AGENTS.md` 指定，profile 名称本身不代表环境独占性（配置方式见下文[在全局 `AGENTS.md` 中配置服务器属性](#在全局-agentsmd-中配置服务器属性)）。

### 常用命令

```zsh
# 通过独立 SSH 隧道交互连接目标数据库
mysql_tunnel staging

# 执行已保存的 SQL 文件（同时支持 --file=FILE 与 --file FILE），退出时自动清理本次连接
mysql_tunnel staging --file=/tmp/reviewed-query.sql

# 原生 MySQL Shell dump：先使用 --dryRun=true 预览导出计划
mysql_tunnel staging --dump app_db orders \
  --outputUrl="$HOME/exports/orders" --dryRun=true \
  '--where=app_db.orders=id >= 1000'

# -d 等价于 --dump；实际导出需指定不存在或为空的目标目录
mysql_tunnel staging -d app_db orders \
  --outputUrl="$HOME/exports/orders" \
  '--where=app_db.orders=id >= 1000'

# 兼容传统 mysqldump，适用于需要导出标准 SQL 文件的场景
mysqldump_tunnel staging app_db orders > "$HOME/exports/orders.sql"
```

### 隔离与清理机制

- **多连接隔离**：每次调用都会创建独立的临时目录（权限 `0700`）、Unix socket、SSH master（显式关闭 `ForwardAgent` 与 `ControlPersist`）及临时凭据，支持多连接并发且互不干扰。
- **客户端与凭据保护**：`mysql_tunnel` 默认优先使用标准 `mysql` 客户端（可通过 `MYSQL_TUNNEL_CLIENT=mysqlsh` 指定使用 `mysqlsh`；`--dump`/`-d`/`--js`/`--py` 会自动选用 `mysqlsh`）。`mysql` 与 `mysqldump_tunnel` 通过权限 `0600` 的临时密码文件传递密码，密码直接从 macOS `/usr/bin/security`（支持 `keychain:SERVICE` / `keychain:SERVICE:ACCOUNT`）或已安装的 `mysql-secret-store-keychain` 读取，不进入命令行参数或环境变量；`mysqlsh` 模式则使用临时钥匙串条目并在退出时擦除。
- **自动清理**：正常退出或收到 `INT`、`TERM`、`HUP` 信号时，脚本会自动关闭隧道并清理所有临时资源（强制 `SIGKILL` 或异常断电除外）。

---

## SSH 密钥创建与轮换

`scripts/create-ssh-key.py` 支持三种密钥模式：

| 模式（`--type`） | 公钥类型 | 适用场景 |
| :--- | :--- | :--- |
| `ed25519`（默认） | `ssh-ed25519` | **兼容性最广，通用场景优先选用**，支持跨设备迁移和绝大多数 SSH 服务端 |
| `secure-enclave` | `sk-ecdsa-sha2-nistp256@openssh.com` | 需本机 Touch ID 硬件保护，且服务端支持 OpenSSH 8.2+ FIDO/SK 密钥 |
| `secure-enclave-ecdsa` | `ecdsa-sha2-nistp256` | 需本机 Touch ID 硬件保护，但服务端不支持 `sk-*` 密钥类型（配合 `keychain-pkcs11` 使用） |

`ed25519` 模式默认生成无口令密钥；如需设置口令，可添加 `--prompt-passphrase`，由 `ssh-keygen` 在终端交互读取。

```zsh
# 1. 新建普通 Ed25519 密钥（目标文件已存在时会拒绝覆盖）
python3 scripts/create-ssh-key.py --file "$HOME/.ssh/id_ed25519_work" \
  --comment user@workstation

# 2. 替换现有密钥对，并将旧密钥归档到指定的空目录
python3 scripts/create-ssh-key.py --file "$HOME/.ssh/id_ed25519" \
  --comment user@workstation --archive-dir "$HOME/.ssh/legacy-ed25519"

# 3. 创建原生 Secure Enclave 密钥（sk-ecdsa）及其 SSH 句柄
python3 scripts/create-ssh-key.py --type secure-enclave \
  --file "$HOME/.ssh/id_ecdsa_sk_personal" --label personal-ssh \
  --comment user@mac-secure-enclave

# 4. 创建基于 PKCS#11 的标准 ECDSA Secure Enclave 密钥（仅保存公钥文件）
python3 scripts/create-ssh-key.py --type secure-enclave-ecdsa \
  --file "$HOME/.ssh/secure-ecdsa/id_ecdsa.pub" --label secure-ecdsa \
  --pkcs11-module "$HOME/.ssh/secure-ecdsa/keychain-pkcs11.dylib" \
  --comment user@mac-secure-ecdsa

# 5. 为现有普通私钥添加或修改口令（原口令为空时直接回车）
ssh-keygen -p -f "$HOME/.ssh/id_ed25519"
```

### 安全与轮换说明

- `--archive-dir` 由调用者指定且必须为空或不存在；脚本会先归档旧文件并校验一致性，再创建新密钥并原子替换，避免归档失败导致留下孤儿 CTK identity。
- 修改已有普通私钥的口令不会改变公钥指纹。每份私钥副本的口令需分别修改，已加载到 SSH agent 的副本需单独移除或重新加载。
- 注册远端公钥时，请先核对目标用户及新旧公钥指纹，并备份原有的 `authorized_keys`。写入新公钥后，使用指定的新身份建立全新连接验证登录，确认无误后再按授权移除旧公钥。文件私钥与原生 `sk-ecdsa` 应禁用 SSH agent 和连接复用；PKCS#11 模式请通过 [`ssh-connect`](#ssh-连接ssh-connect) 管理独立 agent。
- Secure Enclave 私钥由当前 Mac 的硬件安全芯片保管，无法导出，迁移设备时需重新生成并部署公钥；若创建过程中断，请先使用 `sc_auth list-ctk-identities` 核对状态后再重试。

---

## 本地测试

所有测试均在临时目录中使用本地替身脚本运行，不会连接任何实际服务器或访问真实凭据：

```zsh
python3 tests/test_create_ssh_key.py
python3 tests/test_mysql_tunnel.py
python3 tests/test_skill_package.py
```

---

## 项目 Skills 与安装

`skills/` 目录包含 4 个可独立分发的 Agent Skills：

- `connection-operation-review`：SSH/Bash 与 MySQL 操作的前置环境分类与单文件审阅约定（独占测试环境按需展示；非独占测试与正式环境须先完整展示操作文件及执行命令后再执行，内容变更后重新展示）。
- `ssh-connection-check`：SSH 别名解析、版本检查与 Touch ID / 密钥认证验证。
- `mysql-tunnel`：MySQL 安全隧道连接与 SQL 执行。
- `mysql-dump`：基于 MySQL Shell `dumpTables()` 的表级过滤导出与结果校验。

每个技能目录均为独立安装单元：SSH/MySQL 执行类技能依赖 `connection-operation-review`，`mysql-dump` 还额外依赖 `mysql-tunnel`。Skill 属于 agent 执行约定，系统级权限边界仍由运行环境本身保障。

安装到 `~/.codex/skills/` 前请先检查同名目录，避免覆盖已有配置：

```zsh
(
  for skill_dir in skills/*; do
    destination="$HOME/.codex/skills/${skill_dir##*/}"
    if [[ -e "$destination" ]]; then
      print -u2 "Skill already exists: $destination"
      exit 1
    fi
  done
  mkdir -p "$HOME/.codex/skills"
  cp -R skills/* "$HOME/.codex/skills/"
)
```

安装完成后，即可在后续对话中直接使用，也可通过 `~/.codex/skills/mysql-tunnel/scripts/mysql-tunnel.zsh` 加载隧道脚本。

### 在全局 `AGENTS.md` 中配置服务器属性

建议在全局 `AGENTS.md`（如 `~/.codex/AGENTS.md`）或项目级 `AGENTS.md` 中预先声明各 SSH 目标与 MySQL profile 的环境类别（**独占测试**、**非独占测试**或**正式服务器**）。

如果未提前声明：

- 使用 Touch ID / Secure Enclave 认证的目标会被 `connection-operation-review` 默认按**正式服务器**处理；
- 其他无法判断的目标会先询问环境类别。

一旦被归类为**非独占测试**或**正式服务器**，每次执行远端 Bash 或 SQL 前都会触发单文件审阅流程——在对话中先完整输出待执行的 Bash / SQL 文件内容与实际命令，内容变更后也会重新展示；而明确标注为**独占测试**的服务器则允许按需展示、直接批量操作。

全局 `AGENTS.md` 配置示例：

```markdown
## 服务器环境属性

- `dev_box` / MySQL profile `dev`：独占测试服务器
- `staging_gateway` / MySQL profile `staging`：非独占测试服务器
- `prod_db_gateway` / MySQL profile `prod`：正式服务器
```
