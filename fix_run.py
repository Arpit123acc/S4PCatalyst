import json, pathlib

path = pathlib.Path('C:/Users/arpit.c.srivastava/Downloads/S4PC-Catalyst-v1.0/output/MM-RPT-0001/run.json')
data = json.loads(path.read_text(encoding='utf-8'))

data['status'] = 'completed'
data['checkpoint_request'] = None

for s in data['steps']:
    if s['n'] == 12:
        s['status'] = 'PASS'
        s['detail'] = (
            '10-package-summary.md written with: executive summary, 24-artifact inventory, '
            'ADT activation order (9 phases), transport strategy, full tenant verification '
            'checklist (TVC-01 to TVC-13), post-deployment steps (Comm Arrangement, Launchpad '
            'tiles, movement type verification, initial data). EXP-023 recorded. '
            'Developer accepted (CP3, 2026-07-20 18:57).'
        )

existing_cps = [a['checkpoint'] for a in data['human_approvals']]
cp3_variants = ['CP3 \u00b7 Acceptance', 'CP3 - Acceptance', 'CP3']
already_there = any(v in existing_cps for v in cp3_variants)
if not already_there:
    data['human_approvals'].append({
        'checkpoint': 'CP3 \u00b7 Acceptance',
        'decision': 'approved',
        'notes': '',
        'by': 'developer (webapp)',
        'date': '2026-07-20 18:57'
    })

path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
print('status:', data['status'])
print('step12:', next(s['status'] for s in data['steps'] if s['n'] == 12))
print('approvals:', len(data['human_approvals']))
print('checkpoint_request:', data['checkpoint_request'])
