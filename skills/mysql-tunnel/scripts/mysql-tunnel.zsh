typeset -g _MYSQL_TUNNEL_SCRIPT_DIR="${${(%):-%N}:A:h}"

unalias mysql_tunnel mysqldump_tunnel 2>/dev/null

# Profiles are loaded from MYSQL_TUNNEL_PROFILES_FILE (default: ~/.my.cnf).
_mysql_tunnel_run() (
  local mode="$1" profile="${2-}"
  if [[ -z "$profile" ]]; then
    print -u2 'Usage: mysql_tunnel PROFILE [--tsv | --dump|-d SCHEMA TABLES --outputUrl=DIR] [arguments]'
    print -u2 '       mysqldump_tunnel PROFILE [arguments]'
    exit 2
  fi
  shift 2

  _find_bin() {
    local env_var="$1" name="$2"
    shift 2
    if (( ${(P)+env_var} )); then
      local override="${(P)env_var}"
      if [[ -n "$override" && -x "$override" ]]; then
        print -r -- "$override"
        return 0
      fi
      return 1
    fi
    local candidate=""
    candidate="$(command -v "$name" 2>/dev/null)" || candidate=""
    if [[ -n "$candidate" && -x "$candidate" ]]; then
      print -r -- "$candidate"
      return 0
    fi
    for candidate in "$@"; do
      if [[ -x "$candidate" ]]; then
        print -r -- "$candidate"
        return 0
      fi
    done
    return 1
  }

  # Wrapper options go immediately after the profile; SQL arguments stay intact.
  local want_tsv=0 need_mysqlsh=0 is_dump=0
  if [[ "$mode" == dump ]]; then
    is_dump=1
  fi
  if [[ "$mode" == query && "${1-}" == --tsv ]]; then
    want_tsv=1
    shift
  elif [[ "$mode" == query && ( "${1-}" == --dump || "${1-}" == -d ) ]]; then
    need_mysqlsh=1
    is_dump=1
    shift
    set -- -- util dump-tables "$@"
  elif [[ "$mode" == query && ( "${1-}" == --js || "${1-}" == --py ) ]]; then
    need_mysqlsh=1
  fi

  local client_kind="" client_bin=""
  if [[ "$mode" == dump ]]; then
    client_kind=mysqldump
    client_bin="$(_find_bin MYSQL_TUNNEL_MYSQLDUMP_BIN mysqldump \
      /opt/homebrew/opt/mysql-client/bin/mysqldump \
      /usr/local/opt/mysql-client/bin/mysqldump \
      /opt/homebrew/bin/mysqldump \
      /usr/local/bin/mysqldump)" || {
      print -u2 'mysqldump_tunnel: mysqldump executable not found'
      exit 1
    }
  else
    local preferred="${MYSQL_TUNNEL_CLIENT:-auto}"
    if (( need_mysqlsh )) || [[ "$preferred" == mysqlsh ]]; then
      client_kind=mysqlsh
      client_bin="$(_find_bin MYSQL_TUNNEL_MYSQLSH_BIN mysqlsh \
        /usr/local/bin/mysqlsh \
        /opt/homebrew/bin/mysqlsh \
        /usr/local/mysql-shell/bin/mysqlsh)" || {
        print -u2 'mysql_tunnel: mysqlsh is required for --dump/-d/--js/--py or MYSQL_TUNNEL_CLIENT=mysqlsh (use mysqldump_tunnel for SQL dumps)'
        exit 1
      }
    elif [[ "$preferred" == mysql ]]; then
      client_kind=mysql
      client_bin="$(_find_bin MYSQL_TUNNEL_MYSQL_BIN mysql \
        /opt/homebrew/opt/mysql-client/bin/mysql \
        /usr/local/opt/mysql-client/bin/mysql \
        /opt/homebrew/bin/mysql \
        /usr/local/bin/mysql)" || {
        print -u2 'mysql_tunnel: mysql client executable not found'
        exit 1
      }
    else
      if client_bin="$(_find_bin MYSQL_TUNNEL_MYSQL_BIN mysql \
        /opt/homebrew/opt/mysql-client/bin/mysql \
        /usr/local/opt/mysql-client/bin/mysql \
        /opt/homebrew/bin/mysql \
        /usr/local/bin/mysql)"; then
        client_kind=mysql
      elif client_bin="$(_find_bin MYSQL_TUNNEL_MYSQLSH_BIN mysqlsh \
        /usr/local/bin/mysqlsh \
        /opt/homebrew/bin/mysqlsh \
        /usr/local/mysql-shell/bin/mysqlsh)"; then
        client_kind=mysqlsh
      else
        print -u2 'mysql_tunnel: neither mysql nor mysqlsh executable was found'
        exit 1
      fi
    fi
  fi

  local input_file=""
  local -a client_args=()
  if [[ "$client_kind" == mysqlsh ]]; then
    if (( want_tsv )); then
      client_args+=(--tabbed)
    fi
    client_args+=("$@")
  elif [[ "$client_kind" == mysql ]]; then
    if (( want_tsv )); then
      client_args+=(--batch)
    fi
    local i=1 arg=""
    while (( i <= $# )); do
      arg="${argv[i]}"
      case "$arg" in
        -e|--execute)
          client_args+=("$arg")
          if (( i < $# )); then
            (( i++ ))
            client_args+=("${argv[i]}")
          fi
          ;;
        --file=*)
          input_file="${arg#*=}"
          if [[ ! -r "$input_file" ]]; then
            print -u2 "mysql_tunnel: SQL file not readable: $input_file"
            exit 2
          fi
          ;;
        --file)
          if (( i >= $# )); then
            print -u2 'mysql_tunnel: --file requires a file path'
            exit 2
          fi
          (( i++ ))
          input_file="${argv[i]}"
          if [[ ! -r "$input_file" ]]; then
            print -u2 "mysql_tunnel: SQL file not readable: $input_file"
            exit 2
          fi
          ;;
        *)
          client_args+=("$arg")
          ;;
      esac
      (( i++ ))
    done
  else
    client_args+=("$@")
  fi

  local gateway="" endpoint="" db_user="" credential_source="" defaults_suffix=""
  local pkcs11_dir="" pkcs11_module="" pkcs11_pubkey=""
  local profiles_file="${MYSQL_TUNNEL_PROFILES_FILE:-$HOME/.my.cnf}"
  if [[ ! -r "$profiles_file" ]]; then
    print -u2 "Profiles file not found: $profiles_file"
    exit 2
  fi
  local defaults_bin options opt value
  defaults_bin="$(_find_bin MYSQL_TUNNEL_PRINT_DEFAULTS_BIN my_print_defaults \
    /opt/homebrew/opt/mysql-client/bin/my_print_defaults \
    /usr/local/opt/mysql-client/bin/my_print_defaults)" || {
    print -u2 'mysql_tunnel: my_print_defaults from the MySQL client tools is required to read my.cnf'
    exit 2
  }
  options="$("$defaults_bin" --defaults-file="${profiles_file:a}" "mysql_tunnel_$profile")" || exit 2
  for opt in "${(@f)options}"; do
    value="${opt#*=}"
    case "$opt" in
      --gateway=*) gateway="$value" ;;
      --endpoint=*) endpoint="$value" ;;
      --db-user=*) db_user="$value" ;;
      --credential-source=*) credential_source="$value" ;;
      --defaults-suffix=*) defaults_suffix="$value" ;;
      --pkcs11-dir=*) pkcs11_dir="${value/#\~\//$HOME/}" ;;
      --pkcs11-module=*) pkcs11_module="${value/#\~\//$HOME/}" ;;
      --pkcs11-pubkey=*) pkcs11_pubkey="${value/#\~\//$HOME/}" ;;
    esac
  done
  if [[ -z "$gateway" || -z "$endpoint" || -z "$db_user" || -z "$credential_source" ]]; then
    print -u2 "Unknown or incomplete profile: $profile (checked $profiles_file)"
    exit 2
  fi
  local ssh_connect="$_MYSQL_TUNNEL_SCRIPT_DIR/ssh-connect"
  if [[ ! -x "$ssh_connect" ]]; then
    ssh_connect="$_MYSQL_TUNNEL_SCRIPT_DIR/../../connection-operation-review/scripts/ssh-connect"
  fi
  if [[ ! -x "$ssh_connect" ]]; then
    print -u2 "mysql_tunnel: executable ssh-connect not found near $_MYSQL_TUNNEL_SCRIPT_DIR"
    exit 1
  fi
  local tunnel_dir credential_url credential_attempted=0 child_pid=""
  umask 077
  tunnel_dir="$(mktemp -d /tmp/mysql-tunnel.XXXXXX)" || exit
  credential_url="$db_user@/tmp%2F${tunnel_dir##*/}%2Fmysql.sock"
  trap '
    ssh -F /dev/null -S "$tunnel_dir/control" -O exit "$gateway" >/dev/null 2>&1
    if (( credential_attempted )); then
      python3 "$_MYSQL_TUNNEL_SCRIPT_DIR/mysql-tunnel-credentials.py" \
        --erase "$credential_url" >/dev/null 2>&1
    fi
    rm -f "$tunnel_dir/mysql.sock" "$tunnel_dir/control" "$tunnel_dir/client.cnf" "$tunnel_dir/ssh.log"
    rmdir "$tunnel_dir" 2>/dev/null
  ' EXIT
  # Backgrounded children in non-interactive shells inherit SIG_IGN for SIGINT,
  # so forward SIGTERM and wait for the client to exit before tearing down the tunnel.
  trap '[[ -n "$child_pid" ]] && { kill -TERM "$child_pid" 2>/dev/null; wait "$child_pid" 2>/dev/null; }; exit 130' INT
  trap '[[ -n "$child_pid" ]] && { kill -TERM "$child_pid" 2>/dev/null; wait "$child_pid" 2>/dev/null; }; exit 143' TERM
  trap '[[ -n "$child_pid" ]] && { kill -HUP "$child_pid" 2>/dev/null; kill -TERM "$child_pid" 2>/dev/null; wait "$child_pid" 2>/dev/null; }; exit 129' HUP

  local auth_kind="MySQL SSH 隧道"
  if (( is_dump )); then
    auth_kind="MySQL 导出 SSH 隧道"
  fi

  local -a ssh_connect_opts=()
  if [[ -n "$pkcs11_dir" ]]; then
    ssh_connect_opts+=(--secure-ecdsa-dir "$pkcs11_dir")
  elif [[ -n "$pkcs11_module" || -n "$pkcs11_pubkey" ]]; then
    ssh_connect_opts+=(--pkcs11-module "$pkcs11_module" --pkcs11-pubkey "$pkcs11_pubkey")
  fi

  SSH_CONNECT_LOG_FILE="$tunnel_dir/ssh.log" \
    SSH_CONNECT_PROFILE="$profile" SSH_CONNECT_DB_USER="$db_user" SSH_CONNECT_DB_SERVER="$endpoint" \
    SSH_CONNECT_METHOD="$auth_kind" "$ssh_connect" "${ssh_connect_opts[@]}" \
    -n -N -o ConnectTimeout=15 -o ClearAllForwardings=yes \
    -o ForwardAgent=no -o ControlPersist=no \
    -M -S "$tunnel_dir/control" -f "$gateway" || exit
  ssh -F /dev/null -S "$tunnel_dir/control" -O forward \
    -L "$tunnel_dir/mysql.sock:$endpoint" "$gateway" || exit
  if [[ "$client_kind" == mysqlsh ]]; then
    credential_attempted=1
  fi
  python3 "$_MYSQL_TUNNEL_SCRIPT_DIR/mysql-tunnel-credentials.py" \
    "$credential_source" "$credential_url" "$client_kind" "$tunnel_dir/client.cnf" || exit
  local -a client_cmd=()
  if [[ "$client_kind" == mysqlsh ]]; then
    client_cmd=(
      "$client_bin" --defaults-group-suffix="$defaults_suffix" --sqlc
      --host=localhost --user="$db_user" --socket="$tunnel_dir/mysql.sock"
      --credential-store-helper=keychain --save-passwords=never "${client_args[@]}"
    )
  elif [[ "$client_kind" == mysql ]]; then
    client_cmd=(
      "$client_bin" --defaults-file="$tunnel_dir/client.cnf" --no-login-paths
      --protocol=SOCKET --host=localhost --user="$db_user"
      --socket="$tunnel_dir/mysql.sock" "${client_args[@]}"
    )
  else
    client_cmd=(
      "$client_bin" --defaults-file="$tunnel_dir/client.cnf" --no-login-paths
      --protocol=SOCKET --host=localhost --user="$db_user"
      --socket="$tunnel_dir/mysql.sock" "${client_args[@]}"
    )
  fi
  if [[ -n "$input_file" ]]; then
    if [[ -t 0 ]]; then
      "${client_cmd[@]}" < "$input_file"
      exit "$?"
    fi
    "${client_cmd[@]}" < "$input_file" &
  elif [[ -t 0 ]]; then
    "${client_cmd[@]}"
    exit "$?"
  else
    "${client_cmd[@]}" <&0 &
  fi
  child_pid=$!
  wait "$child_pid"
  local rc=$?
  child_pid=""
  exit "$rc"
)

mysql_tunnel() { _mysql_tunnel_run query "$@"; }
mysqldump_tunnel() { _mysql_tunnel_run dump "$@"; }
