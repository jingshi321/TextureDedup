"""Previewed texture moves, preserving paths and never replacing targets."""
import os, errno, hashlib, json, shutil, time
from collections import Counter
from pathlib import Path
from engine import SAVE, EXTENSIONS
from vfx_filter import classify_texture, texture_keywords

def linked(path):
    return path.is_symlink() or (hasattr(path,'is_junction') and path.is_junction())

def roots(source,target):
    if not isinstance(source,str) or not source.strip() or not isinstance(target,str) or not target.strip():
        raise ValueError('请选择来源文件夹和目标文件夹')
    src=Path(source).expanduser().resolve();dst=Path(target).expanduser().resolve()
    if not src.is_dir(): raise ValueError('来源文件夹不存在')
    if dst==src or dst.is_relative_to(src) or src.is_relative_to(dst):
        raise ValueError('来源与目标必须互不包含，请选择独立的目标文件夹')
    if src==SAVE or src.is_relative_to(SAVE) or dst==SAVE or dst.is_relative_to(SAVE):
        raise ValueError('请选择软件 save 缓存目录之外的贴图文件夹')
    if dst.exists() and not dst.is_dir(): raise ValueError('目标不是文件夹')
    return src,dst

def target_safe(path,root):
    if not path.is_relative_to(root): raise ValueError('目标超出指定文件夹')
    for parent in (path,*path.parents):
        if linked(parent): raise ValueError('目标路径包含链接：'+str(parent))
        if parent==root: break
    if path.resolve()!=path: raise ValueError('目标路径已变化：'+str(path))

def select_textures(source,vfx_only=False):
    if not isinstance(source,str) or not source.strip(): raise ValueError('请选择来源文件夹')
    src=Path(source).expanduser().resolve();items=[];keyword_items=[];counts={'excluded':0,'unknown':0};keyword_counts=Counter()
    if not src.is_dir(): raise ValueError('来源文件夹不存在')
    if src==SAVE or src.is_relative_to(SAVE): raise ValueError('请选择 save 缓存目录之外的贴图文件夹')
    def walk_error(error): raise error
    for folder,dirs,files in os.walk(src,followlinks=False,onerror=walk_error):
        dirs[:]=sorted(d for d in dirs if not linked(Path(folder,d)) and Path(folder,d).resolve()!=SAVE)
        for name in sorted(files):
            p=Path(folder,name)
            if p.suffix.lower() not in EXTENSIONS or linked(p) or not p.is_file(): continue
            relative=p.relative_to(src);category,reason=classify_texture(relative)
            tags=texture_keywords(relative);auto_selected=category=='selected'
            if auto_selected: tags.append(reason)
            row=dict(source=str(p),reason=reason,tags=tags,auto_selected=auto_selected)
            if category!='excluded':
                stat=p.stat();row.update(size=stat.st_size,mtime=stat.st_mtime_ns,keyword_index=len(keyword_items))
                keyword_items.append(row);keyword_counts.update(tags)
            if vfx_only and category!='selected': counts[category]+=1;continue
            if 'size' not in row:
                stat=p.stat();row.update(size=stat.st_size,mtime=stat.st_mtime_ns)
            items.append(row)
    default_keywords=sorted({tag for row in items for tag in row['tags']})
    return dict(source=str(src),items=items,keyword_items=keyword_items,default_keywords=default_keywords,
                keywords=[dict(keyword=k,count=n) for k,n in sorted(keyword_counts.items())],
                bytes=sum(r['size'] for r in items),**counts)

def selection_items(selected,keywords=None,priority_keywords=None):
    if priority_keywords is None: priority_keywords=[]
    if not isinstance(priority_keywords,list) or any(not isinstance(k,str) for k in priority_keywords):
        raise ValueError('置顶关键词筛选无效')
    if keywords is None:
        if priority_keywords: raise ValueError('置顶关键词必须包含在已选关键词中')
        return selected['items']
    if not isinstance(keywords,list) or any(not isinstance(k,str) for k in keywords):
        raise ValueError('关键词筛选无效')
    if not keywords:
        if priority_keywords: raise ValueError('置顶关键词必须包含在已选关键词中')
        return []
    allowed={row['keyword'] for row in selected.get('keywords',[])}
    wanted=set(keywords);priority={tag:i for i,tag in reversed(list(enumerate(priority_keywords)))}
    if not wanted<=allowed: raise ValueError('关键词已失效，请重新选择')
    if not priority.keys()<=wanted: raise ValueError('置顶关键词必须包含在已选关键词中')
    return sorted((row for row in selected.get('keyword_items',[]) if wanted.intersection(row.get('tags',[]))),
                  key=lambda row:(min((priority[tag] for tag in row.get('tags',[]) if tag in priority),default=len(priority)),row.get('auto_selected',False)))


def make_move_plan(source,target,selection=None):
    src,dst=roots(source,target);items=[];skipped=[]
    selected=selection if selection is not None else select_textures(source)
    if selected['source']!=str(src): raise ValueError('来源与所选贴图不一致')
    for row in selected['items']:
        p=verify_source(row,src);dest=dst/p.relative_to(src);target_safe(dest,dst)
        if dest.exists(): skipped.append(str(dest));continue
        items.append(dict(row,target=str(dest)))
    return dict(source=str(src),target=str(dst),items=items,skipped=skipped,bytes=sum(r['size'] for r in items))

def verify_source(row,root):
    path=Path(row['source'])
    if not path.is_relative_to(root) or path.resolve()!=path or linked(path): raise ValueError('来源路径已变化：'+str(path))
    stat=path.stat()
    if not path.is_file() or (stat.st_size,stat.st_mtime_ns)!=(row['size'],row['mtime']): raise ValueError('来源文件已变化，请重新预览：'+str(path))
    return path

def move_one(row,src,dst):
    path=verify_source(row,src);target=Path(row['target']);target_safe(target,dst)
    target.parent.mkdir(parents=True,exist_ok=True);target_safe(target,dst)
    if target.exists(): raise FileExistsError('目标已存在，停止移动：'+str(target))
    # Windows rename never replaces an existing destination. EXDEV needs a copy.
    try: os.rename(path,target);return
    except OSError as e:
        if e.errno!=errno.EXDEV and getattr(e,'winerror',None)!=17: raise
    digest=hashlib.sha256()
    with path.open('rb') as source,target.open('xb') as output:
        for block in iter(lambda:source.read(1024*1024),b''):
            output.write(block);digest.update(block)
        output.flush();os.fsync(output.fileno())
    copied=hashlib.sha256()
    with target.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): copied.update(block)
    if copied.digest()!=digest.digest(): raise ValueError('目标校验失败，原文件保留：'+str(path))
    verify_source(row,src);shutil.copystat(path,target)
    # This removal completes the user-confirmed cross-volume move after verification.
    path.unlink()

def execute_move(plan,progress=lambda *a:None):
    src,dst=roots(plan['source'],plan['target'])
    for row in plan['items']:
        verify_source(row,src);target_safe(Path(row['target']),dst)
        if Path(row['target']).exists(): raise ValueError('目标已存在，请重新预览：'+row['target'])
    journal=SAVE/('move-'+str(time.time_ns())+'.jsonl');completed=[];errors=[]
    with journal.open('a',encoding='utf-8') as log:
        for row in plan['items']:
            try:
                log.write(json.dumps(dict(event='before',**row),ensure_ascii=False)+'\n');log.flush()
                move_one(row,src,dst);completed.append(row['source'])
                log.write(json.dumps(dict(event='moved',**row),ensure_ascii=False)+'\n');log.flush()
                progress(len(completed),len(plan['items']))
            except Exception as e: errors.append(str(e));break
    return dict(completed=len(completed),errors=errors,journal=str(journal),skipped=len(plan['skipped']))
