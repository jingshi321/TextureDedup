"""Explicit, previewed recycle-bin operations; never permanently delete files."""
import hashlib, json, os, struct, time
from pathlib import Path
from PIL import Image
from engine import SAVE

ALLOWED={'文件完全重复','RGBA 像素重复','等比例缩放候选','非等比例拉伸候选'}
SIMILAR={'外观相似','AI 近似'}

def make_plan(result,selections,scope=None):
    if not selections: raise ValueError('请先选择要处理的组')
    groups={g['id']:g for g in result['groups']}; selected=[]
    for selection in selections:
        if scope is not None and int(selection['group']) not in scope: continue
        g=groups[int(selection['group'])]
        if g['kind'] not in ALLOWED|SIMILAR: raise ValueError('不支持的分组类型')
        k=int(selection['keep'])
        if k not in g['ids']: raise ValueError('保留文件不属于该组')
        largest=max(result['records'][i]['size'] for i in g['ids'])
        if result['records'][k]['size']!=largest: raise ValueError('必须选择该组字节数最大的文件保留')
        selected.append((g,k))
    if not selected: raise ValueError('请先选择当前筛选下要处理的组')

    # A file marked unselected by any selected group is a deletion target.
    keep=set(); candidates=set()
    for g,k in selected:
        keep.add(k)
        candidates.update(i for i in g['ids'] if i!=k)
    keep-=candidates
    items=[dict(result['records'][i],index=i) for i in sorted(candidates)]
    if len({os.path.normcase(r['path']) for r in items})!=len(items): raise ValueError('重复路径，请重新扫描')
    return dict(items=items,keepers=[dict(result['records'][i],index=i) for i in sorted(keep)],bytes=sum(r['size'] for r in items))

def prune_result(result,completed):
    removed_paths={os.path.normcase(path) for path in completed}
    removed={i for i,row in enumerate(result['records'])
             if row is not None and os.path.normcase(row['path']) in removed_paths}
    if not removed: return result
    # Keep image and group IDs stable so remembered choices cannot target another file.
    records=[None if i in removed else row for i,row in enumerate(result['records'])]
    groups=[]
    for group in result['groups']:
        ids=[i for i in group['ids'] if records[i] is not None]
        if len(ids)<2: continue
        keep=group.get('keep')
        if keep not in ids:
            keep=max(ids,key=lambda i:(records[i]['size'],-i))
        groups.append(dict(group,ids=ids,keep=keep))
    scanned=max(0,result.get('scanned',sum(row is not None for row in result['records']))-len(removed))
    return dict(result,records=records,groups=groups,scanned=scanned,
                revision=result.get('revision',0)+1)

def verify_stat(row):
    p=Path(row['path'])
    if p.is_symlink() or not p.is_file(): raise ValueError('文件已不存在或为链接：'+str(p))
    s=p.stat()
    if (s.st_size,s.st_mtime_ns)!=(row['size'],row['mtime']): raise ValueError('文件已变化，请重新扫描：'+str(p))
    return p,s

def verify(row):
    p,s=verify_stat(row)
    if not row.get('filehash'):
        if not row.get('pixelhash'): raise ValueError('缺少内容校验值，请重新扫描：'+str(p))
        with Image.open(p) as source:
            if getattr(source,'n_frames',1)>1: raise ValueError('文件已变化，请重新扫描：'+str(p))
            image=source.convert('RGBA')
            current=hashlib.sha256(struct.pack('<II',*image.size))
            current.update(image.tobytes())
        if current.hexdigest()!=row['pixelhash']:
            raise ValueError('内容已变化，请重新扫描：'+str(p))
    else:
        h=hashlib.sha256()
        with p.open('rb') as f:
            for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
        if h.hexdigest()!=row['filehash']:
            raise ValueError('内容已变化，请重新扫描：'+str(p))
    after=p.stat()
    if (after.st_size,after.st_mtime_ns)!=(s.st_size,s.st_mtime_ns):
        raise ValueError('内容已变化，请重新扫描：'+str(p))

def execute(plan,recycle=None,progress=None):
    if recycle is None:
        from send2trash import send2trash
        recycle=send2trash
    rows=plan['keepers']+plan['items']; total=len(rows)+len(plan['items'])
    if progress: progress('正在核对文件',0,total)
    # Validate every keeper and target before the first operation.
    for i,r in enumerate(rows,1):
        verify(r)
        if progress: progress('正在核对文件',i,total)
    journal=SAVE/('recycle-'+str(time.time_ns())+'.jsonl')
    completed=[]; errors=[]
    with journal.open('a',encoding='utf-8') as log:
        if progress: progress('正在移入回收站',len(rows),total)
        for start in range(0,len(plan['items']),64):
            batch=plan['items'][start:start+64]; paths=[r['path'] for r in batch]
            try:
                for r in batch: verify_stat(r)
            except Exception as e:
                errors.append(str(e)); break
            for path in paths: log.write(json.dumps(dict(event='before',path=path),ensure_ascii=False)+'\n')
            log.flush()
            try:
                recycle(paths)
            except Exception as e:
                errors.append(str(e))
            moved={path for path in paths if not Path(path).exists()}
            completed.extend(moved)
            if len(moved)!=len(paths) and not errors:
                errors.append('回收站未能移入本批全部文件，剩余文件已保留')
            for path in paths:
                if path in moved: log.write(json.dumps(dict(event='recycled',path=path),ensure_ascii=False)+'\n')
            log.flush()
            if progress: progress('正在移入回收站',len(rows)+len(completed),total)
            if errors: break
    return dict(completed=completed,errors=errors,journal=str(journal))
