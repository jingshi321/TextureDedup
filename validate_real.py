import sys
import os
sys.dont_write_bytecode=True
from engine import *
import shutil, threading

folder=SAVE/'real-sample'; folder.mkdir(exist_ok=True)
source_value=os.environ.get('TEXTUREDEDUP_TEST_IMAGE_DIR')
if not source_value or not Path(source_value).is_dir():
    raise SystemExit('Set TEXTUREDEDUP_TEST_IMAGE_DIR to a local image folder before running this optional test.')
source=Path(source_value)
count=0
for path in source.rglob('*.png'):
    if count>=150: break
    target=folder/path.relative_to(source); target.parent.mkdir(parents=True,exist_ok=True)
    if not target.exists(): shutil.copyfile(path,target)
    count+=1
started=time.perf_counter()
result=scan([str(folder)],dict(similar=True,ai=True,alpha=True,distance=6,color=.08,threshold=.97),lambda *a:None,threading.Event(),SAVE/'real-test-index.sqlite')
(SAVE/'real-validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(dict(images=len(result['records']),groups=len(result['groups']),errors=result['errors'],seconds=round(time.perf_counter()-started,2)),ensure_ascii=True))
