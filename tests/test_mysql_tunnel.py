from pathlib import Path
import json
import os
import shlex
import signal
import subprocess
import tempfile
import time

project = Path(__file__).resolve().parents[1]
configuration = (project / 'scripts/mysql-tunnel.zsh').read_text()
assert '_mysql_tunnel_run() (' in configuration

with tempfile.TemporaryDirectory(prefix='mysql-tunnel-check-') as directory:
    root = Path(directory)
    ssh = root / 'ssh'
    mysql_cli = root / 'mysql'
    mysqlsh = root / 'mysqlsh'
    helper = root / 'keychain'
    security = root / 'security'

    ssh.write_text('''#!/bin/sh
if [ "$1" = -E ]; then
  printf '%s' "$TEST_SSH_DIAGNOSTIC" >> "$2"
  printf '%s' "$TEST_REMOTE_STDERR" >&2
  shift 2
fi
case " $* " in
  *" -O exit "*) exit 0 ;;
esac
if [ "$MYSQL_TUNNEL_TEST_SSH_STATUS" != 0 ]; then
  exit "$MYSQL_TUNNEL_TEST_SSH_STATUS"
fi
while [ "$#" -gt 0 ]; do
  if [ "$1" = -L ]; then
    shift
    touch "${1%%:*}"
  fi
  shift
done
''')
    mysql_cli.write_text('''#!/usr/bin/env python3
from pathlib import Path
import json, os, stat, sys, time
socket = next(arg.split('=', 1)[1] for arg in sys.argv if arg.startswith('--socket='))
assert Path(socket).exists()
defaults = next(arg.split('=', 1)[1] for arg in sys.argv if arg.startswith('--defaults-file='))
assert (Path(defaults).stat().st_mode & 0o777) == 0o600
assert 'password="test-password"' in Path(defaults).read_text()
payload = {'client': 'mysql', 'argv': sys.argv[1:]}
if os.environ.get('MYSQL_TUNNEL_TEST_READ_STDIN') == '1':
    payload['stdin'] = sys.stdin.read()
Path(os.environ['MYSQL_TUNNEL_TEST_MARKER']).write_text(json.dumps(payload))
release = os.environ.get('MYSQL_TUNNEL_TEST_RELEASE')
while release and not Path(release).exists():
    time.sleep(0.02)
''')
    mysqlsh.write_text('''#!/usr/bin/env python3
from pathlib import Path
import json, os, sys, time
socket = next(arg.split('=', 1)[1] for arg in sys.argv if arg.startswith('--socket='))
assert Path(socket).exists()
Path(os.environ['MYSQL_TUNNEL_TEST_MARKER']).write_text(json.dumps({'client': 'mysqlsh', 'argv': sys.argv[1:]}))
release = os.environ.get('MYSQL_TUNNEL_TEST_RELEASE')
while release and not Path(release).exists():
    time.sleep(0.02)
''')
    helper.write_text('''#!/usr/bin/env python3
from pathlib import Path
import json, os, sys
request = json.load(sys.stdin)
if sys.argv[1] == 'get':
    print(json.dumps({'Secret': 'test-password'}))
elif sys.argv[1] == 'store':
    assert request['Secret'] == 'test-password'
elif sys.argv[1] == 'erase':
    Path(os.environ['MYSQL_TUNNEL_TEST_ERASED']).write_text(request['ServerURL'])
''')
    security.write_text('''#!/bin/sh
if [ "$1" = find-generic-password ]; then
  printf '%s\n' 'test-password'
  exit 0
fi
exit 1
''')
    for executable in (ssh, mysql_cli, mysqlsh, helper, security):
        executable.chmod(0o700)

    credential_script = root / 'mysql-tunnel-credentials.py'
    credential_script.write_text((project / 'scripts/mysql-tunnel-credentials.py').read_text())

    connect = root / 'ssh-connect'
    connect.write_text((project / 'scripts/ssh-connect').read_text())
    connect.chmod(0o700)
    module = root / 'mysql-tunnel.zsh'
    module.write_text(configuration)
    function = 'source ' + shlex.quote(str(module))

    se_dir = root / 'secure-ecdsa'
    se_dir.mkdir()
    (se_dir / 'keychain-pkcs11.dylib').write_bytes(b'fake-dylib')
    (se_dir / 'id_ecdsa.pub').write_text('ecdsa-sha2-nistp256 AAAATARGET test@secure-ecdsa\n')

    profiles_file = root / 'profiles.cnf'
    profiles_file.write_text(f'''[mysql_tunnel_primary]
gateway=db_gateway
endpoint=db-primary.internal.example:3306
db-user=appuser
credential-source=appuser@127.0.0.1:13306
defaults-suffix=_primary

[mysql_tunnel_secondary]
gateway=dev_host
endpoint=127.0.0.1:3306
db-user=devuser
credential-source=keychain:dev-mysql:devuser
defaults-suffix=_secondary

[mysql_tunnel_custom_se]
gateway=sshuser@custom_gw
endpoint=10.0.0.5:3306
db-user=appuser
credential-source=appuser@10.0.0.5:3306
defaults-suffix=_custom
pkcs11-dir="{se_dir}"
''')

    command = ['/bin/zsh', '-f', '-c', function + '\nmysql_tunnel primary -e "SELECT 1"']

    def environment(name, status=0, hold=False, with_mysqlsh=True, with_mysql=True, client=None):
        result = dict(os.environ,
                      PATH=f"{root}:{os.environ.get('PATH', '')}",
                      MYSQL_TUNNEL_PROFILES_FILE=str(profiles_file),
                      MYSQL_TUNNEL_MYSQL_BIN=str(mysql_cli) if with_mysql else '',
                      MYSQL_TUNNEL_MYSQLDUMP_BIN=str(mysql_cli),
                      MYSQL_TUNNEL_MYSQLSH_BIN=str(mysqlsh) if with_mysqlsh else '',
                      MYSQL_TUNNEL_KEYCHAIN_HELPER=str(helper) if with_mysqlsh else '',
                      MYSQL_TUNNEL_SECURITY_BIN=str(security),
                      MYSQL_TUNNEL_TEST_SSH_STATUS=str(status),
                      MYSQL_TUNNEL_TEST_MARKER=str(root / (name + '.started')),
                      MYSQL_TUNNEL_TEST_ERASED=str(root / (name + '.erased')))
        if client is not None:
            result['MYSQL_TUNNEL_CLIENT'] = client
        if hold:
            result['MYSQL_TUNNEL_TEST_RELEASE'] = str(root / (name + '.release'))
        return result

    failed = subprocess.run(command, env=environment('failure', 7), capture_output=True, text=True)
    assert failed.returncode == 7, failed.stderr
    assert not (root / 'failure.started').exists(), 'MySQL started after SSH failed'

    missing_profiles = environment('missing-profiles', 0)
    missing_profiles['MYSQL_TUNNEL_PROFILES_FILE'] = str(root / 'nonexistent.cnf')
    missing_res = subprocess.run(command, env=missing_profiles, capture_output=True, text=True)
    assert missing_res.returncode == 2 and 'Profiles file not found' in missing_res.stderr, missing_res.stderr

    # Without mysqlsh or mysql-secret-store-keychain: mysql_tunnel and mysqldump_tunnel work via mysql + security.
    sql_file = root / 'query.sql'
    sql_file.write_text('SELECT 42;\n')
    for name, invocation, expected_tail, read_stdin in (
        ('no-sh-query', ['mysql_tunnel', 'primary', '-e', "SELECT '--dump'"], ['-e', "SELECT '--dump'"], False),
        ('no-sh-tsv', ['mysql_tunnel', 'primary', '--tsv', '-e', 'SELECT 1'], ['--batch', '-e', 'SELECT 1'], False),
        ('no-sh-file', ['mysql_tunnel', 'secondary', f'--file={sql_file}'], [], True),
        ('no-sh-file-sep', ['mysql_tunnel', 'secondary', '--file', str(sql_file)], [], True),
        ('no-sh-dump', ['mysqldump_tunnel', 'primary', 'app_db'], ['app_db'], False),
    ):
        env = environment(name, with_mysqlsh=False)
        if read_stdin:
            env['MYSQL_TUNNEL_TEST_READ_STDIN'] = '1'
        result = subprocess.run(['/bin/zsh', '-f', '-c', function + '\n' + ' '.join(shlex.quote(arg) for arg in invocation)],
                                env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        actual = json.loads((root / (name + '.started')).read_text())
        assert actual['client'] == 'mysql', actual
        if expected_tail:
            assert actual['argv'][-len(expected_tail):] == expected_tail, actual
        if read_stdin:
            assert actual['stdin'] == 'SELECT 42;\n', actual
        assert not (root / (name + '.erased')).exists(), 'mysql/mysqldump mode should not call mysqlsh keychain erase'

    # --dump requires mysqlsh and fails clearly when mysqlsh is absent.
    no_sh_dump_util = subprocess.run(
        ['/bin/zsh', '-f', '-c', function + '\nmysql_tunnel primary --dump app_db orders'],
        env=environment('no-sh-util', with_mysqlsh=False), capture_output=True, text=True,
    )
    assert no_sh_dump_util.returncode == 1 and 'mysqlsh is required' in no_sh_dump_util.stderr, no_sh_dump_util.stderr

    signing = 'sign_and_send_pubkey: signing failed for ECDSA-SK from authenticator: '
    agent_refusal = ('sign_and_send_pubkey: signing failed for ECDSA '
                     '"/tmp/secure-ecdsa/id_ecdsa.pub" from agent: agent refused operation')
    for path in ([str(connect), 'host'], command,
                 ['/bin/zsh', '-fc', function + '\nmysqldump_tunnel primary db'],
                 ['/bin/zsh', '-fc', function + '\nmysql_tunnel primary --dump db table']):
        for diagnostic, remote, expected, marker in (
            (signing + 'Operation cancelled', '', 77, 'SSH_AUTH_REJECTED'),
            (agent_refusal + '\r', '', 77, 'SSH_AUTH_REJECTED'),
            (signing + 'device not found', '', 255, 'SSH_SIGNING_FAILED'),
            ('', agent_refusal, 255, None),
            ('Host key verification failed.', '', 255, None),
            ('Permission denied (publickey).', '', 255, None),
            ('', signing + 'Operation cancelled', 255, None),
        ):
            env = environment('auth', 255)
            env.update(TEST_SSH_DIAGNOSTIC=diagnostic + '\n', TEST_REMOTE_STDERR=remote)
            result = subprocess.run(path, env=env, capture_output=True, text=True)
            assert result.returncode == expected, result.stderr
            assert diagnostic.rstrip("\r") in result.stderr and remote in result.stderr
            if marker:
                assert marker in result.stderr, result.stderr
            else:
                assert 'SSH_AUTH_REJECTED' not in result.stderr and 'SSH_SIGNING_FAILED' not in result.stderr
            assert not result.stdout and not (root / 'auth.started').exists()
            assert not (root / 'auth.erased').exists()

    # With mysqlsh present: --dump/-d and explicit MYSQL_TUNNEL_CLIENT=mysqlsh use mysqlsh + keychain helper.
    dump_arguments = ['app_db', 'orders,items', '--outputUrl=/tmp/example dump',
                      '--where=app_db.orders=id > 1000', '--dryRun=true']
    for flag in ('--dump', '-d'):
        arguments = ['mysql_tunnel', 'primary', flag] + dump_arguments
        invocation = ' '.join(shlex.quote(arg) for arg in arguments)
        result = subprocess.run(['/bin/zsh', '-f', '-c', function + '\n' + invocation],
                                env=environment('dump'), capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        actual = json.loads((root / 'dump.started').read_text())
        assert actual['client'] == 'mysqlsh', actual
        marker = actual['argv'].index('--')
        assert actual['argv'][marker:] == ['--', 'util', 'dump-tables'] + dump_arguments, actual
        assert '--credential-store-helper=keychain' in actual['argv'], actual
        assert (root / 'dump.erased').exists(), 'Dump credential was not cleaned'

    sh_tsv = subprocess.run(
        ['/bin/zsh', '-f', '-c', function + '\nmysql_tunnel primary --tsv -e "SELECT 1"'],
        env=environment('sh-tsv', client='mysqlsh'), capture_output=True, text=True,
    )
    assert sh_tsv.returncode == 0, sh_tsv.stderr
    actual = json.loads((root / 'sh-tsv.started').read_text())
    assert actual['client'] == 'mysqlsh' and actual['argv'][-3:] == ['--tabbed', '-e', 'SELECT 1'], actual
    assert (root / 'sh-tsv.erased').exists()

    # ssh-connect must reject caller-supplied -E so client diagnostics cannot be silently overridden.
    bad_e = subprocess.run([str(connect), '-E', str(root / 'other.log'), 'host'],
                           env=environment('bad-e'), capture_output=True, text=True)
    assert bad_e.returncode == 2 and 'do not pass -E' in bad_e.stderr, bad_e.stderr

    # Test ssh-connect --secure-ecdsa-dir isolated agent lifecycle, identity filtering, and cleanup.
    agent_log = root / 'agent.log'
    ssh_args_log = root / 'ssh-args.log'
    fake_agent = root / 'ssh-agent'
    fake_add = root / 'ssh-add'
    fake_ssh_recorder = root / 'ssh-record'
    fake_agent.write_text('''#!/bin/sh
echo "AGENT:$*" >> "$TEST_AGENT_LOG"
if [ "$1" = -k ]; then
  echo "unset SSH_AUTH_SOCK; unset SSH_AGENT_PID; echo Agent pid $SSH_AGENT_PID killed;"
else
  echo "PROMPT:$KEYCHAIN_PKCS11_PROMPT<END>" >> "$TEST_AGENT_LOG"
  echo "SSH_AUTH_SOCK=/tmp/fake-agent.sock; export SSH_AUTH_SOCK;"
  echo "SSH_AGENT_PID=99999; export SSH_AGENT_PID;"
fi
''')
    fake_add.write_text('''#!/bin/sh
echo "ADD:$*" >> "$TEST_AGENT_LOG"
case "$1" in
  -L)
    echo "ecdsa-sha2-nistp256 AAAAOTHER other-key"
    echo "ecdsa-sha2-nistp256 AAAATARGET secure-ecdsa"
    echo "ssh-ed25519 AAAAED25519 secure-ed25519"
    ;;
  -d)
    removed="$(cat)"
    echo "REMOVED:$removed" >> "$TEST_AGENT_LOG"
    ;;
esac
''')
    fake_ssh_recorder.write_text('''#!/bin/sh
printf '%s\n' "$@" > "$TEST_SSH_ARGS_LOG"
if [ "$1" = -E ]; then
  printf '%s' "$TEST_SSH_DIAGNOSTIC" >> "$2"
  shift 2
fi
if [ "$MYSQL_TUNNEL_TEST_SSH_STATUS" != 0 ]; then
  exit "$MYSQL_TUNNEL_TEST_SSH_STATUS"
fi
while [ "$#" -gt 0 ]; do
  if [ "$1" = -L ]; then
    shift
    touch "${1%%:*}"
  fi
  shift
done
''')
    for executable in (fake_agent, fake_add, fake_ssh_recorder):
        executable.chmod(0o700)

    se_env = environment('se-ok', 0)
    se_env.update(
        SSH_CONNECT_SSH=str(fake_ssh_recorder),
        SSH_CONNECT_SSH_AGENT=str(fake_agent),
        SSH_CONNECT_SSH_ADD=str(fake_add),
        TEST_AGENT_LOG=str(agent_log),
        TEST_SSH_ARGS_LOG=str(ssh_args_log),
    )
    se_ok = subprocess.run([str(connect), '--secure-ecdsa-dir', str(se_dir), 'demo-host', 'true'],
                           env=se_env, capture_output=True, text=True)
    assert se_ok.returncode == 0, se_ok.stderr
    agent_lines = agent_log.read_text().splitlines()
    assert f'AGENT:-s -P {se_dir}/keychain-pkcs11.dylib' in agent_lines, agent_lines
    assert 'PROMPT:\n配置：demo-host\n连接方式：SSH\n服务器：demo-host<END>\n' in agent_log.read_text()
    assert f'ADD:-s {se_dir}/keychain-pkcs11.dylib' in agent_lines, agent_lines
    assert 'REMOVED:ecdsa-sha2-nistp256 AAAAOTHER other-key' in agent_lines, agent_lines
    assert not any('AAAATARGET' in line and line.startswith('REMOVED:') for line in agent_lines), agent_lines
    assert 'AGENT:-k' in agent_lines, agent_lines
    recorded_args = ssh_args_log.read_text().splitlines()
    assert 'IdentitiesOnly=yes' in recorded_args
    assert 'ForwardAgent=no' in recorded_args
    assert 'IdentityAgent=/tmp/fake-agent.sock' in recorded_args
    assert 'ControlPath=none' in recorded_args
    assert str(se_dir / 'id_ecdsa.pub') in recorded_args

    # Explicit --auth-reason and Ed25519 pubkey via --pkcs11-module/--pkcs11-pubkey.
    ed_pub = se_dir / 'id_ed25519.pub'
    ed_pub.write_text('ssh-ed25519 AAAAED25519 test@secure-ed25519\n')
    agent_log.unlink()
    ed_ok = subprocess.run(
        [str(connect), '--auth-reason', 'Deploy -> prod_gw',
         '--pkcs11-module', str(se_dir / 'keychain-pkcs11.dylib'),
         '--pkcs11-pubkey', str(ed_pub), 'deploy@prod_gw', 'true'],
        env=se_env, capture_output=True, text=True,
    )
    assert ed_ok.returncode == 0, ed_ok.stderr
    ed_lines = agent_log.read_text().splitlines()
    assert 'PROMPT:\n配置：prod_gw\n连接方式：SSH\n用户名：deploy\n服务器：prod_gw\n用途：Deploy -> prod_gw<END>\n' in agent_log.read_text()
    assert 'AGENT:-k' in ed_lines, ed_lines

    # Environment variable SSH_CONNECT_AUTH_REASON is also respected.
    agent_log.unlink()
    env_reason = dict(se_env, SSH_CONNECT_AUTH_REASON='EnvOp -> staging_gw')
    env_ok = subprocess.run([str(connect), '--secure-ecdsa-dir', str(se_dir), '-l', 'operator', 'staging_gw', 'true'],
                            env=env_reason, capture_output=True, text=True)
    assert env_ok.returncode == 0, env_ok.stderr
    assert 'PROMPT:\n配置：staging_gw\n连接方式：SSH\n用户名：operator\n服务器：staging_gw\n用途：EnvOp -> staging_gw<END>\n' in agent_log.read_text()

    # Rejected Touch ID under --secure-ecdsa-dir still returns 77 and kills the isolated agent
    # for both generic agent refusal (upstream/ed25519 branches) and explicit cancel text (patched branch).
    for cancel_diag in (
        agent_refusal,
        'sign_and_send_pubkey: signing failed for ECDSA "/tmp/secure-ecdsa/id_ecdsa.pub" from agent: user cancelled',
    ):
        agent_log.unlink()
        se_rej_env = dict(se_env, MYSQL_TUNNEL_TEST_SSH_STATUS='255', TEST_SSH_DIAGNOSTIC=cancel_diag + '\n')
        se_rej = subprocess.run([str(connect), '--secure-ecdsa-dir', str(se_dir), 'demo-host'],
                                env=se_rej_env, capture_output=True, text=True)
        assert se_rej.returncode == 77 and 'SSH_AUTH_REJECTED' in se_rej.stderr, se_rej.stderr
        assert 'AGENT:-k' in agent_log.read_text().splitlines()

    # Profile with pkcs11_dir passes MySQL prompt to isolated ssh-agent without mysqlsh and without ControlPath=none.
    agent_log.unlink()
    custom_env = environment('custom', 0, with_mysqlsh=False)
    custom_env.update(
        SSH_CONNECT_SSH=str(fake_ssh_recorder),
        SSH_CONNECT_SSH_AGENT=str(fake_agent),
        SSH_CONNECT_SSH_ADD=str(fake_add),
        TEST_AGENT_LOG=str(agent_log),
        TEST_SSH_ARGS_LOG=str(ssh_args_log),
    )
    custom_res = subprocess.run(
        ['/bin/zsh', '-f', '-c', function + '\nmysql_tunnel custom_se -e "SELECT 1"'],
        env=custom_env, capture_output=True, text=True,
    )
    assert custom_res.returncode == 0, custom_res.stderr
    custom_ssh_args = ssh_args_log.read_text().splitlines()
    assert 'ForwardAgent=no' in custom_ssh_args
    assert 'ControlPersist=no' in custom_ssh_args
    assert 'ControlPath=none' not in custom_ssh_args
    custom_agent_lines = agent_log.read_text().splitlines()
    assert 'PROMPT:\n配置：custom_se\n连接方式：MySQL SSH 隧道\n用户名：sshuser\n服务器：custom_gw\n数据库用户名：appuser\n数据库服务器：10.0.0.5:3306<END>\n' in agent_log.read_text()
    assert 'AGENT:-k' in custom_agent_lines, custom_agent_lines

    # mysqldump_tunnel passes MySQL Dump prompt to isolated ssh-agent.
    agent_log.unlink()
    dump_se_env = environment('custom-dump', 0, with_mysqlsh=False)
    dump_se_env.update(
        SSH_CONNECT_SSH=str(fake_ssh_recorder),
        SSH_CONNECT_SSH_AGENT=str(fake_agent),
        SSH_CONNECT_SSH_ADD=str(fake_add),
        TEST_AGENT_LOG=str(agent_log),
        TEST_SSH_ARGS_LOG=str(ssh_args_log),
    )
    dump_se_res = subprocess.run(
        ['/bin/zsh', '-f', '-c', function + '\nmysqldump_tunnel custom_se app_db'],
        env=dump_se_env, capture_output=True, text=True,
    )
    assert dump_se_res.returncode == 0, dump_se_res.stderr
    assert 'PROMPT:\n配置：custom_se\n连接方式：MySQL 导出 SSH 隧道\n用户名：sshuser\n服务器：custom_gw\n数据库用户名：appuser\n数据库服务器：10.0.0.5:3306<END>\n' in agent_log.read_text()
    assert 'AGENT:-k' in agent_log.read_text().splitlines()

    # mysql_tunnel --dump also passes MySQL Dump prompt to isolated ssh-agent.
    agent_log.unlink()
    sh_dump_se_env = environment('custom-sh-dump', 0, with_mysqlsh=True)
    sh_dump_se_env.update(
        SSH_CONNECT_SSH=str(fake_ssh_recorder),
        SSH_CONNECT_SSH_AGENT=str(fake_agent),
        SSH_CONNECT_SSH_ADD=str(fake_add),
        TEST_AGENT_LOG=str(agent_log),
        TEST_SSH_ARGS_LOG=str(ssh_args_log),
    )
    sh_dump_se_res = subprocess.run(
        ['/bin/zsh', '-f', '-c', function + '\nmysql_tunnel custom_se --dump app_db orders --outputUrl=/tmp/out --dryRun=true'],
        env=sh_dump_se_env, capture_output=True, text=True,
    )
    assert sh_dump_se_res.returncode == 0, sh_dump_se_res.stderr
    assert 'PROMPT:\n配置：custom_se\n连接方式：MySQL 导出 SSH 隧道\n用户名：sshuser\n服务器：custom_gw\n数据库用户名：appuser\n数据库服务器：10.0.0.5:3306<END>\n' in agent_log.read_text()
    assert 'AGENT:-k' in agent_log.read_text().splitlines()

    processes = []
    try:
        for name in ('first', 'second', 'third'):
            processes.append(subprocess.Popen(command, env=environment(name, hold=True, client='mysqlsh'),
                                              stdin=subprocess.DEVNULL,
                                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                              start_new_session=True))
        deadline = time.monotonic() + 10
        while not all((root / (name + '.started')).exists() for name in ('first', 'second', 'third')):
            assert time.monotonic() < deadline, 'Concurrent connections did not start'
            time.sleep(0.02)
        sockets = []
        for name in ('first', 'second', 'third'):
            arguments = json.loads((root / (name + '.started')).read_text())['argv']
            assert arguments[-2:] == ['-e', 'SELECT 1'], arguments
            sockets.append(Path(next(arg.split('=', 1)[1] for arg in arguments if arg.startswith('--socket='))))
        assert len(set(sockets)) == 3, 'Connections share a socket'
        (root / 'first.release').touch()
        _, errors = processes[0].communicate(timeout=10)
        assert processes[0].returncode == 0, errors
        assert not sockets[0].parent.exists(), 'First tunnel directory was not cleaned'
        assert (root / 'first.erased').exists(), 'First temporary credential was not erased'
        assert processes[1].poll() is None and sockets[1].exists(), 'First exit affected second connection'
        os.killpg(processes[1].pid, signal.SIGINT)
        _, errors = processes[1].communicate(timeout=10)
        assert processes[1].returncode in (130, -signal.SIGINT), errors
        assert not sockets[1].parent.exists(), 'SIGINT-interrupted tunnel was not cleaned'
        assert (root / 'second.erased').exists(), 'SIGINT-interrupted credential was not erased'
        assert processes[2].poll() is None and sockets[2].exists(), 'Earlier exits affected third connection'
        os.killpg(processes[2].pid, signal.SIGTERM)
        _, errors = processes[2].communicate(timeout=10)
        assert processes[2].returncode in (143, -signal.SIGTERM), errors
        assert not sockets[2].parent.exists(), 'SIGTERM-interrupted tunnel was not cleaned'
        assert (root / 'third.erased').exists(), 'SIGTERM-interrupted credential was not erased'
    finally:
        for process in processes:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                process.communicate(timeout=10)

    # Direct unit tests for mysql-tunnel-credentials.py: escaping, trailing newline preservation, and keychain: parsing.
    sec_args_log = root / 'sec-args.log'
    special_sec = root / 'security-special'
    special_sec.write_text('''#!/usr/bin/env python3
import os, sys
from pathlib import Path
Path(os.environ['TEST_SEC_ARGS']).write_text('\\n'.join(sys.argv[1:]))
sys.stdout.write(os.environ['TEST_SEC_OUT'])
''')
    special_sec.chmod(0o700)

    for source, expected_args, raw_out, expected_line in (
        ('keychain:svc-only', ['find-generic-password', '-w', '-s', 'svc-only'],
         'p\\a"s\ts\n', 'password="p\\\\a\\"s\\ts"'),
        ('keychain:svc:acct', ['find-generic-password', '-w', '-s', 'svc', '-a', 'acct'],
         'trail-nl\n\n', 'password="trail-nl\\n"'),
    ):
        cnf_out = root / (source.replace(':', '_') + '.cnf')
        cred_env = dict(
            os.environ,
            MYSQL_TUNNEL_KEYCHAIN_HELPER='',
            MYSQL_TUNNEL_SECURITY_BIN=str(special_sec),
            TEST_SEC_ARGS=str(sec_args_log),
            TEST_SEC_OUT=raw_out,
        )
        cred_res = subprocess.run(
            ['python3', str(credential_script), source, 'dest', 'mysql', str(cnf_out)],
            env=cred_env, capture_output=True, text=True,
        )
        assert cred_res.returncode == 0, cred_res.stderr
        assert sec_args_log.read_text().splitlines() == expected_args
        assert (cnf_out.stat().st_mode & 0o777) == 0o600
        assert cnf_out.read_text() == f'[client]\n{expected_line}\n', cnf_out.read_text()

print('PASS: mysql-only and optional mysqlsh routing; --dump/-d filtering; PKCS#11 agent; concurrent sessions and cleanup')
