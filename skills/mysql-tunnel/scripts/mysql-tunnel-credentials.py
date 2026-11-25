import json
import os
import shutil
import subprocess
import sys


def resolve_helper():
    env_helper = os.environ.get('MYSQL_TUNNEL_KEYCHAIN_HELPER')
    if env_helper is not None:
        if env_helper and os.path.isfile(env_helper) and os.access(env_helper, os.X_OK):
            return env_helper
        return None
    for candidate in (
        '/usr/local/mysql-shell/bin/mysql-secret-store-keychain',
        shutil.which('mysql-secret-store-keychain'),
    ):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def resolve_security():
    return os.environ.get('MYSQL_TUNNEL_SECURITY_BIN') or '/usr/bin/security'


def read_via_security(source):
    security = resolve_security()
    if not os.path.isfile(security) or not os.access(security, os.X_OK):
        return None
    if source.startswith('keychain:'):
        rest = source[len('keychain:'):]
        if ':' in rest:
            service, account = rest.split(':', 1)
            attempts = [([security, 'find-generic-password', '-w', '-s', service, '-a', account])]
        else:
            attempts = [([security, 'find-generic-password', '-w', '-s', rest])]
    else:
        attempts = [
            [security, 'find-generic-password', '-w', '-s', source],
            [security, 'find-generic-password', '-w', '-a', source],
        ]
    for command in attempts:
        result = subprocess.run(command, text=True, capture_output=True)
        if result.returncode == 0:
            return result.stdout.removesuffix('\n').removesuffix('\r')
    return None


def read_via_helper(helper, source):
    result = subprocess.run(
        [helper, 'get'],
        input=json.dumps({'SecretType': 'password', 'ServerURL': source}),
        text=True, capture_output=True,
    )
    if result.returncode:
        return None
    try:
        credential = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise SystemExit('Keychain helper returned invalid JSON for ' + source)
    if not isinstance(credential, dict) or not isinstance(credential.get('Secret'), str):
        raise SystemExit('Saved database credential is missing a password for ' + source)
    return credential['Secret']


def read_password(source, helper):
    if source.startswith('keychain:'):
        password = read_via_security(source)
        if password is not None:
            return password
        raise SystemExit('Could not read the saved database password from macOS Keychain for ' + source)
    if helper:
        password = read_via_helper(helper, source)
        if password is not None:
            return password
    password = read_via_security(source)
    if password is not None:
        return password
    raise SystemExit('Could not read the saved database password for ' + source)


def write_defaults_file(defaults_file, password):
    # MySQL option-file quoting; password never enters argv or the environment.
    for old, new in [('\\', '\\\\'), ('"', '\\"'), ('\n', '\\n'), ('\r', '\\r'), ('\t', '\\t'), ('\b', '\\b')]:
        password = password.replace(old, new)
    fd = os.open(defaults_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write('[client]\npassword="' + password + '"\n')


def main():
    helper = resolve_helper()

    if len(sys.argv) == 3 and sys.argv[1] == '--erase':
        destination = sys.argv[2]
        if not helper:
            raise SystemExit(0)
        result = subprocess.run(
            [helper, 'erase'],
            input=json.dumps({'SecretType': 'password', 'ServerURL': destination}),
            text=True, capture_output=True,
        )
        raise SystemExit(result.returncode)

    if len(sys.argv) != 5:
        raise SystemExit('Usage: mysql-tunnel-credentials.py SOURCE DESTINATION MODE DEFAULTS_FILE | --erase DESTINATION')

    source, destination, mode, defaults_file = sys.argv[1:]
    password = read_password(source, helper)

    if mode in ('mysqlsh', 'query'):
        if not helper:
            raise SystemExit('mysql-secret-store-keychain is required for mysqlsh mode')
        result = subprocess.run(
            [helper, 'store'],
            input=json.dumps({'SecretType': 'password', 'ServerURL': destination, 'Secret': password}),
            text=True, capture_output=True,
        )
        if result.returncode:
            raise SystemExit('Could not prepare the database credential for this session')
    else:
        write_defaults_file(defaults_file, password)


if __name__ == '__main__':
    main()
