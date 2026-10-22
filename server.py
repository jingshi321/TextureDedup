import sys
sys.dont_write_bytecode=True
import os, json, threading, io, secrets, webbrowser, argparse
import time, math, sqlite3
from collections import defaultdict, deque
from cleanup import make_plan, execute, prune_result, SIMILAR
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from engine import scan, Cancelled, ROOT, SAVE, score_contents, CONTENT_SCORE_VERSION, store_scan_cache, EXTENSIONS
from PIL import Image, ImageOps
from move_textures import make_move_plan, execute_move, select_textures, verify_source, selection_items

TOKEN=secrets.token_urlsafe(24)
LOCK=threading.RLock()
STATE={'running':False,'phase':'就绪','done':0,'total':0,'error':None,'result':None}
CANCEL=threading.Event()
PLANS={}
MOVE_PLANS={}
MOVE_SELECTIONS={}
FOLDER_PICKER=None
MOVE_STATUS={'running':False,'done':0,'total':0,'outcome':None}
CLEANUP_STATUS={'running':False,'phase':'','done':0,'total':0,'token':None,'error':None}

def run_move(plan):
    def progress(done,total):
        with LOCK:
            MOVE_STATUS.update(done=done,total=total)
            STATE.update(done=done,total=total)
    try:
        outcome=execute_move(plan,progress)
    except Exception as e:
        outcome={'completed':0,'errors':[str(e)],'skipped':len(plan['skipped'])}
    with LOCK:
        MOVE_STATUS.update(running=False,outcome=outcome)
        STATE.update(running=False,phase='移动结束，请重新扫描',result=None)
        (SAVE/'last-result.json').write_text('null',encoding='utf-8')

def filter_groups(result,q):
    minimum=float(q.get('min_similarity',['0'])[0])
    if not math.isfinite(minimum) or not 0<=minimum<=100:
        raise ValueError('相似度必须在 0–100 之间')
    kind=q.get('kind',[''])[0]
    return [g for g in result['groups'] if not g.get('score_error') and (not kind or g['kind']==kind) and float(g['score'])*100>=minimum]

def mixed_groups(groups):
    """Show every available category early in the all-results view."""
    buckets=defaultdict(deque)
    for group in groups: buckets[group['kind']].append(group)
    active=deque(buckets.values()); ordered=[]
    while active:
        bucket=active.popleft(); ordered.append(bucket.popleft())
        if bucket: active.append(bucket)
    return ordered

def largest_selections(result,q):
    return [dict(group=g['id'],keep=max(g['ids'],key=lambda i:(result['records'][i]['size'],-i)),kind=g['kind'],score=g['score']) for g in filter_groups(result,q)]

def preview_selection(result,selections,q=None):
    groups={g['id']:g for g in result['groups']}
    scope={g['id'] for g in filter_groups(result,q)} if q is not None else None
    active=[s for s in selections if scope is None or int(s['group']) in scope]
    if not active: raise ValueError('请先选择当前筛选下要处理的组')
    for selection in active:
        try: group=groups.get(int(selection['group'])); keep=int(selection['keep'])
        except (KeyError,TypeError,ValueError): group=None; keep=None
        if group is None or keep not in group['ids']: raise ValueError('选择的分组或保留文件已失效')
    similar=[s for s in active if groups[int(s['group'])]['kind'] in SIMILAR]
    plan=make_plan(result,active,scope)
    similar_candidates={i for selection in similar for i in groups[int(selection['group'])]['ids'] if i!=int(selection['keep'])}
    actual_items={row['index'] for row in plan['items']}
    return dict(plan,similar_groups=len(similar),skipped_groups=0,
                similar_items=len(similar_candidates&actual_items))

def run_scan(roots,options):
    def progress(phase,done,total):
        with LOCK: STATE.update(phase=phase,done=done,total=total)
    try:
        result=scan(roots,options,progress,CANCEL)
        (SAVE/'last-result.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
        with LOCK: STATE.update(result=result,phase='扫描完成',done=result['scanned'],total=result['scanned'])
    except Cancelled:
        with LOCK: STATE.update(phase='已取消')
    except Exception as e:
        with LOCK: STATE.update(phase='扫描失败',error=str(e))
    finally:
        with LOCK: STATE['running']=False

def run_rescore(result):
    def progress(phase,done,total):
        with LOCK: STATE.update(phase=phase,done=done,total=total)
    try:
        with sqlite3.connect(str(SAVE/'index.sqlite')) as db:
            score_contents(result,db,progress,CANCEL)
        (SAVE/'last-result.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
        with LOCK: STATE.update(result=result,phase='内容相似度已更新',done=result['scanned'],total=result['scanned'])
    except Cancelled:
        with LOCK: STATE.update(phase='内容评分已取消；重启后可继续')
    except Exception as e:
        with LOCK: STATE.update(phase='内容评分失败',error=str(e))
    finally:
        with LOCK: STATE['running']=False

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def send(self,data,kind='application/json; charset=utf-8',status=200):
        if not isinstance(data,bytes): data=json.dumps(data,ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type',kind); self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers(); self.wfile.write(data)
    def valid_host(self): return self.headers.get('Host')==f'127.0.0.1:{self.server.server_port}'
    def do_GET(self):
        if not self.valid_host(): return self.send({'error':'无效访问来源'},status=403)
        try:
            u=urlparse(self.path); q=parse_qs(u.query)
            if u.path=='/':
                page=(ROOT/'index.html').read_text(encoding='utf-8').replace('__TOKEN__',TOKEN)
                return self.send(page.encode('utf-8'),'text/html; charset=utf-8')
            if u.path=='/app.js': return self.send((ROOT/'app.js').read_bytes(),'text/javascript; charset=utf-8')
            if u.path=='/api/status':
                with LOCK:
                    s={k:v for k,v in STATE.items() if k!='result'}; r=STATE['result']
                    if r:
                        counts={}
                        for g in r['groups']: counts[g['kind']]=counts.get(g['kind'],0)+1
                        s.update(summary={'images':sum(row is not None for row in r['records']),'groups':len(r['groups']),'revision':r.get('revision',0),'errors':len(r['errors']),'counts':counts,'limited':r['limited'],'options':r['options'],'ai_stats':r.get('ai_stats'),'timings':r.get('timings')})
                return self.send(s)
            if u.path=='/api/move-status':
                with LOCK: return self.send(dict(MOVE_STATUS))
            if u.path=='/api/cleanup-status':
                with LOCK: status=dict(CLEANUP_STATUS)
                return self.send(status)
            if u.path=='/api/move-thumb':
                with LOCK:
                    selected=MOVE_SELECTIONS.get(q.get('selection',[''])[0])
                    if selected is None: raise ValueError('选择已失效，请重新选择')
                    index=int(q['id'][0]);rows=selected['keyword_items']
                    if not 0<=index<len(rows): raise ValueError('贴图编号无效')
                    row=rows[index]
                path=verify_source(row,Path(selected['source']))
                with Image.open(path) as source:
                    im=source.convert('RGBA');im.thumbnail((192,192))
                    buffer=io.BytesIO();im.save(buffer,format='PNG')
                return self.send(buffer.getvalue(),'image/png')
            if u.path not in ('/api/results','/api/select-largest','/api/thumb'): return self.send({'error':'未找到'},status=404)
            with LOCK: result=STATE['result']
            if result is None: return self.send({'groups':[],'total':0})
            if u.path=='/api/select-largest':
                with LOCK:
                    if STATE['running']: raise ValueError('请等待扫描完成')
                selected=largest_selections(result,q)
                return self.send(dict(selections=selected,total=len(selected)))
            if u.path=='/api/results':
                kind=q.get('kind',[''])[0]; offset=max(0,int(q.get('offset',['0'])[0])); limit=12
                groups=filter_groups(result,q)
                available=filter_groups(result,{k:v for k,v in q.items() if k!='kind'})
                counts={}
                for group in available: counts[group['kind']]=counts.get(group['kind'],0)+1
                if not kind: groups=mixed_groups(groups)
                page=[]
                for g in groups[offset:offset+limit]:
                    keep=g.get('keep',max(g['ids'],key=lambda i:(result['records'][i]['size'],-i)))
                    ordered=sorted(g['ids'],key=lambda i:(-result['records'][i]['size'],i))
                    page.append({**g,'keep':keep,'items':[dict(result['records'][i],index=i) for i in ordered[:100]],'hidden':max(0,len(g['ids'])-100)})
                response={'groups':page,'total':len(groups),'counts':counts,'available_total':len(available),'errors':result['errors'][:20],'revision':result.get('revision',0)}
                if q.get('revision',[''])[0]!=str(response['revision']):
                    response['group_ids']=[g['id'] for g in result['groups']]
                    response['removed_ids']=[i for i,row in enumerate(result['records']) if row is None]
                return self.send(response)
            if u.path=='/api/thumb':
                i=int(q['id'][0]); channel=q.get('channel',['rgba'])[0]
                size=max(64,min(1024,int(q.get('size',['384'])[0])))
                if i<0 or i>=len(result['records']) or result['records'][i] is None: raise ValueError('贴图已删除或编号无效')
                with Image.open(result['records'][i]['path']) as source:
                    im=source.convert('RGBA')
                    if channel=='alpha': im=im.getchannel('A').convert('RGB')
                    elif channel=='rgb': im=im.convert('RGB')
                    elif channel in ('r','g','b'): im=im.getchannel(channel.upper()).convert('RGB')
                    im.thumbnail((size,size),Image.Resampling.LANCZOS)
                    buffer=io.BytesIO(); im.save(buffer,format='PNG')
                return self.send(buffer.getvalue(),'image/png')
            return self.send({'error':'未找到'},status=404)
        except (BrokenPipeError,ConnectionResetError): pass
        except Exception as e: self.send({'error':str(e)},status=400)
    def do_POST(self):
        if not self.valid_host() or self.headers.get('X-Token')!=TOKEN: return self.send({'error':'无效请求'},status=403)
        origin=self.headers.get('Origin')
        if origin and origin!=f'http://127.0.0.1:{self.server.server_port}': return self.send({'error':'无效来源'},status=403)
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length>(8*1024*1024 if self.path=='/api/cleanup-preview' else 65536): raise ValueError('请求过大')
            data=json.loads(self.rfile.read(length) or b'{}')
            if self.path=='/api/move-select':
                with LOCK:
                    if STATE['running']: raise ValueError('请等待当前操作完成')
                    selected=select_textures(data.get('source'),vfx_only=True)
                    key=secrets.token_urlsafe(24);MOVE_SELECTIONS.clear();MOVE_SELECTIONS[key]=selected
                return self.send(dict(selection=key,source=selected['source'],total=len(selected['items']),bytes=selected['bytes'],excluded=selected['excluded'],unknown=selected['unknown'],keywords=selected['keywords'],default_keywords=selected['default_keywords']))
            if self.path=='/api/move-selection':
                with LOCK:
                    selected=MOVE_SELECTIONS.get(data.get('selection'))
                    if selected is None: raise ValueError('选择已失效，请重新选择')
                    offset=max(0,int(data.get('offset',0)))
                    rows=selection_items(selected,data.get('keywords'),data.get('priority_keywords'))
                    items=[dict(row,index=row['keyword_index']) for row in rows[offset:offset+100]]
                return self.send(dict(items=items,total=len(rows),bytes=sum(row['size'] for row in rows)))
            if self.path=='/api/move-preview':
                with LOCK:
                    if STATE['running']: raise ValueError('请等待当前操作完成')
                    selected=MOVE_SELECTIONS.get(data.get('selection')) if 'selection' in data else None
                    if 'selection' in data and selected is None: raise ValueError('选择已失效，请重新选择')
                    if selected is not None: selected=dict(selected,items=selection_items(selected,data.get('keywords'),data.get('priority_keywords')))
                    plan=make_move_plan(data.get('source'),data.get('target'),selected)
                    key=secrets.token_urlsafe(24);MOVE_PLANS.clear();MOVE_PLANS[key]=(time.monotonic(),plan)
                    report=SAVE/('move-plan-'+str(time.time_ns())+'.json')
                    report.write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
                    return self.send(dict(token=key,total=len(plan['items']),bytes=plan['bytes'],skipped=len(plan['skipped']),items=plan['items'][:200],report=str(report)))
            if self.path=='/api/move-execute':
                if data.get('confirm') is not True: raise ValueError('请确认移动清单')
                with LOCK:
                    item=MOVE_PLANS.pop(data.get('token',''),None)
                    if STATE['running'] or item is None or time.monotonic()-item[0]>600: raise ValueError('清单已失效，请重新预览')
                    plan=item[1]
                    MOVE_STATUS.update(running=True,done=0,total=len(plan['items']),outcome=None)
                    STATE.update(running=True,phase='正在移动贴图',done=0,total=len(plan['items']),error=None)
                    PLANS.clear()
                    threading.Thread(target=run_move,args=(plan,),daemon=True).start()
                return self.send({'ok':True})
            if self.path=='/api/scan':
                roots=data.get('roots',[]); options=data.get('options',{})
                if not isinstance(roots,list) or not roots or not all(isinstance(p,str) for p in roots): raise ValueError('请输入目录，每行一个')
                options['distance']=max(0,min(16,int(options.get('distance',6))))
                options['color']=max(.005,min(.5,float(options.get('color',.08))))
                options['threshold']=max(.8,min(1,float(options.get('threshold',.97))))
                with LOCK:
                    if STATE['running']: return self.send({'error':'已有扫描正在运行'},status=409)
                    STATE.update(running=True,phase='准备扫描',done=0,total=0,error=None,result=None); CANCEL.clear()
                    PLANS.clear()
                    threading.Thread(target=run_scan,args=(roots,options),daemon=True).start()
                return self.send({'ok':True})
            if self.path=='/api/cleanup-preview':
                with LOCK:
                    if STATE['running'] or STATE['result'] is None: raise ValueError('请等待扫描完成')
                    q={k:[str(data[k])] for k in ('kind','min_similarity') if k in data}
                    plan=preview_selection(STATE['result'],data.get('selections',[]),q if q else None)
                    key=secrets.token_urlsafe(24); PLANS.clear()
                    PLANS[key]=(time.monotonic(),STATE['result'],plan)
                return self.send(dict(plan,token=key))
            if self.path=='/api/cleanup-execute':
                with LOCK:
                    item=PLANS.pop(data.get('token',''),None)
                    if item is None or time.monotonic()-item[0]>300 or STATE['running'] or item[1] is not STATE['result']: raise ValueError('清单已失效，请重新预览')
                    plan=item[2]; total=len(plan['keepers'])+2*len(plan['items'])
                    CLEANUP_STATUS.update(running=True,phase='正在核对文件',done=0,total=total,
                                          token=data['token'],error=None,files=len(plan['items']),checked=len(plan['keepers'])+len(plan['items']))
                    STATE.update(running=True,operation='cleanup',phase='正在核对文件',done=0,total=total,error=None)
                def progress(phase,done,total):
                    with LOCK:
                        CLEANUP_STATUS.update(phase=phase,done=done,total=total)
                        STATE.update(phase=phase,done=done,total=total)
                try:
                    outcome=execute(plan,progress=progress)
                    with LOCK:
                        if outcome['completed']:
                            result=prune_result(STATE['result'],outcome['completed'])
                            STATE.update(result=result)
                            PLANS.clear()
                            try:
                                (SAVE/'last-result.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
                            except OSError as e:
                                outcome['errors'].append('结果已更新，但结果缓存保存失败：'+str(e))
                        phase='清理遇到问题，已停止' if outcome['errors'] else '清理完成，结果已更新'
                        CLEANUP_STATUS.update(phase=phase,error='；'.join(outcome['errors']) or None)
                        STATE.update(phase=phase)
                except Exception as e:
                    with LOCK:
                        CLEANUP_STATUS.update(phase='清理失败',error=str(e))
                        STATE.update(phase='清理失败',error=str(e))
                    raise
                finally:
                    with LOCK:
                        CLEANUP_STATUS['running']=False
                        STATE.update(running=False,operation=None)
                return self.send(outcome)
            if self.path=='/api/cancel': CANCEL.set(); return self.send({'ok':True})
            if self.path=='/api/open-image':
                with LOCK:
                    i=int(data['id']);result=STATE['result']
                    if result is None or i<0 or i>=len(result['records']) or result['records'][i] is None: raise ValueError('贴图已删除或编号无效')
                    path=Path(result['records'][i]['path']).resolve()
                    if path.suffix.lower() not in EXTENSIONS or not path.is_file(): raise ValueError('原始图片不存在或格式无效，请重新扫描')
                os.startfile(str(path),'open');return self.send({'ok':True})
            if self.path=='/api/open':
                with LOCK:
                    i=int(data['id']); result=STATE['result']
                    if result is None or i<0 or i>=len(result['records']) or result['records'][i] is None: raise ValueError('贴图已删除或编号无效')
                    folder=str(Path(result['records'][i]['path']).parent)
                os.startfile(folder); return self.send({'ok':True})
            if self.path=='/api/folder':
                if FOLDER_PICKER is not None: return self.send({'path':FOLDER_PICKER() or ''})
                # Separate process owns its Tk event loop; no cross-thread Tk access.
                import subprocess
                script="import tkinter as t; from tkinter import filedialog; r=t.Tk(); r.withdraw(); r.attributes('-topmost',True); print(filedialog.askdirectory(title='选择贴图目录')); r.destroy()"
                env=os.environ.copy(); env['PYTHONIOENCODING']='utf-8'; env['PYTHONDONTWRITEBYTECODE']='1'
                p=subprocess.run([sys.executable,'-B','-c',script],capture_output=True,text=True,encoding='utf-8',env=env)
                if p.returncode: raise ValueError(p.stderr)
                return self.send({'path':p.stdout.strip()})
            return self.send({'error':'未找到'},status=404)
        except Exception as e: self.send({'error':str(e)},status=400)

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--no-browser',action='store_true'); parser.add_argument('--port',type=int,default=0); args=parser.parse_args()
    os.environ['TEMP']=os.environ['TMP']=str(SAVE/'tmp'); (SAVE/'tmp').mkdir(exist_ok=True)
    cached=SAVE/'last-result.json'
    if cached.exists():
        try:
            result=json.loads(cached.read_text(encoding='utf-8'))
            if result and result.get('score_version')!=CONTENT_SCORE_VERSION:
                backup=SAVE/('before-content-score-'+str(time.time_ns())+'.json')
                backup.write_bytes(cached.read_bytes())
                STATE.update(running=True,phase='正在更新旧结果的内容相似度',done=0,total=len(result['groups']))
                threading.Thread(target=run_rescore,args=(result,),daemon=True).start()
            elif result:
                with sqlite3.connect(str(SAVE/'index.sqlite')) as db:
                    store_scan_cache(db,result)
                STATE.update(result=result,phase='上次扫描结果（缓存）',done=result['scanned'],total=result['scanned'])
        except (ValueError,OSError,TypeError): pass
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    url=f'http://127.0.0.1:{server.server_port}'
    print('TextureDedup: '+url,flush=True)
    print('Cache: '+str(SAVE),flush=True)
    if not args.no_browser: webbrowser.open(url)
    try: server.serve_forever()
    except KeyboardInterrupt: CANCEL.set()
    finally: server.server_close()

if __name__=='__main__': main()
