#!/usr/bin/env python3
"""Configure existing OneColleague jobs. --apply pauses jobs; --resume restores triggers.

Uses teamcity-cli authentication. Saves complete changed sections before applying.
Never starts a release or creates a Git tag. Run from this repository root.
"""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

CORE_ROOT = 'OneColleague_Core_HttpsCodeupAliyunCom683925d84a97520ddedc3dfcOneColleagueOneColleagueGitRefsHeadsOneColleague'
JOBS = ['OneColleague_Core_Build', 'OneColleague_Core_Release',
        'OneColleague_Application_Build', 'OneColleague_Application_Release',
        'OneColleague_Publish_Test', 'OneColleague_Publish_Production']
BASE_URL = 'https://dongdongkc.shierkeji.com:5202/updates/'


def api(endpoint, data=None, method='GET'):
    args = ['teamcity', 'api', '/app/rest/' + endpoint, '--raw']
    with tempfile.TemporaryDirectory(prefix='onecolleague-api-') as folder:
        if data is not None:
            body = Path(folder) / 'body'
            body.write_text(data if isinstance(data, str) else json.dumps(data), encoding='utf-8')
            args += ['-X', method, '-H', 'Content-Type: ' + ('text/plain' if isinstance(data, str) else 'application/json'), '--input', str(body)]
        result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8')
        if result.returncode:
            raise RuntimeError(result.stderr)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return result.stdout.strip()


def props(**values):
    return {'property': [{'name': key, 'value': value} for key, value in values.items()]}


def step(name, script):
    return {'name': name, 'type': 'simpleRunner', 'properties': props(**{
        'use.custom.script': 'true', 'script.content': script, 'teamcity.step.mode': 'default'})}


def apply(backup):
    backup.mkdir(parents=True, exist_ok=False)
    for job in JOBS:
        state = api(f'buildTypes/id:{job}?fields=id,paused')
        (backup / f'{job}.state.json').write_text(json.dumps(state), encoding='utf-8')
        for section in ['steps', 'settings', 'triggers', 'snapshot-dependencies', 'artifact-dependencies', 'parameters']:
            (backup / f'{job}.{section}.json').write_text(json.dumps(api(f'buildTypes/id:{job}/{section}')), encoding='utf-8')
    (backup / 'core-branch-spec.txt').write_text(api(f'vcs-roots/id:{CORE_ROOT}/properties/branchSpec'), encoding='utf-8')
    for job in JOBS:
        api(f'buildTypes/id:{job}/paused', 'true', 'PUT')
    api(f'vcs-roots/id:{CORE_ROOT}/properties/branchSpec', '+:refs/heads/OneColleague\n+:refs/tags/(onecolleague-v*)', 'PUT')
    for release in [False, True]:
        suffix = 'Release' if release else 'Build'
        core = f'OneColleague_Core_{suffix}'
        app = f'OneColleague_Application_{suffix}'
        script = 'set -eu\nexport BUILD_ID="%teamcity.build.id%"\nexport EXPECTED_COMMIT="%build.vcs.number%"\n'
        script += 'export RELEASE_TAG="%teamcity.build.branch%"\n' if release else 'export RELEASE_TAG=""\n'
        script += 'bash .teamcity/core-build.sh\n'
        api(f'buildTypes/id:{core}/steps', {'step': [step('Build native Windows core', script)]}, 'PUT')
        api(f'buildTypes/id:{core}/settings/artifactRules', 'dist/*.zip => .\ndist/core-release-manifest.json => .', 'PUT')
        api(f'buildTypes/id:{core}/settings/executionTimeoutMin', '120', 'PUT')
        api(f'buildTypes/id:{core}/parameters/onecolleague.version', 'pending', 'PUT')
        api(f'buildTypes/id:{core}/parameters/onecolleague.coreCommit', 'pending', 'PUT')
        api(f'buildTypes/id:{core}/triggers', {'trigger': [{'type': 'vcsTrigger', 'properties': props(branchFilter='+:onecolleague-v*' if release else '+:<default>')}]}, 'PUT')
        api(f'buildTypes/id:{app}/artifact-dependencies', {'artifact-dependency': [{
            'type': 'artifact_dependency', 'source-buildType': {'id': core},
            'properties': props(cleanDestinationDirectory='true',
                pathRules='+:onecolleague-windows-standalone.zip => core-artifacts/\n+:core-release-manifest.json => core-artifacts/',
                revisionName='sameChainOrLastFinished', revisionValue='latest.sameChainOrLastFinished')}]}, 'PUT')
        # Even if TeamCity falls back in artifact resolution, prepare-core checks the
        # snapshot build ID and commit. A different build can never be packaged.
        snapshot = api(f'buildTypes/id:{app}/snapshot-dependencies')
        for dependency in snapshot.get('snapshot-dependency', []):
            for prop in dependency.get('properties', {}).get('property', []):
                if prop['name'] in ['run-build-if-dependency-failed', 'run-build-if-dependency-failed-to-start']:
                    prop['value'] = 'MAKE_FAILED_TO_START'
        api(f'buildTypes/id:{app}/snapshot-dependencies', snapshot, 'PUT')
        script = f'''set -eu
export CORE_VERSION="%dep.{core}.onecolleague.version%"
export CORE_COMMIT="%dep.{core}.onecolleague.coreCommit%"
export CORE_BUILD_ID="%dep.{core}.teamcity.build.id%"
export BUILD_ID="%teamcity.build.id%"
bash scripts/teamcity-application.sh
'''
        api(f'buildTypes/id:{app}/steps', {'step': [step('Stage exact core and build NSIS', script)]}, 'PUT')
        api(f'buildTypes/id:{app}/settings/artifactRules', 'src-tauri/target/x86_64-pc-windows-msvc/release/bundle/nsis/** => onecolleague_windows.zip\nrelease-manifest.json', 'PUT')
        api(f'buildTypes/id:{app}/parameters/env.ONECOLLEAGUE_UPDATE_MANIFEST_URL', BASE_URL + ('OneColleague' if release else 'OneColleague_test') + '/version.json', 'PUT')
        triggers = api(f'buildTypes/id:{app}/triggers')
        for trigger in triggers.get('trigger', []):
            if trigger['type'] == 'buildDependencyTrigger':
                for prop in trigger['properties']['property']:
                    if prop['name'] == 'branchFilter':
                        prop['value'] = '+:onecolleague-v*' if release else '+:<default>'
        api(f'buildTypes/id:{app}/triggers', triggers, 'PUT')
    for job in ['OneColleague_Publish_Test', 'OneColleague_Publish_Production']:
        source = 'OneColleague_Application_Build' if job.endswith('_Test') else 'OneColleague_Application_Release'
        if job.endswith('_Test'):
            api(f'buildTypes/id:{job}/snapshot-dependencies', {'snapshot-dependency': [{
                'type': 'snapshot_dependency', 'source-buildType': {'id': source},
                'properties': props(**{'run-build-if-dependency-failed':'MAKE_FAILED_TO_START',
                    'run-build-if-dependency-failed-to-start':'MAKE_FAILED_TO_START',
                    'sync-revisions':'true', 'take-successful-builds-only':'true'})}]}, 'PUT')
            api(f'buildTypes/id:{job}/artifact-dependencies', {'artifact-dependency': [{
                'type': 'artifact_dependency', 'source-buildType': {'id': source},
                'properties': props(cleanDestinationDirectory='false',
                    pathRules='+:onecolleague_windows.zip!** => .\n+:release-manifest.json => .',
                    revisionName='sameChainOrLastFinished', revisionValue='latest.sameChainOrLastFinished')}]}, 'PUT')
        steps = api(f'buildTypes/id:{job}/steps')
        for item in steps.get('step', []):
            for prop in item['properties']['property']:
                if prop['name'] == 'script.content':
                    guard = '''set -eu
python3 - <<'PY'
import json
m = json.load(open('release-manifest.json', encoding='utf-8'))
assert str(m['buildId']) == '%dep.OneColleague_Application_Release.teamcity.build.id%', 'installer is not from snapshot build'
PY
'''
                    prop['value'] = guard.replace('OneColleague_Application_Release', source) + prop['value']
        api(f'buildTypes/id:{job}/steps', steps, 'PUT')
    print(f'Configured paused jobs. Backup: {backup}. Validate branch builds before --resume.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--resume', action='store_true')
    parser.add_argument('--backup-dir', type=Path)
    args = parser.parse_args()
    if args.apply:
        if not args.backup_dir:
            parser.error('--apply requires a new --backup-dir outside the repository')
        apply(args.backup_dir)
    elif args.resume:
        for job in JOBS:
            api(f'buildTypes/id:{job}/paused', 'false', 'PUT')
        print('Resumed migration jobs; no release was queued.')
    else:
        print('Jobs:', ', '.join(JOBS))
        print('Branch: OneColleague; production tags: onecolleague-v*; Windows x64 only.')


if __name__ == '__main__':
    main()
