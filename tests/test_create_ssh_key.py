from contextlib import redirect_stdout
from io import StringIO
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import tempfile
from unittest.mock import patch


project = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
script = project / 'scripts/create-ssh-key.py'
spec = importlib.util.spec_from_file_location('create_ssh_key', script)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

with tempfile.TemporaryDirectory(prefix='ssh-key-check-') as temporary:
    root = Path(temporary)
    target = root / 'id_ed25519'
    public = root / 'id_ed25519.pub'
    archive = root / 'legacy-ed25519'
    command = ['python3', str(script), '--file', str(target), '--comment', 'test@local']
    first = subprocess.run(command, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    original = target.read_bytes()
    original_public = public.read_bytes()
    derived = subprocess.check_output(['/usr/bin/ssh-keygen', '-y', '-P', '', '-f', str(target)], text=True)
    assert derived.split()[:2] == public.read_text().split()[:2]
    assert target.stat().st_mode & 0o777 == 0o600

    refused = subprocess.run(command, capture_output=True, text=True)
    assert refused.returncode != 0
    assert target.read_bytes() == original
    replaced = subprocess.run(command + ['--archive-dir', str(archive)], capture_output=True, text=True)
    assert replaced.returncode == 0, replaced.stderr
    assert (archive / target.name).read_bytes() == original
    assert (archive / public.name).read_bytes() == original_public
    assert target.read_bytes() != original
    assert archive.stat().st_mode & 0o777 == 0o700

    before = {'1' * 40: ('p-256-ne', 'bio')}
    after = dict(before, **{'2' * 40: ('p-256-ne', 'bio')})
    calls = []
    generated = root / 'hardware'
    generated.mkdir()

    def native(command, **options):
        calls.append((command, options))
        if command[0] == module.SSH_KEYGEN:
            assert options['env']['KEYCHAIN_CERTIFICATES'] == '2' * 40
            (generated / 'id_ecdsa_sk_rk_label').write_text('test handle')
            (generated / 'id_ecdsa_sk_rk_label.pub').write_text(
                'sk-ecdsa-sha2-nistp256@openssh.com AAAATEST ssh:\n')
        return subprocess.CompletedProcess(command, 0)

    arguments = SimpleNamespace(type='secure-enclave', label='test-label')
    with patch.object(module, 'ctk_identities', side_effect=[before, after]), \
            patch.object(module.subprocess, 'run', side_effect=native), \
            redirect_stdout(StringIO()):
        private_handle, public_handle = module.create_pair(arguments, generated, 'test@secure-enclave')
    assert private_handle.exists()
    assert public_handle.read_text().endswith(' test@secure-enclave\n')
    assert calls[0][0][-4:] == ['-k', 'p-256-ne', '-t', 'bio']

    ecdsa_dir = root / 'hardware-ecdsa'
    ecdsa_dir.mkdir()
    fake_module = root / 'keychain-pkcs11.dylib'
    fake_module.write_bytes(b'fake-pkcs11')
    before_pubkeys = {('ecdsa-sha2-nistp256', 'AAAAEXISTING')}
    after_pubkeys = before_pubkeys | {('ecdsa-sha2-nistp256', 'AAAANEWKEY')}
    ecdsa_calls = []

    def ecdsa_run(command, **options):
        ecdsa_calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    ecdsa_args = SimpleNamespace(
        type='secure-enclave-ecdsa',
        label='secure-ecdsa-test',
        pkcs11_module=str(fake_module),
    )
    with patch.object(module, 'ctk_identities', side_effect=[before, before, after]), \
            patch.object(module, 'pkcs11_public_keys', side_effect=[before_pubkeys, after_pubkeys]), \
            patch.object(module.subprocess, 'run', side_effect=ecdsa_run), \
            redirect_stdout(StringIO()):
        priv, pub = module.create_pair(ecdsa_args, ecdsa_dir, 'test@secure-ecdsa')
    assert priv is None
    assert pub.read_text() == 'ecdsa-sha2-nistp256 AAAANEWKEY test@secure-ecdsa\n'
    assert ecdsa_calls == [[module.SC_AUTH, 'create-ctk-identity', '-l', 'secure-ecdsa-test', '-k', 'p-256-ne', '-t', 'bio']]

    # Archive verification must run before create_pair so archive failures never create orphan CTK keys.
    bad_archive = root / 'bad-archive'
    create_called = []

    def fail_if_called(*_args, **_kwargs):
        create_called.append(True)
        raise AssertionError('create_pair should not run when archive verification fails')

    with patch.object(sys, 'argv', [
        'create-ssh-key.py', '--file', str(target), '--archive-dir', str(bad_archive),
    ]), patch.object(module.filecmp, 'cmp', return_value=False), \
            patch.object(module, 'create_pair', side_effect=fail_if_called):
        try:
            module.main()
            raise AssertionError('Expected RuntimeError on failed archive verification')
        except RuntimeError as exc:
            assert 'Archive verification failed' in str(exc)
    assert not create_called
    assert not bad_archive.exists()

print('PASS: Ed25519 generation and archive; overwrite refusal; Secure Enclave SK and PKCS#11 ECDSA routing')
