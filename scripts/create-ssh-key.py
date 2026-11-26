#!/usr/bin/env python3
"""Create a file-backed or macOS Secure Enclave SSH key pair."""

import argparse
import filecmp
import getpass
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile


SSH_KEYGEN = '/usr/bin/ssh-keygen'
SC_AUTH = '/usr/sbin/sc_auth'
APPLE_PROVIDER = '/usr/lib/ssh-keychain.dylib'


def ctk_identities():
    result = subprocess.run(
        [SC_AUTH, 'list-ctk-identities', '-t', 'sha1', '-e', 'hex'],
        check=True, capture_output=True, text=True,
    )
    identities = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 3 and re.fullmatch(r'[0-9a-fA-F]{40}', fields[1]):
            identities[fields[1]] = (fields[0], fields[2])
    return identities


def pkcs11_public_keys(module_path):
    result = subprocess.run(
        [SSH_KEYGEN, '-D', str(module_path)],
        check=True, capture_output=True, text=True,
    )
    keys = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 2:
            keys.add((fields[0], fields[1]))
    return keys


def _create_ctk_identity(label):
    before = ctk_identities()
    subprocess.run(
        [SC_AUTH, 'create-ctk-identity', '-l', label, '-k', 'p-256-ne', '-t', 'bio'],
        check=True,
    )
    after = ctk_identities()
    created = after.keys() - before.keys()
    if len(created) != 1:
        details = ', '.join(sorted(created)) or 'none'
        raise RuntimeError(
            f'Cannot uniquely identify the new CTK key (created: {details}). '
            'Inspect sc_auth list-ctk-identities before retrying.'
        )
    key_hash = created.pop()
    if after[key_hash] != ('p-256-ne', 'bio'):
        raise RuntimeError(
            f'The new CTK key {key_hash} is not non-exportable and biometric-protected '
            f'(got {after[key_hash]}).'
        )
    print('Created CTK identity ' + key_hash, flush=True)
    return key_hash


def create_pair(args, directory, comment):
    generated = directory / 'key'
    if args.type == 'ed25519':
        command = [SSH_KEYGEN, '-q', '-t', 'ed25519', '-f', str(generated), '-C', comment]
        if not args.prompt_passphrase:
            command.extend(['-N', ''])
        subprocess.run(command, check=True)
        return generated, Path(str(generated) + '.pub')

    if args.type == 'secure-enclave-ecdsa':
        module_path = Path(args.pkcs11_module).expanduser().absolute()
        before_ctk = ctk_identities()
        before_keys = pkcs11_public_keys(module_path) if before_ctk else set()
        key_hash = _create_ctk_identity(args.label)
        after_keys = pkcs11_public_keys(module_path)
        new_keys = after_keys - before_keys
        if len(new_keys) != 1:
            raise RuntimeError(
                f'Expected exactly one new PKCS#11 public key for CTK identity {key_hash} '
                f'(found {len(new_keys)}). Inspect ssh-keygen -D {module_path}.'
            )
        key_type, key_blob = new_keys.pop()
        if key_type != 'ecdsa-sha2-nistp256':
            raise RuntimeError(f'Unexpected PKCS#11 ECDSA public key type: {key_type}')
        public = directory / 'id_ecdsa.pub'
        public.write_text(f'{key_type} {key_blob} {comment}\n')
        return None, public

    key_hash = _create_ctk_identity(args.label)
    environment = dict(os.environ, KEYCHAIN_CERTIFICATES=key_hash)
    subprocess.run(
        [SSH_KEYGEN, '-w', APPLE_PROVIDER, '-K', '-N', ''],
        cwd=directory, env=environment, check=True,
    )
    public_files = list(directory.glob('*.pub'))
    if len(public_files) != 1:
        raise RuntimeError(
            f'Expected exactly one downloaded key handle for CTK identity {key_hash}. '
            'The CTK identity remains available through sc_auth.'
        )
    public = public_files[0]
    fields = public.read_text().split()
    if len(fields) < 2 or fields[0] != 'sk-ecdsa-sha2-nistp256@openssh.com':
        raise RuntimeError('Unexpected Secure Enclave SSH public key format.')
    public.write_text(f'{fields[0]} {fields[1]} {comment}\n')
    return public.with_suffix(''), public


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--type',
        choices=('ed25519', 'secure-enclave', 'secure-enclave-ecdsa'),
        default='ed25519',
    )
    parser.add_argument('--file', required=True, help='Destination private-key, key-handle, or public-key path')
    parser.add_argument('--comment', help='Public-key comment')
    parser.add_argument('--label', help='CTK identity label, required for secure-enclave modes')
    parser.add_argument('--pkcs11-module', help='PKCS#11 module path, required for secure-enclave-ecdsa')
    parser.add_argument('--prompt-passphrase', action='store_true', help='Let ssh-keygen prompt for an Ed25519 passphrase')
    parser.add_argument('--archive-dir', help='Empty directory for existing key files before replacement')
    args = parser.parse_args()

    if args.type == 'secure-enclave':
        if not args.label or args.prompt_passphrase or args.pkcs11_module:
            parser.error('secure-enclave requires --label and does not accept --prompt-passphrase or --pkcs11-module')
    elif args.type == 'secure-enclave-ecdsa':
        if not args.label or not args.pkcs11_module or args.prompt_passphrase:
            parser.error('secure-enclave-ecdsa requires --label and --pkcs11-module and does not accept --prompt-passphrase')
        module_path = Path(args.pkcs11_module).expanduser().absolute()
        if not module_path.is_file() or not os.access(module_path, os.R_OK):
            parser.error(f'PKCS#11 module must be a readable file: {module_path}')
    else:
        if args.label or args.pkcs11_module:
            parser.error('--label and --pkcs11-module apply only to secure-enclave modes')

    raw_target = Path(args.file).expanduser().absolute()
    if args.type == 'secure-enclave-ecdsa':
        public = raw_target if raw_target.suffix == '.pub' else Path(str(raw_target) + '.pub')
        target = None
        pair = (public,)
    else:
        target = raw_target
        public = Path(str(target) + '.pub')
        pair = (target, public)

    if any(path.is_symlink() or (path.exists() and not path.is_file()) for path in pair):
        parser.error('Destination paths must be regular files or absent')
    existing = [path.exists() for path in pair]
    if any(existing) and not all(existing):
        parser.error('Existing key pair is incomplete; resolve it before replacement')
    if all(existing) and not args.archive_dir:
        parser.error('Destination already exists; supply --archive-dir to preserve it before replacement')
    archive = Path(args.archive_dir).expanduser().absolute() if args.archive_dir else None
    if archive:
        if not all(existing):
            parser.error('--archive-dir requires existing key files')
        if archive.is_symlink() or (archive.exists() and (not archive.is_dir() or any(archive.iterdir()))):
            parser.error('Archive directory must be absent or empty')

    comment = args.comment or f'{getpass.getuser()}@{platform.node()}'
    if '\n' in comment or '\r' in comment:
        parser.error('Comment must fit on one line')

    archive_existed = bool(archive and archive.exists())
    archived_files = []
    if archive:
        # Prepare and verify archive before creating any CTK identity so archive failures leave no orphan key.
        archive.mkdir(mode=0o700, parents=True, exist_ok=True)
        archive.chmod(0o700)
        try:
            for path in pair:
                archived_path = archive / path.name
                shutil.copy2(path, archived_path)
                archived_files.append(archived_path)
                archived_path.chmod(0o600 if path == target else 0o644)
                if not filecmp.cmp(path, archived_path, shallow=False):
                    raise RuntimeError('Archive verification failed; original files were retained')
        except Exception:
            for archived_path in archived_files:
                archived_path.unlink(missing_ok=True)
            if not archive_existed and archive.exists() and not any(archive.iterdir()):
                archive.rmdir()
            raise

    public.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix='.ssh-key-', dir=public.parent) as temporary:
            private_source, public_source = create_pair(args, Path(temporary), comment)
            if private_source is not None:
                private_source.chmod(0o600)
            public_source.chmod(0o644)
            if not archive and any(path.exists() for path in pair):
                raise RuntimeError('Destination appeared during generation; refusing to overwrite it')
            replacements = [(public_source, public)]
            if private_source is not None and target is not None:
                replacements.insert(0, (private_source, target))
            published = []
            try:
                for source, destination in replacements:
                    os.replace(source, destination)
                    published.append(destination)
            except OSError:
                for destination in published:
                    if archive:
                        shutil.copy2(archive / destination.name, destination)
                    else:
                        destination.unlink(missing_ok=True)
                raise
    except Exception:
        if archive:
            for archived_path in archived_files:
                archived_path.unlink(missing_ok=True)
            if not archive_existed and archive.exists() and not any(archive.iterdir()):
                archive.rmdir()
        raise

    if target is not None:
        print('Private key or handle: ' + str(target))
    print('Public key: ' + str(public))
    if archive:
        print('Previous key files: ' + str(archive))
    subprocess.run([SSH_KEYGEN, '-lf', str(public)], check=True)


if __name__ == '__main__':
    main()
