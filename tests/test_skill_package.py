from pathlib import Path
import re
import shutil
import subprocess
import tempfile


project = Path(__file__).resolve().parents[1]
expected = {
    'connection-operation-review',
    'ssh-connection-check', 'mysql-tunnel', 'mysql-dump',
}
assert {path.name for path in (project / 'skills').iterdir()} == expected

with tempfile.TemporaryDirectory(prefix='ssh-skills-check-') as temporary:
    copied = Path(temporary) / 'skills'
    shutil.copytree(project / 'skills', copied)
    for name in expected:
        skill = copied / name
        assert (skill / 'agents/openai.yaml').is_file()
        content = (skill / 'SKILL.md').read_text()
        for link in re.findall(r'\]\(([^)]+)\)', content):
            if '://' not in link:
                assert (skill / link).exists(), f'{name} has a missing resource: {link}'
    script = copied / 'mysql-tunnel/scripts/mysql-tunnel.zsh'
    result = subprocess.run(
        ['/bin/zsh', '-f', '-c', 'source "$1"; print -r -- "$_MYSQL_TUNNEL_SCRIPT_DIR"', 'check', str(script)],
        check=True, capture_output=True, text=True,
    )
    assert Path(result.stdout.strip()).resolve() == script.parent.resolve(), 'Copied skill still depends on project path'

print(f'PASS: {len(expected)} portable skill directories; all resources resolve; copied runtime loads')
