"""Texture comparison. All runtime state lives under save/."""
import sys
import os
sys.dont_write_bytecode = True
from pathlib import Path
ROOT = Path(__file__).resolve().parent
SAVE = Path(os.environ.get('TEXTUREDEDUP_CACHE_DIR',str(ROOT / 'save'))).resolve()
SAVE.mkdir(parents=True,exist_ok=True)
sys.path.insert(0, str(SAVE / 'deps'))
if (ROOT/'save'/'gpu-deps').is_dir(): sys.path.insert(0,str(ROOT/'save'/'gpu-deps'))
import os, hashlib, json, sqlite3, struct, time, threading, io, zlib, ctypes, stat, base64
from collections import defaultdict, OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import numpy as np
from PIL import Image

EXTENSIONS = {'.png','.tga','.dds','.tif','.tiff','.bmp','.jpg','.jpeg','.webp'}
DCT = np.cos(np.pi*(np.arange(32)[None,:]+.5)*np.arange(8)[:,None]/32)

class Cancelled(Exception): pass

CONTENT_SCORE_VERSION='rgba-mae-common256-v1'
SCAN_CACHE_VERSION='search-reuse-v3:'+CONTENT_SCORE_VERSION


def box_channels(im,size):
    # Resize channels separately to preserve RGB hidden under transparent pixels.
    return np.stack([np.asarray(c.resize(size,Image.Resampling.BOX),dtype=np.uint8) for c in im.split()],axis=2)


def ai_thumbnail(im):
    bg=Image.new('RGBA',im.size,(128,128,128,255))
    return np.asarray(Image.alpha_composite(bg,im).convert('RGB').resize((224,224),Image.Resampling.BILINEAR),dtype=np.uint8)


def ai_array(rgb):
    a=np.asarray(rgb,dtype=np.float32)/255
    a=(a-np.array([.485,.456,.406],np.float32))/np.array([.229,.224,.225],np.float32)
    return a.transpose(2,0,1)


class PreparedImages:
    """Lossless intermediate images; bounded disk use inside the scan cache directory."""
    def __init__(self,path,limit=1024*1024*1024):
        self.lock=threading.Lock();self.limit=limit;self.writes=0
        self.db=sqlite3.connect(str(path),check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS prepared_v1(hash TEXT PRIMARY KEY,box BLOB,ai BLOB)')
        self.bytes=self.db.execute('SELECT COALESCE(SUM(COALESCE(length(box),0)+COALESCE(length(ai),0)),0) FROM prepared_v1').fetchone()[0]
    def capture(self,row,im,want_ai=False):
        if want_ai and self.needed(row,'ai'):self.put(row,'ai',ai_thumbnail(im))
    def needed(self,row,kind):
        with self.lock:
            if self.bytes>=self.limit:return False
            found=self.db.execute('SELECT '+kind+' IS NOT NULL FROM prepared_v1 WHERE hash=?',(row['pixelhash'],)).fetchone()
            return not found or not found[0]
    def put(self,row,kind,raw):
        if not self.needed(row,kind):return
        value=zlib.compress(raw.tobytes(),1);key=row['pixelhash']
        with self.lock:
            current=self.db.execute('SELECT '+kind+' IS NOT NULL FROM prepared_v1 WHERE hash=?',(key,)).fetchone()
            if current and current[0] or self.bytes+len(value)>self.limit:return
            if current is None:self.db.execute('INSERT INTO prepared_v1(hash,'+kind+') VALUES(?,?)',(key,value))
            else:self.db.execute('UPDATE prepared_v1 SET '+kind+'=? WHERE hash=?',(value,key))
            self.bytes+=len(value);self.writes+=1
            if self.writes%128==0:self.db.commit()
    def get(self,row,kind):
        try:
            with self.lock:
                found=self.db.execute('SELECT '+kind+' FROM prepared_v1 WHERE hash=?',(row['pixelhash'],)).fetchone()
        except sqlite3.Error:return None
        if not found or found[0] is None:return None
        try:
            shape=(224,224,3) if kind=='ai' else (min(row['h'],256),min(row['w'],256),4)
            return np.frombuffer(zlib.decompress(found[0]),np.uint8).reshape(shape)
        except (ValueError,zlib.error):return None
    def close(self):
        with self.lock:self.db.commit();self.db.close()

class DecodedFileReuse:
    """Share decoded features for files whose complete byte hashes match."""
    def __init__(self):self.lock=threading.Lock();self.entries={};self.hits=0
    def acquire(self,key):
        with self.lock:
            entry=self.entries.get(key)
            if entry is None:
                event=threading.Event();self.entries[key]=[event,None,None]
                return None,event
        entry[0].wait()
        with self.lock:
            row,error=self.entries[key][1:]
            if row is not None:self.hits+=1
        if error is not None:raise ValueError(error)
        return row,None
    def finish(self,key,event,row=None,error=None):
        with self.lock:
            entry=self.entries.get(key)
            if entry is not None and entry[0] is event:
                entry[1]=row;entry[2]=error
        event.set()

def scan_cache_identity(roots,options,records):
    config=dict(options);config.pop('refresh',None)
    key=json.dumps([SCAN_CACHE_VERSION,roots,config],sort_keys=True,ensure_ascii=False)
    manifest=[[r['path'],r['size'],r['mtime']] for r in records]
    return hashlib.sha256(key.encode()).hexdigest(),hashlib.sha256(json.dumps(manifest,ensure_ascii=False).encode()).hexdigest()

def store_scan_cache(db,result):
    if result['errors'] or any(row is None for row in result['records']): return
    key,manifest=scan_cache_identity(result['roots'],result['options'],result['records'])
    db.execute('CREATE TABLE IF NOT EXISTS scan_results(key TEXT PRIMARY KEY,manifest TEXT,data TEXT)')
    db.execute('INSERT OR REPLACE INTO scan_results VALUES(?,?,?)',(key,manifest,json.dumps(result,ensure_ascii=False)))


def encode_image_cache(row):
    cached=dict(row)
    thumb=cached.get('thumb')
    if isinstance(thumb,bytes):
        cached['thumb']='z1:'+base64.b64encode(zlib.compress(thumb,1)).decode('ascii')
    return json.dumps(cached,ensure_ascii=False,separators=(',',':'))


def decode_image_cache(data):
    cached=json.loads(data)
    thumb=cached.get('thumb')
    if isinstance(thumb,str):
        if thumb.startswith('z1:'):
            thumb=zlib.decompress(base64.b64decode(thumb[3:]))
        else:
            thumb=bytes.fromhex(thumb)
        cached['thumb']=thumb
    alpha=cached.get('alpha')
    if isinstance(alpha,list):cached['alpha']=tuple(alpha)
    return cached

class ResizePixels:
    """Per-scan LRU, bounded to 64 MiB of raw-channel comparison arrays."""
    def __init__(self, limit=64*1024*1024,prepared=None):
        self.limit=limit; self.bytes=0; self.items=OrderedDict();self.lock=threading.Lock();self.prepared=prepared
        self.loading=[threading.Lock() for _ in range(32)]
    def get(self,row,size):
        key=(row['pixelhash'],size)
        # Batch preparation and ordinary reads share the same image lock.
        with self.loading[hash(row['pixelhash'])%len(self.loading)]:return self._get(row,size,key)
    def prepare_many(self,requests,pool,check):
        """Decode multi-size images once, using at most half the LRU for this batch."""
        check();images={}
        for row,size in requests:images.setdefault(row['pixelhash'],[row,{}])[1][size]=None
        jobs=[];budget=self.limit//2
        with self.lock:
            for pixelhash,(row,sizes) in images.items():
                missing=[size for size in sizes if (pixelhash,size) not in self.items]
                needed=sum(w*h*4*np.dtype(np.float32).itemsize for w,h in missing)
                if len(missing)>1 and needed<=budget:
                    jobs.append((row,missing));budget-=needed
        futures=[]
        for row,sizes in jobs:
            check();futures.append(pool.submit(self._prepare,row,sizes,check))
        for future in futures:
            check();future.result()
        check()
    def _prepare(self,row,sizes,check):
        check()
        with self.loading[hash(row['pixelhash'])%len(self.loading)]:
            check()
            with self.lock:sizes=[size for size in sizes if (row['pixelhash'],size) not in self.items]
            if len(sizes)<2:return
            try:
                with Image.open(row['path']) as source,source.convert('RGBA') as rgba:
                    for size in sizes:
                        check();self._get(row,size,(row['pixelhash'],size),rgba)
            except Cancelled:raise
            except Exception:pass  # The ordinary pair read reports an image error as before.
    def _get(self,row,size,key,source=None):
        with self.lock:
            if key in self.items:
                self.items.move_to_end(key); return self.items[key]
        raw=None
        if self.prepared and size==(min(row['w'],256),min(row['h'],256)):
            raw=self.prepared.get(row,'box')
        if raw is None:
            if source is not None:raw=box_channels(source,size)
            else:
                with Image.open(row['path']) as im:raw=box_channels(im.convert('RGBA'),size)
            if self.prepared and size==(min(row['w'],256),min(row['h'],256)):
                try:self.prepared.put(row,'box',raw)
                except (sqlite3.Error,OSError):pass
        value=raw.astype(np.float32)/255
        with self.lock:
            if key in self.items:
                self.items.move_to_end(key);return self.items[key]
            if value.nbytes<=self.limit:
                while self.items and self.bytes+value.nbytes>self.limit:
                    _,old=self.items.popitem(last=False); self.bytes-=old.nbytes
                self.items[key]=value; self.bytes+=value.nbytes
        return value

def comparison_size(a,b):
    return (min(a['w'],b['w'],256),min(a['h'],b['h'],256))


def content_pair(a,b,strict_alpha):
    return (*tuple(CONTENT_SCORE_VERSION+':'+v for v in sorted((a['pixelhash'],b['pixelhash']))),int(strict_alpha))


def _content_score(diff,strict_alpha):
    rgb=float(diff[:,:,:3].mean(dtype=np.float64))
    alpha=float(diff[:,:,3].mean(dtype=np.float64))
    return max(0.,1-max(rgb,alpha if strict_alpha else 0.))


def resize_match(a,b,pixels=None,with_score=False,strict_alpha=True):
    """Verify a candidate by resizing raw channels to their common lower size."""
    size=comparison_size(a,b)
    pixels=pixels or ResizePixels()
    diff=np.abs(pixels.get(a,size)-pixels.get(b,size))
    matched=float(diff.mean())<=.012 and float(np.quantile(diff,.95))<=.04
    return (matched,_content_score(diff,strict_alpha)) if with_score else matched

def content_similarity(a,b,pixels,strict_alpha=True):
    """One minus normalized mean channel error; alpha cannot dilute RGB error."""
    size=comparison_size(a,b)
    diff=np.abs(pixels.get(a,size)-pixels.get(b,size))
    return _content_score(diff,strict_alpha)

def score_contents(result,db,progress,cancel,pixels=None,fresh_scores=None):
    """Rescore existing candidates without searching the image library again."""
    db.execute('CREATE TABLE IF NOT EXISTS content_scores(a TEXT,b TEXT,alpha INTEGER,score REAL,PRIMARY KEY(a,b,alpha))')
    pixels=pixels or ResizePixels(); records=result['records']; strict=bool(result['options'].get('alpha',True))
    validated={};fresh_scores=fresh_scores or {}
    def check():
        if cancel.is_set():raise Cancelled()
    def validate(row,force=False):
        path=row['path']
        if path not in validated or (force and validated[path] is None):
            try:
                stat=os.stat(path)
                if (stat.st_size,stat.st_mtime_ns)!=(row['size'],row['mtime']): raise ValueError('源文件已变化，请重新扫描')
                validated[path]=None
            except Exception as e: validated[path]=e
        if validated[path] is not None: raise validated[path]
    def calculate(a,b):
        if cancel.is_set(): raise Cancelled()
        score=content_similarity(a,b,pixels,strict)
        for row in (a,b):
            stat=os.stat(row['path'])
            if (stat.st_size,stat.st_mtime_ns)!=(row['size'],row['mtime']): raise ValueError('评分期间源文件已变化，请重新扫描')
        return score
    def failed(g,e):
        g['score_error']=str(e);g['score']=0.
        result['errors'].append('内容评分失败（组 '+str(g['id'])+'）：'+str(e))
    workers=min(max(1,len(result['groups'])),adaptive_workers(len(result['groups'])))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for start in range(0,len(result['groups']),128):
            check();pending=[];jobs={}
            for g in result['groups'][start:start+128]:
                check()
                if g['kind'] in ('文件完全重复','RGBA 像素重复'):
                    g['score']=1.;g.pop('score_error',None);continue
                try:
                    if len(g['ids'])!=2: raise ValueError('内容评分需要两张候选图片')
                    a,b=(records[i] for i in g['ids'])
                    pair=content_pair(a,b,strict)
                    validate(a,pair in fresh_scores);validate(b,pair in fresh_scores)
                    if pair in fresh_scores:
                        cached=(fresh_scores[pair],)
                        db.execute('INSERT OR REPLACE INTO content_scores VALUES(?,?,?,?)',(*pair,float(cached[0])))
                    else:
                        cached=None if result['options'].get('refresh') else db.execute('SELECT score FROM content_scores WHERE a=? AND b=? AND alpha=?',pair).fetchone()
                    if cached:
                        g.setdefault('search_score',g['score']);g['score']=float(cached[0]);g.pop('score_error',None)
                    else:
                        jobs.setdefault(pair,(a,b));pending.append((g,pair))
                except Cancelled:raise
                except Exception as e: failed(g,e)
            pixels.prepare_many(((row,comparison_size(a,b)) for a,b in jobs.values() for row in (a,b)),pool,check)
            inflight={pair:pool.submit(calculate,a,b) for pair,(a,b) in jobs.items()}
            for g,pair in pending:
                check()
                try:
                    score=inflight[pair].result()
                    db.execute('INSERT OR REPLACE INTO content_scores VALUES(?,?,?,?)',(*pair,score))
                    g.setdefault('search_score',g['score']);g['score']=float(score);g.pop('score_error',None)
                except Cancelled: raise
                except Exception as e: failed(g,e)
            db.commit();progress('计算图片内容相似度',min(start+128,len(result['groups'])),len(result['groups']))
    db.commit();result['score_version']=CONTENT_SCORE_VERSION
    return result

def phash(im):
    a = DCT @ np.asarray(im.resize((32,32),Image.Resampling.LANCZOS),dtype=float) @ DCT.T
    v = a.flatten(); v[0] = 0
    return int.from_bytes(np.packbits(v > np.median(v[1:])).tobytes(),'big')

def read_image(path,prepared=None,want_ai=False,full_hash=True,initial_stat=None,file_reuse=None):
    before = initial_stat or os.stat(path)
    h = hashlib.sha256() if full_hash else None
    image_source=None
    if full_hash:
        with open(path,'rb') as f:
            if before.st_size<=32*1024*1024:
                data=f.read();h.update(data);image_source=io.BytesIO(data)
            else:
                for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    filehash=h.hexdigest() if h is not None else ''
    reuse_event=None
    if file_reuse is not None and filehash:
        cached,reuse_event=file_reuse.acquire(filehash)
        if cached is not None:
            after=os.stat(path)
            if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns): raise ValueError('扫描期间文件发生变化')
            row=dict(cached);row.update(path=path,size=before.st_size,mtime=before.st_mtime_ns,filehash=filehash)
            return row
    try:
        with Image.open(image_source if image_source is not None else path) as source:
            if getattr(source,'n_frames',1)>1: raise ValueError('多帧图片暂不支持')
            im = source if source.mode=='RGBA' else source.convert('RGBA')
            alpha = im.getchannel('A')
            pixels=hashlib.sha256();pixels.update(struct.pack('<II',*im.size));pixels.update(im.tobytes())
            row = dict(path=path,size=before.st_size,mtime=before.st_mtime_ns,w=im.width,h=im.height,
                filehash=filehash,pixelhash=pixels.hexdigest(),
                rgbhash=str(phash(im.convert('L'))),alphahash=str(phash(alpha)),
                thumb=Image.merge('RGBA',tuple(c.resize((24,24),Image.Resampling.BOX) for c in im.split())).tobytes(),alpha=alpha.getextrema())
            if prepared:
                try:prepared.capture(row,im,want_ai)
                except (sqlite3.Error,OSError):pass # Cache failure must not prevent a normal scan.
        after = os.stat(path)
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns): raise ValueError('扫描期间文件发生变化')
    except Exception as exc:
        if reuse_event is not None:file_reuse.finish(filehash,reuse_event,error=str(exc))
        raise
    if reuse_event is not None:file_reuse.finish(filehash,reuse_event,row)
    return row


_cpu_sample_lock=threading.Lock();_cpu_sample=None
def cpu_load():
    """Return recent system CPU load on Windows, or None when unavailable."""
    global _cpu_sample
    if os.name!='nt': return None
    class FILETIME(ctypes.Structure):_fields_=[('low',ctypes.c_uint32),('high',ctypes.c_uint32)]
    idle,kernel,user=FILETIME(),FILETIME(),FILETIME()
    if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle),ctypes.byref(kernel),ctypes.byref(user)):return None
    def value(t):return (t.high<<32)+t.low
    now=(value(idle),value(kernel),value(user))
    with _cpu_sample_lock:
        old=_cpu_sample;_cpu_sample=now
    if old is None:return None
    di,dk,du=(now[i]-old[i] for i in range(3)); total=dk+du
    return max(0.,min(1.,1-di/total)) if total else None


def adaptive_workers(backlog):
    cores=os.cpu_count() or 4; maximum=min(24,max(4,cores*2)); load=cpu_load()
    if os.name=='nt':
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_=[('dwLength',ctypes.c_ulong),('dwMemoryLoad',ctypes.c_ulong),('ullTotalPhys',ctypes.c_ulonglong),('ullAvailPhys',ctypes.c_ulonglong),('ullTotalPageFile',ctypes.c_ulonglong),('ullAvailPageFile',ctypes.c_ulonglong),('ullTotalVirtual',ctypes.c_ulonglong),('ullAvailVirtual',ctypes.c_ulonglong),('ullAvailExtendedVirtual',ctypes.c_ulonglong)]
        memory=MEMORYSTATUSEX();memory.dwLength=ctypes.sizeof(memory)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            maximum=min(maximum,max(4,int(memory.ullAvailPhys//(384*1024*1024))))
    if load is None:return min(maximum,max(4,min(8,backlog)))
    if load>=.88:return 4
    if load>=.72:return min(6,maximum)
    if load<=.45:return maximum
    return min(12,maximum)

class BKTree:
    def __init__(self): self.root=None
    def add(self,h,i):
        if self.root is None: self.root=[h,[i],{}]; return
        node=self.root
        while True:
            d=(h^node[0]).bit_count()
            if not d: node[1].append(i); return
            if d not in node[2]: node[2][d]=[h,[i],{}]; return
            node=node[2][d]
    def find(self,h,r):
        stack=[self.root] if self.root else []
        while stack:
            node=stack.pop(); d=(h^node[0]).bit_count()
            if d<=r: yield from node[1]
            stack.extend(n for k,n in node[2].items() if d-r<=k<=d+r)


def hash_candidates(hashes,target,radius):
    if not len(hashes):return np.empty(0,dtype=np.intp)
    distance=np.bitwise_count(np.bitwise_xor(hashes,np.uint64(target)))
    return np.flatnonzero(distance<=radius)

class FeatureModel:
    def __init__(self,prefer_gpu=True):
        import onnxruntime as ort
        self.ort=ort;self.fallback=None
        self.model=ROOT/'models'/'texture-features-batch.onnx'
        if not self.model.exists():self.model=SAVE/'models'/'texture-features-batch.onnx'
        if not self.model.exists():raise ValueError('缺少批量 AI 模型，请使用新版完整安装包')
        self._create(prefer_gpu)
    def _create(self,prefer_gpu):
        ort=self.ort;opts=ort.SessionOptions();opts.intra_op_num_threads=4;opts.inter_op_num_threads=1
        gpu=prefer_gpu and 'DmlExecutionProvider' in ort.get_available_providers()
        if gpu:
            opts.enable_mem_pattern=False;opts.execution_mode=ort.ExecutionMode.ORT_SEQUENTIAL
            opts.add_free_dimension_override_by_name('batch',16)
        providers=[('DmlExecutionProvider',{'device_id':0}),'CPUExecutionProvider'] if gpu else ['CPUExecutionProvider']
        try:self.session=ort.InferenceSession(str(self.model),sess_options=opts,providers=providers)
        except Exception as e:
            if not gpu:raise
            self.fallback=str(e);self._create(False);return
        self.gpu='DmlExecutionProvider' in self.session.get_providers()
        self.batch_size=16 if self.gpu else 8
        self.provider='GPU · DirectML' if self.gpu else 'CPU'
        self.name=self.session.get_inputs()[0].name
    @staticmethod
    def prepare(path):
        with Image.open(path) as src:
            return ai_array(ai_thumbnail(src.convert('RGBA')))
    def encode_batch(self,arrays):
        data=np.ascontiguousarray(np.stack(arrays),dtype=np.float32);count=len(data)
        if self.gpu and count<16:data=np.concatenate([data,np.repeat(data[-1:],16-count,axis=0)])
        try:values=self.session.run(None,{self.name:data})[0][:count].reshape(count,-1)
        except Exception as e:
            if not self.gpu:raise
            self.fallback=str(e);self._create(False)
            values=self.session.run(None,{self.name:data[:count]})[0].reshape(count,-1)
        return (values/np.maximum(np.linalg.norm(values,axis=1,keepdims=True),1e-12)).astype(np.float32)
    def encode(self,path):return self.encode_batch([self.prepare(path)])[0]


def extract_ai(records,reps,db,options,progress,check,prepared=None):
    from vfx_filter import classify_texture
    selected={}
    for i,r in enumerate(records):
        if not options.get('ai_vfx_only',False) or classify_texture(r['path'])[0]=='selected':selected.setdefault(r['pixelhash'],i)
    ai_reps=list(selected.values());vectors={};pending=[];errors=[];model=None
    stats={'scope':'特效候选' if options.get('ai_vfx_only',False) else '全部贴图','images':len(ai_reps),'excluded':len(reps)-len(ai_reps),'cached':0}
    for i in ai_reps:
        check();key='mobilenetv2-pool-v2:'+records[i]['pixelhash']
        cached=None if options.get('refresh') else db.execute('SELECT vector FROM features WHERE hash=?',(key,)).fetchone()
        if cached:vectors[i]=np.frombuffer(cached[0],np.float32).copy();stats['cached']+=1
        else:pending.append((i,key))
    if pending:
        model=FeatureModel();progress('AI '+model.provider+' · 批量提取特征',len(vectors),len(ai_reps))
        def prepare(item):
            i,key=item
            try:
                check();rgb=prepared.get(records[i],'ai') if prepared else None
                return i,key,ai_array(rgb) if rgb is not None else model.prepare(records[i]['path']),None
            except Cancelled:raise
            except Exception as e:return i,key,None,str(e)
        workers=min(8,max(2,(os.cpu_count() or 4)//2))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            source=iter(pending);queue=deque();batch=[];batch_ids=[]
            def submit():
                item=next(source,None)
                if item is not None:queue.append(pool.submit(prepare,item))
            for _ in range(32):submit()
            processed=0
            while queue:
                check();i,key,array,error=queue.popleft().result();submit();processed+=1
                if error:errors.append(records[i]['path']+': AI '+error)
                else:batch.append(array);batch_ids.append((i,key))
                if batch and (len(batch)>=model.batch_size or not queue):
                    check()
                    values=model.encode_batch(batch)
                    for (item,cachekey),value in zip(batch_ids,values):
                        vectors[item]=value;db.execute('INSERT OR REPLACE INTO features VALUES(?,?)',(cachekey,value.tobytes()))
                    batch=[];batch_ids=[]
                    if processed%128<model.batch_size:db.commit()
                    progress('AI '+model.provider+' · 批量提取特征',stats['cached']+processed,len(ai_reps))
            db.commit()
    ids=[i for i in ai_reps if i in vectors]
    stats.update(provider=model.provider if model else '本次运行缓存',fallback=model.fallback if model else None)
    return ids,[vectors[i] for i in ids],errors,stats


def ai_candidate_scores(matrix,batch,n,threshold):
    # Batched SIMD dot products avoid starting a BLAS thread team for each query.
    candidates=matrix[batch]
    scores=np.einsum('ij,j->i',candidates,matrix[n],optimize=False)
    # Preserve the old decision at floating-point threshold boundaries.
    near=np.abs(scores-threshold)<=2e-6
    if np.any(near):
        reference=candidates@matrix[n]
        scores[near]=reference[near]
    return scores


def scan(roots,options,progress,cancel,dbpath=None):
    scan_started=time.perf_counter()
    def check():
        if cancel.is_set(): raise Cancelled()
    paths=[]; seen=set(); errors=[]; file_stats={}
    normalized=[]; save_key=os.path.normcase(str(SAVE.resolve()))
    for root in roots:
        p=Path(root).expanduser().resolve()
        if not p.is_dir(): raise ValueError('目录不存在：'+str(p))
        normalized.append(str(p))
        folders=[str(p)]
        while folders:
            check(); folder=folders.pop()
            try:
                with os.scandir(folder) as entries:
                    for entry in entries:
                        check()
                        try:
                            info=entry.stat(follow_symlinks=False)
                            if getattr(info,'st_file_attributes',0)&0x400: continue
                            child=entry.path
                            if stat.S_ISDIR(info.st_mode):
                                if os.path.normcase(child)!=save_key: folders.append(child)
                            elif stat.S_ISREG(info.st_mode) and Path(entry.name).suffix.lower() in EXTENSIONS:
                                key=os.path.normcase(child)
                                if key not in seen:
                                    seen.add(key); paths.append(child); file_stats[child]=info
                        except OSError as e: errors.append(entry.path+': '+str(e))
            except OSError as e: errors.append(folder+': '+str(e))
        progress('发现贴图',len(paths),0)
    discovery_seconds=time.perf_counter()-scan_started
    paths.sort(); records=[]
    db=sqlite3.connect(str(dbpath or SAVE/'index.sqlite'))
    db.execute('CREATE TABLE IF NOT EXISTS images(path TEXT PRIMARY KEY,size INTEGER,mtime INTEGER,data TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS features(hash TEXT PRIMARY KEY,vector BLOB)')
    db.execute('CREATE TABLE IF NOT EXISTS resize_pairs(a TEXT,b TEXT,matched INTEGER,PRIMARY KEY(a,b))')
    db.execute('CREATE TABLE IF NOT EXISTS scan_results(key TEXT PRIMARY KEY,manifest TEXT,data TEXT)')
    groups=[]; limited=False;prepared=None;timings={'目录枚举':round(discovery_seconds,3)}
    try:
        lookup_started=time.perf_counter(); pending=[]; size_counts={}
        for s in file_stats.values(): size_counts[s.st_size]=size_counts.get(s.st_size,0)+1
        has_image_cache=not options.get('refresh') and db.execute('SELECT 1 FROM images LIMIT 1').fetchone() is not None
        if has_image_cache and file_stats:
            db.execute('CREATE TEMP TABLE scan_files(path TEXT PRIMARY KEY,size INTEGER,mtime INTEGER)')
            db.executemany('INSERT INTO scan_files VALUES(?,?,?)',((path,s.st_size,s.st_mtime_ns) for path,s in file_stats.items()))
            query='''SELECT f.path,i.size,i.mtime,i.data FROM scan_files f
                     LEFT JOIN images i ON i.path=f.path AND i.size=f.size AND i.mtime=f.mtime'''
            for index,(path,size,mtime,data) in enumerate(db.execute(query)):
                check()
                if data:
                    try:cached=decode_image_cache(data)
                    except (ValueError,TypeError,zlib.error):pending.append(path)
                    else:
                        if not isinstance(cached.get('thumb'),bytes):pending.append(path)
                        elif not cached.get('filehash') and size_counts.get(size,0)>1:pending.append(path)
                        else:records.append(cached)
                else: pending.append(path)
                if index%1024==0: progress('检查贴图缓存',index,len(paths))
        else:
            pending=list(paths)
        timings['缓存检查']=round(time.perf_counter()-lookup_started,3)
        if not pending and not errors and not options.get('refresh'):
            records.sort(key=lambda r:r['path'])
            key,manifest=scan_cache_identity(normalized,options,records)
            cached=db.execute('SELECT data FROM scan_results WHERE key=? AND manifest=?',(key,manifest)).fetchone()
            if cached:
                check();result=json.loads(cached[0])
                result['options']=dict(options)
                result['finished']=time.strftime('%Y-%m-%d %H:%M:%S')
                result['timings']={**timings,'结果复用':True,'总耗时':round(time.perf_counter()-scan_started,3)}
                progress('复用未变化的查重结果',len(paths),len(paths))
                return result
        try:
            if options.get('ai') or options.get('similar',True) or options.get('resize',True):
                prepared=PreparedImages(str(dbpath or SAVE/'index.sqlite')+'.prepared.sqlite')
        except (sqlite3.Error,OSError):prepared=None
        from vfx_filter import classify_texture
        file_reuse=DecodedFileReuse()
        def work(path):
            try:
                check()
                want_ai=options.get('ai') and (not options.get('ai_vfx_only',False) or classify_texture(path)[0]=='selected')
                return read_image(path,prepared,want_ai,size_counts.get(file_stats[path].st_size,0)>1,file_stats[path],file_reuse),None
            except Cancelled: raise
            except Exception as e: return None,path+': '+str(e)
        # Recreate a bounded pool between waves so the worker count can follow
        # system CPU pressure without adding another runtime dependency.
        read_started=time.perf_counter(); completed=0;last_progress=0
        for wave_start in range(0,len(pending),512):
            check();wave=pending[wave_start:wave_start+512]
            with ThreadPoolExecutor(max_workers=adaptive_workers(len(wave))) as pool:
                futures={pool.submit(work,path) for path in wave}
                while futures:
                    check();done,_=wait(futures,timeout=.2,return_when=FIRST_COMPLETED)
                    for future in done:
                        futures.remove(future); row,error=future.result();check()
                        if error: errors.append(error)
                        else:
                            records.append(row)
                            db.execute('INSERT OR REPLACE INTO images VALUES(?,?,?,?)',(row['path'],row['size'],row['mtime'],encode_image_cache(row)))
                        completed+=1
                    if completed and completed%256<len(done): db.commit()
                    if done and (not futures or time.monotonic()-last_progress>=.1):
                        progress('读取贴图（含缓存）',len(records)+len(errors),len(paths)); last_progress=time.monotonic()
            progress('读取贴图（含缓存）',len(records)+len(errors),len(paths))
        db.commit()
        timings['图像读取与特征']=round(time.perf_counter()-read_started,3)
        records.sort(key=lambda r:r['path'])
        appearance_started=time.perf_counter()
        for field,kind in [('filehash','文件完全重复'),('pixelhash','RGBA 像素重复')]:
            buckets=defaultdict(list)
            for i,r in enumerate(records): buckets[r[field]].append(i)
            for ids in buckets.values():
                if not ids or not records[ids[0]][field]: continue
                if len(ids)>1 and (field=='filehash' or len({records[i]['filehash'] for i in ids})>1):
                    groups.append(dict(kind=kind,ids=ids,score=1.0,note='完整文件 SHA256 相同' if field=='filehash' else '尺寸和全部 RGBA 像素相同'))
        representatives={}
        for i,r in enumerate(records): representatives.setdefault(r['pixelhash'],i)
        reps=list(representatives.values()); pair_seen=set()
        thumbs={i:np.frombuffer(records[i]['thumb'],np.uint8).reshape(24,24,4) for i in reps}
        def compatible(i,j,ai=False,stretch=False):
            a,b=records[i],records[j]
            if not stretch and abs(np.log((a['w']/a['h'])/(b['w']/b['h'])))>.08: return False
            ta,tb=thumbs[i],thumbs[j]
            if options.get('alpha',True):
                if (int(a['alphahash'])^int(b['alphahash'])).bit_count()>8:return False
                alpha_diff=np.abs(ta[:,:,3].astype(np.float32)/255-tb[:,:,3].astype(np.float32)/255)
                if float(alpha_diff.mean())>.035:return False
            if ai:return True
            rgb_diff=np.abs(ta[:,:,:3].astype(np.float32)/255-tb[:,:,:3].astype(np.float32)/255)
            return float(rgb_diff.mean())<=float(options.get('color',.08))
        def add(i,j,kind,score):
            nonlocal limited
            key=tuple(sorted((i,j)))
            if key in pair_seen: return False
            if len(pair_seen)>=50000: limited=True; return False
            pair_seen.add(key); groups.append(dict(kind=kind,ids=list(key),score=float(score),note='相似候选，请对照 RGB / Alpha 人工确认'))
            return True
        resize_pixels=ResizePixels(prepared=prepared)
        fresh_scores={};strict_alpha=bool(options.get('alpha',True))
        if options.get('similar',True) or options.get('resize',True):
            radius=int(options.get('distance',6)); candidate_radius=max(radius,12) if options.get('resize',True) else radius
            rgb_hashes=np.fromiter((int(records[i]['rgbhash']) for i in reps),dtype=np.uint64,count=len(reps))
            verified=0
            compare_workers=adaptive_workers(len(reps))
            def candidates():
                for n,i in enumerate(reps):
                    check();h=int(rgb_hashes[n])
                    for position in hash_candidates(rgb_hashes[:n],h,candidate_radius):
                        check();j=reps[position];a,b=records[i],records[j]
                        distance=(h^int(b['rgbhash'])).bit_count()
                        resize=options.get('resize',True) and (a['w'],a['h'])!=(b['w'],b['h'])
                        similar=options.get('similar',True) and distance<=radius and abs(np.log((a['w']/a['h'])/(b['w']/b['h'])))<=.08
                        if (resize or similar) and compatible(i,j,stretch=True):
                            yield i,j,distance,resize,similar
                    if n%100==0:progress('比较外观',n,len(reps))
            def compare_batch(batch,pool):
                nonlocal verified
                resize_jobs=[];resize_results={};requests=[]
                for offset,(i,j,distance,resize,similar) in enumerate(batch):
                    check()
                    if not resize:continue
                    a,b=records[i],records[j]
                    try:
                        pair=tuple('rgba-box-v1:'+v for v in sorted((a['pixelhash'],b['pixelhash'])))
                        cached=None if options.get('refresh') else db.execute('SELECT matched FROM resize_pairs WHERE a=? AND b=?',pair).fetchone()
                        if cached is None:
                            resize_jobs.append((offset,pair,a,b))
                            requests.extend((row,comparison_size(a,b)) for row in (a,b))
                        else:resize_results[offset]=(bool(cached[0]),None,None)
                    except Exception as exc:resize_results[offset]=(False,None,exc)
                resize_pixels.prepare_many(requests,pool,check)
                futures=[(offset,pair,pool.submit(resize_match,a,b,resize_pixels,with_score=True,strict_alpha=strict_alpha)) for offset,pair,a,b in resize_jobs]
                for offset,pair,future in futures:
                    check()
                    try:
                        matched,score=future.result();resize_results[offset]=(matched,score,None)
                        db.execute('INSERT OR REPLACE INTO resize_pairs VALUES(?,?,?)',(*pair,int(matched)));verified+=1
                        if verified%256==0:db.commit()
                    except Cancelled:raise
                    except Exception as exc:resize_results[offset]=(False,None,exc)
                for offset,(i,j,distance,resize,similar) in enumerate(batch):
                    check();a,b=records[i],records[j];entry=resize_results.get(offset);resized=False;added=False;score=None
                    if entry is not None:
                        matched,score,exc=entry
                        if exc:errors.append(a['path']+' / '+b['path']+': '+str(exc))
                        elif matched:
                            kind='等比例缩放候选' if a['w']*b['h']==b['w']*a['h'] else '非等比例拉伸候选'
                            added=add(i,j,kind,1-distance/64);resized=True
                    if not resized and similar:added=add(i,j,'外观相似',1-distance/64)
                    if added and score is not None:fresh_scores[content_pair(a,b,strict_alpha)]=score
                    if limited:break
            with ThreadPoolExecutor(max_workers=compare_workers) as compare_pool:
                batch=[]
                # Candidate batches span images while bounding queued work even
                # when a single image has thousands of hash neighbours.
                for candidate in candidates():
                    batch.append(candidate)
                    if len(batch)>=compare_workers*4:
                        compare_batch(batch,compare_pool);batch=[]
                        if limited:break
                if batch and not limited:compare_batch(batch,compare_pool)
        timings['外观候选']=round(time.perf_counter()-appearance_started,3)
        ai_started=time.perf_counter()
        if options.get('ai'):
            ids,vectors,ai_errors,ai_stats=extract_ai(records,reps,db,options,progress,check,prepared)
            errors.extend(ai_errors)
            for i in ids:
                if i not in thumbs:thumbs[i]=np.frombuffer(records[i]['thumb'],np.uint8).reshape(24,24,4)
            if vectors:
                matrix=np.stack(vectors); rng=np.random.default_rng(42)
                bits=(matrix@rng.standard_normal((matrix.shape[1],96),dtype=np.float32)>0).reshape(-1,8,12)
                keys=(bits*(2**np.arange(12))).sum(axis=2); tables=[defaultdict(list) for _ in range(8)]
                for n,i in enumerate(ids):
                    check(); candidates=set()
                    for t in range(8): candidates.update(tables[t][int(keys[n,t])])
                    candidates=sorted(candidates)
                    for start in range(0,len(candidates),512):
                        check(); batch=candidates[start:start+512]
                        scores=ai_candidate_scores(matrix,batch,n,float(options.get('threshold',.97)))
                        for offset in np.flatnonzero(scores>=float(options.get('threshold',.97))):
                            j=batch[offset]
                            if compatible(i,ids[j],True):add(i,ids[j],'AI 近似',scores[offset])
                        if limited: break
                    for t in range(8): tables[t][int(keys[n,t])].append(n)
                    if n%100==0: progress('AI 近似检索',n,len(ids))
                    if limited: break
        if options.get('ai'): timings['AI检索']=round(time.perf_counter()-ai_started,3)
        rank={'文件完全重复':0,'RGBA 像素重复':1,'等比例缩放候选':2,'非等比例拉伸候选':3,'外观相似':4,'AI 近似':5}
        groups.sort(key=lambda g:(rank[g['kind']],-len(g['ids']),-g['score']))
        for n,g in enumerate(groups):
            g['id']=n
            g['keep']=max(g['ids'],key=lambda i:(records[i]['size'],-i))
        for r in records: r.pop('thumb',None)
        result=dict(roots=normalized,options=options,records=records,groups=groups,errors=errors,scanned=len(paths),limited=limited,finished=time.strftime('%Y-%m-%d %H:%M:%S'))
        if options.get('ai'):result['ai_stats']=ai_stats
        score_started=time.perf_counter()
        result=score_contents(result,db,progress,cancel,resize_pixels,fresh_scores)
        timings['内容相似度']=round(time.perf_counter()-score_started,3)
        timings['解码复用']=file_reuse.hits
        timings['总耗时']=round(time.perf_counter()-scan_started,3);timings['结果复用']=False
        result['timings']=timings
        check();store_scan_cache(db,result)
        return result
    finally:
        if prepared:prepared.close()
        db.commit(); db.close()
