import json,re
from pathlib import Path
from urllib.parse import unquote
root=Path.cwd()
files=[root/'docs/evaluation.md',root/'evals/README.md',root/'progress/evaluation-results-2026-10-26.md',root/'progress/evaluation-2026-10-26.md']
errors=[]
links=0
for file in files:
    text=file.read_text(encoding='utf-8')
    for target in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)',text):
        if '://' in target or target.startswith('#'): continue
        target=unquote(target.split('#')[0])
        links+=1
        if not (file.parent/target).exists(): errors.append(str(file.relative_to(root))+': '+target)
text=(root/'docs/evaluation.md').read_text(encoding='utf-8')
body=re.search(r'```mermaid\n(.*?)```',text,re.S).group(1).strip()
assert body==(root/'docs/diagrams/evaluation.mmd').read_text(encoding='utf-8').strip()
for name in ['live-core-summary.json','live-analysis.json','live-core-report.html','live-core-console.txt','live-core-trials.csv','coding-final.json']:
    content=(root/'progress/evidence/2026-10-26'/name).read_text(encoding='utf-8-sig')
    if re.search(r'(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{12,}',content): errors.append(name+': token-like content')
    if str(root).lower() in content.lower() or 'authorization' in content.lower(): errors.append(name+': private metadata')
analysis=json.loads((root/'progress/evidence/2026-10-26/live-analysis.json').read_text(encoding='utf-8'))
assert analysis['total_estimated_cny'] < 5 and not analysis['core_close_warning_observed']
assert not analysis['working_tree_dirty_at_run_start']
result={'documents':len(files),'local_links_checked':links,'mermaid_source_synchronized':True,'public_attachments_checked':6,'budget_and_warning_checks_passed':True,'errors':errors}
(root/'progress/evidence/2026-10-26/e4-artifact-check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
assert not errors
