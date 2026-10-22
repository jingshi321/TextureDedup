"""Windows desktop host. Owns one isolated temporary cache per launch."""
import sys
sys.dont_write_bytecode=True
import os, tempfile, secrets, json, shutil, time, threading, subprocess, ctypes, math
from pathlib import Path


def cleanup_cache(path,marker):
    path=Path(path)
    # Never follow a supplied path or delete anything outside our exact temp child.
    if path.is_symlink() or (hasattr(path,'is_junction') and path.is_junction()): return False
    path=path.resolve()
    if path.parent!=Path(tempfile.gettempdir()).resolve() or not path.name.startswith('TextureDedup-'): return False
    try:
        if (path/'.owned-cache').read_text(encoding='ascii')!=marker: return False
    except OSError: return False
    for attempt in range(20):
        try:
            for child in path.iterdir():
                if child.name=='.owned-cache':continue
                if child.is_dir() and not child.is_symlink() and not (hasattr(child,'is_junction') and child.is_junction()):shutil.rmtree(child)
                elif child.is_dir() and hasattr(child,'is_junction') and child.is_junction():child.rmdir()
                else:child.unlink()
            (path/'.owned-cache').unlink();path.rmdir();return True
        except FileNotFoundError: return True
        except OSError: time.sleep(.25)
    return False


def cleanup_worker():
    path,marker,pid=sys.argv[2:5]
    kernel=ctypes.windll.kernel32
    kernel.OpenProcess.restype=ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_ulong]
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    handle=kernel.OpenProcess(0x00100000,False,int(pid))
    if handle:
        kernel.WaitForSingleObject(handle,120000)
        kernel.CloseHandle(handle)
    # WebView2 may release its storage shortly after the main process exits.
    for _ in range(12):
        if cleanup_cache(path,marker) or not Path(path).exists(): break
        time.sleep(1)


def window_geometry(work_area,dpi_scale=1.0):
    # The target is the physical outer window size. pywebview multiplies its
    # logical coordinates by the monitor scale, so convert the whole rectangle.
    scale=dpi_scale if dpi_scale>0 else 1.0
    left,top,right,bottom=work_area
    work_width=right-left;work_height=bottom-top
    width=min(2858,work_width,max(480,work_width-80))
    height=min(1650,work_height,max(480,work_height-80))
    physical=(left+max(0,(work_width-width)//2),top+max(0,(work_height-height)//2),width,height)
    if scale==1:return physical
    # WinForms truncates the scaled result; avoid losing a pixel to float rounding.
    return tuple(math.nextafter(value/scale,math.copysign(math.inf,value)) for value in physical)


def main():
    if len(sys.argv)>1 and sys.argv[1]=='--cleanup-cache':
        cleanup_worker();return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception: pass
    cache=Path(tempfile.mkdtemp(prefix='TextureDedup-'))
    original_tempdir=tempfile.tempdir
    marker=secrets.token_hex(32);(cache/'.owned-cache').write_text(marker,encoding='ascii')
    # Keep the original TEMP for ownership checks; all library temp work goes here.
    os.environ['TEXTUREDEDUP_CACHE_DIR']=str(cache)
    original_temp={key:os.environ.get(key) for key in ('TEMP','TMP')}
    scratch=cache/'tmp';scratch.mkdir()
    os.environ['TEMP']=os.environ['TMP']=str(scratch)
    tempfile.tempdir=str(scratch)
    server=None;http=None;window=None
    log=(cache/'desktop.log').open('a',encoding='utf-8')
    old_stdout,old_stderr=sys.stdout,sys.stderr;sys.stdout=sys.stderr=log
    try:
        import webview
        import server as backend
        server=backend
        http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        http.daemon_threads=False
        http_thread=threading.Thread(target=http.serve_forever,daemon=True);http_thread.start()
        class RECT(ctypes.Structure): _fields_=[('left',ctypes.c_long),('top',ctypes.c_long),('right',ctypes.c_long),('bottom',ctypes.c_long)]
        area=RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030,0,ctypes.byref(area),0):
            work_area=(area.left,area.top,area.right,area.bottom)
        else: work_area=(0,0,2858,1650)
        try: dpi_scale=ctypes.windll.user32.GetDpiForSystem()/96
        except Exception: dpi_scale=1.0
        x,y,width,height=window_geometry(work_area,dpi_scale)
        window=webview.create_window('TextureDedup',f'http://127.0.0.1:{http.server_port}/',width=width,height=height,x=x,y=y,min_size=(min(1100,width),min(700,height)))
        def pick_folder():
            value=window.create_file_dialog(webview.FileDialog.FOLDER)
            return value[0] if value else ''
        server.FOLDER_PICKER=pick_folder
        closing=threading.Event();ready_to_close=threading.Event()
        def on_closing():
            if ready_to_close.is_set(): return True
            server.CANCEL.set()
            if not server.STATE['running']: return True
            if not closing.is_set():
                closing.set()
                def finish_operation():
                    window.set_title('正在结束当前操作并清理缓存，请稍候…')
                    while server.STATE['running']: time.sleep(.2)
                    ready_to_close.set();window.destroy()
                threading.Thread(target=finish_operation,daemon=True).start()
            return False
        window.events.closing+=on_closing
        def smoke():
            report=Path(sys.argv[sys.argv.index('--smoke-test')+1]).resolve()
            info={'cache':str(cache),'url':f'http://127.0.0.1:{http.server_port}/'}
            try:
                for _ in range(120):
                    if window.evaluate_js('document.readyState')=='complete': break
                    time.sleep(.25)
                info['title']=window.evaluate_js('document.title')
                info['move_button']=window.evaluate_js('document.getElementById("moveSelect").textContent')
                info['default_similarity']=window.evaluate_js('document.getElementById("minSimilarity").value')
                info['window_geometry']={'x':x,'y':y,'width':width,'height':height,'dpi_scale':dpi_scale,'work_area':list(work_area)}
                handle=ctypes.c_void_p(window.native.Handle.ToInt64())
                bounds=RECT()
                if not ctypes.windll.user32.GetWindowRect(handle,ctypes.byref(bounds)):
                    raise OSError('无法读取原生窗口尺寸')
                info['physical_window']={'x':bounds.left,'y':bounds.top,'width':bounds.right-bounds.left,'height':bounds.bottom-bounds.top,'dpi':ctypes.windll.user32.GetDpiForWindow(handle)}
                expected=tuple(round(value*dpi_scale) for value in (x,y,width,height))
                if (bounds.left,bounds.top,bounds.right-bounds.left,bounds.bottom-bounds.top)!=expected:
                    raise AssertionError('实际窗口位置或尺寸与预期不符：'+str(info['physical_window']))
                info['viewport']=json.loads(window.evaluate_js('JSON.stringify({width:innerWidth,height:innerHeight,outerWidth,outerHeight,dpr:devicePixelRatio,screenWidth:screen.width,screenHeight:screen.height,availWidth:screen.availWidth,availHeight:screen.availHeight,screenX,screenY})'))
                # Exercise actual bundled dependencies, model and HTTP scan.
                from PIL import Image
                import http.client as http_client
                fixtures=cache/'smoke-fixtures';fixtures.mkdir()
                Image.new('RGBA',(32,32),(50,100,150,200)).save(fixtures/'smoke.png')
                shutil.copyfile(fixtures/'smoke.png',fixtures/'smoke_copy.png')
                def post(endpoint,data):
                    connection=http_client.HTTPConnection('127.0.0.1',http.server_port)
                    try:
                        connection.request('POST','/'+endpoint,json.dumps(data).encode(),{'X-Token':server.TOKEN})
                        response=connection.getresponse()
                        if response.status!=200: raise RuntimeError(response.read().decode('utf-8'))
                        return json.load(response)
                    finally: connection.close()
                # Engine deliberately skips its own cache; the explicit fixture root
                # uses the scanner's standard fixture hook via a separate folder.
                from engine import FeatureModel
                features=FeatureModel().encode(str(fixtures/'smoke.png'))
                info['ai_feature_dimensions']=int(features.size)
                info['ai_provider']=FeatureModel().provider
                import send2trash
                info['recycle_available']=callable(send2trash.send2trash)
                if '--smoke-source' in sys.argv:
                    source=sys.argv[sys.argv.index('--smoke-source')+1]
                    post('api/scan',{'roots':[source],'options':{'similar':True,'resize':True,'alpha':True,'ai':True,'ai_vfx_only':True}})
                    while server.STATE['running']:time.sleep(.2)
                    info['scan_error']=server.STATE['error']
                    info['scan_images']=len(server.STATE['result']['records'])
                    info['scan_groups']=len(server.STATE['result']['groups'])
                    info['ai_stats']=server.STATE['result'].get('ai_stats')
                    info['selected']=post('api/move-select',{'source':source})['total']
                # Recycle only disposable images created inside this launch's owned cache.
                from engine import scan
                recycle_fixtures=cache/'smoke-recycle-fixtures';recycle_fixtures.mkdir()
                for side in (32,64):
                    Image.new('RGB',(side,side),(20,40,60)).save(recycle_fixtures/(str(side)+'.bmp'))
                result=scan([str(recycle_fixtures)],dict(similar=False,resize=True,alpha=True),lambda *args:None,threading.Event(),cache/'smoke-recycle.sqlite')
                assert all(not row['filehash'] for row in result['records'])
                server.STATE.update(result=result,running=False)
                group=next(g for g in result['groups'] if g['kind']=='等比例缩放候选')
                plan=post('api/cleanup-preview',{'selections':[{'group':group['id'],'keep':group['keep']}]})
                assert len(plan['items'])==1
                outcome=post('api/cleanup-execute',{'token':plan['token']})
                assert len(outcome['completed'])==1 and not outcome['errors'],outcome
                assert not Path(plan['items'][0]['path']).exists()
                assert Path(plan['keepers'][0]['path']).is_file()
                assert server.STATE['result']['scanned']==1 and not server.STATE['result']['groups']
                info['recycle_test']={'completed':len(outcome['completed']),'backend':send2trash.send2trash.__module__,'keeper_preserved':True,'counts_updated':True,'fixtures_only':True}
                info['ok']=True
            except Exception as e:
                import traceback
                info.update(ok=False,error=traceback.format_exc())
            finally:
                report.write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8');window.destroy()
        kwargs={'gui':'edgechromium','private_mode':True,'storage_path':str(cache/'webview')}
        webview.start(smoke if '--smoke-test' in sys.argv else None,**kwargs)
    except Exception:
        import traceback
        message=traceback.format_exc();log.write(message);log.flush()
        ctypes.windll.user32.MessageBoxW(None,'软件启动失败：\n'+message,'TextureDedup',0x10)
    finally:
        if server:
            server.CANCEL.set()
            while server.STATE['running']:time.sleep(.2)
        if http:http.shutdown();http.server_close()
        sys.stdout,sys.stderr=old_stdout,old_stderr;log.close()
        for key,value in original_temp.items():
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value
        tempfile.tempdir=original_tempdir
        if not cleanup_cache(cache,marker):
            args=[sys.executable]
            if not getattr(sys,'frozen',False): args+=['-B',str(Path(__file__).resolve())]
            args+=['--cleanup-cache',str(cache),marker,str(os.getpid())]
            env=os.environ.copy();env['PYINSTALLER_RESET_ENVIRONMENT']='1'
            subprocess.Popen(args,creationflags=subprocess.CREATE_NO_WINDOW,env=env)


if __name__=='__main__':main()
