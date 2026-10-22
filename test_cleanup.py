import sys
sys.dont_write_bytecode=True
import unittest, threading, time
from engine import SAVE, scan, read_image
from cleanup import make_plan, execute, prune_result, verify
from PIL import Image

class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.folder=SAVE/('cleanup-test-'+str(time.time_ns()));self.folder.mkdir()
        im=Image.new('RGBA',(64,64),(0,0,0,0))
        for x in range(64):
            for y in range(64): im.putpixel((x,y),(x*4,y*4,((x//8+y//8)%2)*200,255 if x>15 else 0))
        for name,size in [('base',(64,64)),('scale',(128,128)),('stretch',(128,32))]:
            im.resize(size,Image.Resampling.NEAREST).save(self.folder/(name+'.png'))
    @staticmethod
    def mock_recycle(paths):
        from pathlib import Path
        for path in paths:Path(path).unlink()
    def test_resize_classes(self):
        r=scan([str(self.folder)],dict(similar=False,resize=True,alpha=True,distance=6),lambda *x:None,threading.Event(),SAVE/'cleanup-test.sqlite')
        kinds={g['kind'] for g in r['groups']}
        self.assertIn('等比例缩放候选',kinds); self.assertIn('非等比例拉伸候选',kinds)
        for g in r['groups']:
            self.assertEqual(r['records'][g['keep']]['size'],max(r['records'][i]['size'] for i in g['ids']))
    def test_cleanup_accepts_deferred_hashes_from_real_scan(self):
        r=scan([str(self.folder)],dict(similar=False,resize=True,alpha=True,distance=6),lambda *x:None,threading.Event(),self.folder/'deferred.sqlite')
        g=next(g for g in r['groups'] if g['kind']=='等比例缩放候选')
        plan=make_plan(r,[dict(group=g['id'],keep=g['keep'])])
        self.assertTrue(plan['items'])
        self.assertTrue(all(not row['filehash'] for row in plan['keepers']+plan['items']))
        calls=[]
        def recycle(paths):calls.extend(paths);self.mock_recycle(paths)
        outcome=execute(plan,recycle=recycle)
        self.assertEqual(calls,[row['path'] for row in plan['items']])
        self.assertFalse(outcome['errors'])
        self.assertTrue(all(not row['filehash'] for row in r['records']))
    def test_deferred_hash_checks_pixels_without_computing_a_file_hash(self):
        import os
        for role in ('keeper','target','after_verification','missing_pixelhash'):
            with self.subTest(role=role):
                rows=[]
                for i,side in enumerate((64,32)):
                    path=self.folder/(role+str(i)+'.bmp')
                    Image.new('RGB',(side,side),(20,40,60)).save(path)
                    rows.append(read_image(str(path),full_hash=False))
                r=dict(records=rows,groups=[dict(id=0,ids=[0,1],kind='等比例缩放候选')])
                plan=make_plan(r,[dict(group=0,keep=0)])
                row=plan['items'][0] if role=='target' else plan['keepers'][0]
                if role=='after_verification':verify(row)
                if role=='missing_pixelhash':row['pixelhash']=''
                else:
                    from pathlib import Path
                    path=Path(row['path']);data=bytearray(path.read_bytes());data[-1]^=255;path.write_bytes(data)
                    os.utime(path,ns=(path.stat().st_atime_ns,row['mtime']))
                    self.assertEqual((path.stat().st_size,path.stat().st_mtime_ns),(row['size'],row['mtime']))
                calls=[]
                with self.assertRaisesRegex(ValueError,'内容已变化|校验值'):execute(plan,recycle=calls.append)
                self.assertEqual(calls,[])
    def fixture(self):
        rows=[]
        for i,size in enumerate([300,200,100,100]):
            p=self.folder/(str(i)+'.bin');p.write_bytes(bytes([i])*size)
            import hashlib
            s=p.stat();rows.append(dict(path=str(p),size=size,mtime=s.st_mtime_ns,filehash=hashlib.sha256(p.read_bytes()).hexdigest()))
        return dict(records=rows,groups=[dict(id=0,ids=[0,1],kind='RGBA 像素重复'),dict(id=1,ids=[1,2,3],kind='等比例缩放候选'),dict(id=2,ids=[2,3],kind='文件完全重复')])
    def test_overlap_and_equal(self):
        r=self.fixture(); p=make_plan(r,[dict(group=0,keep=0),dict(group=1,keep=1)])
        self.assertEqual([x['index'] for x in p['items']],[1,2,3])
        self.assertEqual([x['index'] for x in p['keepers']],[0])
        self.assertEqual([row['index'] for row in make_plan(r,[dict(group=2,keep=2)])['items']],[3])
        called=[]
        def recycle(paths):called.extend(paths);self.mock_recycle(paths)
        outcome=execute(p,recycle=recycle)
        self.assertEqual(len(called),3);self.assertFalse(outcome['errors'])
        self.assertTrue(__import__('pathlib').Path(r['records'][0]['path']).exists())
        self.assertTrue(all(not __import__('pathlib').Path(r['records'][i]['path']).exists() for i in (1,2,3)))
    def test_changed_keeper_blocks_everything(self):
        r=self.fixture();p=make_plan(r,[dict(group=0,keep=0)])
        from pathlib import Path
        Path(r['records'][0]['path']).write_bytes(b'changed')
        called=[]
        with self.assertRaises(ValueError):execute(p,recycle=called.append)
        self.assertEqual(called,[])
    def test_small_keeper_rejected(self):
        with self.assertRaises(ValueError):make_plan(self.fixture(),[dict(group=0,keep=1)])
    def test_all_marked_files_including_equal_sizes_are_targets(self):
        records=[dict(path=f'C:/fixtures/{i}.png',size=100,mtime=1,filehash='a',pixelhash='b') for i in range(11520)]
        groups=[dict(id=i,ids=[2*i,2*i+1],kind='文件完全重复') for i in range(5760)]
        selections=[dict(group=i,keep=2*i) for i in range(5760)]
        plan=make_plan(dict(records=records,groups=groups),selections)
        self.assertEqual(len(plan['items']),5760)
        self.assertEqual(len(plan['keepers']),5760)
    def test_targets_are_verified_once_and_recycled_in_batches(self):
        from unittest.mock import patch
        plan=make_plan(self.fixture(),[dict(group=1,keep=1)])
        calls=[]
        with patch('cleanup.verify') as verify:
            def recycle(paths):calls.append(paths);self.mock_recycle(paths)
            outcome=execute(plan,recycle=recycle)
        self.assertFalse(outcome['errors'])
        self.assertEqual(verify.call_count,len(plan['keepers'])+len(plan['items']))
        self.assertEqual(calls,[[row['path'] for row in plan['items']]])
    def test_same_size_unselected_files_recycle_across_batches(self):
        import hashlib
        rows=[];groups=[];selections=[];payload=b'x';digest=hashlib.sha256(payload).hexdigest()
        for i in range(130):
            pair=[]
            for side in range(2):
                path=self.folder/f'batch-{i}-{side}.bin';path.write_bytes(payload);stat=path.stat()
                pair.append(len(rows));rows.append(dict(path=str(path),size=1,mtime=stat.st_mtime_ns,filehash=digest))
            groups.append(dict(id=i,ids=pair,kind='文件完全重复'))
            selections.append(dict(group=i,keep=pair[0]))
        plan=make_plan(dict(records=rows,groups=groups),selections);batches=[]
        outcome=execute(plan,recycle=lambda paths:(batches.append(len(paths)),self.mock_recycle(paths)))
        self.assertEqual([len(plan['items']),*batches],[130,64,64,2])
        self.assertEqual((len(outcome['completed']),outcome['errors']),(130,[]))
    def test_bulk_largest_includes_similar_groups_without_opt_in(self):
        import server
        r=self.fixture()
        r['groups'].append(dict(id=3,ids=[0,2],kind='外观相似',score=1))
        r['groups'].append(dict(id=4,ids=[1,3],kind='AI 近似',score=1))
        for g in r['groups']:g.setdefault('score',1)
        selections=server.largest_selections(r,{})
        self.assertEqual(len(selections),5)
        plan=server.preview_selection(r,selections)
        self.assertEqual((plan['similar_groups'],plan['skipped_groups']),(2,0))
        candidates=[dict(group=3,keep=0),dict(group=4,keep=1)]
        preview=server.preview_selection(r,candidates)
        self.assertEqual((preview['similar_groups'],preview['skipped_groups'],preview['similar_items']),(2,0,2))
        self.assertEqual({row['index'] for row in preview['items']},{2,3})
        self.assertEqual({row['index'] for row in make_plan(r,candidates)['items']},{2,3})
    def test_current_filter_ignores_hidden_candidates_and_keepers(self):
        import server
        r=self.fixture()
        for g in r['groups']:g['score']=1
        selected=[dict(group=0,keep=0),dict(group=1,keep=1)]
        plan=server.preview_selection(r,selected,{'kind':['RGBA 像素重复'],'min_similarity':['100']})
        self.assertEqual({row['index'] for row in plan['items']},{1})
        self.assertEqual({row['index'] for row in plan['keepers']},{0})
        direct=make_plan(r,selected,scope={0})
        self.assertEqual({row['index'] for row in direct['items']},{1})
        self.assertEqual({row['index'] for row in direct['keepers']},{0})
        plan=server.preview_selection(r,selected,{'kind':['等比例缩放候选'],'min_similarity':['100']})
        self.assertEqual({row['index'] for row in plan['items']},{2,3})
        self.assertEqual({row['index'] for row in plan['keepers']},{1})
        self.assertEqual(plan['skipped_groups'],0)
        r['groups'][1]['score']=.99
        plan=server.preview_selection(r,selected,{'min_similarity':['100']})
        self.assertEqual({row['index'] for row in plan['items']},{1})
        with self.assertRaises(ValueError):
            server.preview_selection(r,selected,{'kind':['等比例缩放候选'],'min_similarity':['100']})
        with self.assertRaises(ValueError):
            server.preview_selection(r,selected,{'min_similarity':['nan']})
    def test_partial_failure_stops(self):
        r=self.fixture();p=make_plan(r,[dict(group=1,keep=1)])
        calls=[]
        def recycle(paths):
            calls.extend(paths)
            from pathlib import Path
            Path(paths[0]).unlink()
            raise OSError('模拟回收站失败')
        outcome=execute(p,recycle=recycle)
        self.assertEqual(len(outcome['completed']),1);self.assertEqual(len(outcome['errors']),1)
    def test_progress_includes_validation_and_only_successful_recycles(self):
        plan=make_plan(self.fixture(),[dict(group=1,keep=1)])
        events=[]
        outcome=execute(plan,recycle=self.mock_recycle,progress=lambda *args:events.append(args))
        self.assertFalse(outcome['errors'])
        self.assertEqual([done for _,done,_ in events],[0,1,2,3,3,5])
        self.assertEqual({total for _,_,total in events},{5})
        self.assertEqual(events[0][0],'正在核对文件')
        self.assertEqual(events[-1][0],'正在移入回收站')
        plan=make_plan(self.fixture(),[dict(group=1,keep=1)])
        calls=[];events=[]
        def fail_second(paths):
            calls.extend(paths)
            from pathlib import Path
            Path(paths[0]).unlink()
            raise OSError('模拟失败')
        outcome=execute(plan,recycle=fail_second,progress=lambda *args:events.append(args))
        self.assertEqual((len(outcome['completed']),len(outcome['errors'])),(1,1))
        self.assertEqual(events[-1][1:],(4,5))
    def test_prune_preserves_ids_and_does_not_mutate_original(self):
        import copy,sqlite3
        from engine import store_scan_cache
        r=self.fixture()
        r.update(scanned=4,errors=[],groups=[
            dict(id=7,ids=[0,1,2],keep=1,kind='RGBA 像素重复',score=1),
            dict(id=13,ids=[1,3],keep=1,kind='等比例缩放候选',score=.99),
            dict(id=21,ids=[2,3],keep=2,kind='文件完全重复',score=1)])
        before=copy.deepcopy(r)
        self.assertIs(prune_result(r,[]),r)
        updated=prune_result(r,[r['records'][1]['path']])
        self.assertEqual(r,before)
        self.assertIsNone(updated['records'][1])
        self.assertEqual([g['id'] for g in updated['groups']],[7,21])
        self.assertEqual(updated['groups'][0]['ids'],[0,2])
        self.assertEqual(updated['groups'][0]['keep'],0)
        self.assertEqual(updated['groups'][1],r['groups'][2])
        self.assertEqual((updated['scanned'],updated['revision']),(3,1))
        self.assertIs(prune_result(updated,[r['records'][1]['path']]),updated)
        final=prune_result(updated,[r['records'][2]['path']])
        self.assertEqual((final['groups'],final['scanned'],final['revision']),([],2,2))
        with sqlite3.connect(':memory:') as db:
            store_scan_cache(db,final)
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE name='scan_results'").fetchall(),[])
    def test_http_cleanup_updates_counts_groups_cache_and_partial_success(self):
        import server,json,urllib.request,urllib.error,hashlib,copy
        from unittest.mock import patch
        from pathlib import Path
        result=self.fixture()
        for i,size in [(4,500),(5,50)]:
            path=self.folder/(str(i)+'.bin');path.write_bytes(bytes([i])*size)
            result['records'].append(dict(path=str(path),size=size,mtime=path.stat().st_mtime_ns,filehash=hashlib.sha256(path.read_bytes()).hexdigest()))
        result['groups'].append(dict(id=40,ids=[4,5],keep=4,kind='RGBA 像素重复'))
        for g in result['groups']:g['score']=1
        result.update(errors=[],options=dict(alpha=True),scanned=6,limited=False)
        original=copy.deepcopy(result)
        state=dict(server.STATE);server.STATE.update(result=result,running=False)
        http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        threading.Thread(target=http.serve_forever,daemon=True).start()
        base='http://127.0.0.1:'+str(http.server_port)
        def request(path,data=None):
            req=urllib.request.Request(base+path,data=None if data is None else json.dumps(data).encode(),headers={'X-Token':server.TOKEN})
            with urllib.request.urlopen(req) as response:return json.load(response)
        try:
            with patch('server.SAVE',self.folder),patch('server.execute') as recycle,patch('server.os.startfile') as launch:
                first=request('/api/cleanup-preview',dict(selections=[dict(group=0,keep=0)]))
                self.assertEqual([x['index'] for x in first['items']],[1])
                recycle.return_value=dict(completed=[result['records'][1]['path']],errors=[])
                request('/api/cleanup-execute',dict(token=first['token']))
                summary=request('/api/status')['summary']
                self.assertEqual((summary['images'],summary['groups'],summary['revision']),(5,3,1))
                page=request('/api/results?revision=0')
                self.assertEqual(page['group_ids'],[1,2,40])
                self.assertEqual(page['removed_ids'],[1])
                self.assertEqual(page['total'],3)
                self.assertEqual(page['counts'],{'等比例缩放候选':1,'文件完全重复':1,'RGBA 像素重复':1})
                self.assertTrue(all(item['index']!=1 for group in page['groups'] for item in group['items']))
                surviving=next(g for g in page['groups'] if g['id']==1)
                self.assertEqual(surviving['ids'],[2,3])
                self.assertEqual(surviving['keep'],2)
                self.assertNotIn('group_ids',request('/api/results?revision=1'))
                self.assertNotIn('removed_ids',request('/api/results?revision=1'))
                cached=json.loads((self.folder/'last-result.json').read_text(encoding='utf-8'))
                self.assertIsNone(cached['records'][1])
                for path,data in [('/api/thumb?id=1',None),('/api/open-image',{'id':1}),('/api/open',{'id':1})]:
                    with self.assertRaises(urllib.error.HTTPError):request(path,data)
                launch.assert_not_called()
                second=request('/api/cleanup-preview',dict(selections=[dict(group=40,keep=4)]))
                self.assertEqual([x['index'] for x in second['items']],[5])
                recycle.return_value=dict(completed=[result['records'][5]['path']],errors=[])
                request('/api/cleanup-execute',dict(token=second['token']))
                self.assertEqual(request('/api/results')['group_ids'],[1,2])
                self.assertEqual(request('/api/status')['summary']['images'],4)
                # Equal-size survivors remain grouped and are never scheduled for deletion.
                equal=request('/api/cleanup-preview',dict(selections=[dict(group=1,keep=2)]))
                self.assertEqual([row['index'] for row in equal['items']],[3])
                # A failed target stays visible; only the successful path is removed.
                server.STATE.update(result=original)
                partial=request('/api/cleanup-preview',dict(selections=[dict(group=1,keep=1)]))
                recycle.return_value=dict(completed=[original['records'][2]['path']],errors=['模拟失败'])
                outcome=request('/api/cleanup-execute',dict(token=partial['token']))
                self.assertEqual(outcome['errors'],['模拟失败'])
                self.assertIsNotNone(server.STATE['result']['records'][3])
                self.assertEqual(next(g for g in request('/api/results')['groups'] if g['id']==1)['ids'],[1,3])
                # An entirely unsuccessful operation must leave the result intact.
                prior=server.STATE['result']
                retry=request('/api/cleanup-preview',dict(selections=[dict(group=1,keep=1)]))
                recycle.return_value=dict(completed=[],errors=['模拟失败'])
                request('/api/cleanup-execute',dict(token=retry['token']))
                self.assertIs(server.STATE['result'],prior)
                self.assertTrue(all(Path(row['path']).exists() for row in original['records']))
        finally:
            http.shutdown();http.server_close();server.STATE.clear();server.STATE.update(state);server.PLANS.clear()
    def test_similar_cleanup_executes_without_confirmation_and_token_is_single_use(self):
        import server,json,urllib.request,urllib.error
        from unittest.mock import patch
        result=self.fixture()
        for g in result['groups']:g['score']=1
        server.STATE.update(result=result,running=False)
        http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        threading.Thread(target=http.serve_forever,daemon=True).start()
        def post(path,data):
            req=urllib.request.Request('http://127.0.0.1:'+str(http.server_port)+path,data=json.dumps(data).encode(),headers={'X-Token':server.TOKEN,'Content-Type':'application/json'})
            with urllib.request.urlopen(req) as response:return json.load(response)
        try:
            remembered=[dict(group=0,keep=0),dict(group=1,keep=1)]
            result['groups'].append(dict(id=3,ids=[0,2],kind='外观相似',score=1))
            included=post('/api/cleanup-preview',dict(selections=[dict(group=3,keep=0)]))
            self.assertEqual(([r['index'] for r in included['items']],included['similar_items'],included['skipped_groups']),([2],1,0))
            scoped=post('/api/cleanup-preview',dict(selections=remembered,kind='RGBA 像素重复',min_similarity=100))
            self.assertEqual([row['index'] for row in scoped['items']],[1])
            scoped=post('/api/cleanup-preview',dict(selections=remembered,kind='等比例缩放候选',min_similarity=100))
            self.assertEqual({row['index'] for row in scoped['items']},{2,3})
            plan=post('/api/cleanup-preview',dict(selections=[dict(group=0,keep=0)]))
            self.assertEqual(len(plan['items']),1)
            with patch('server.execute',return_value=dict(completed=[],errors=[])) as fake:
                post('/api/cleanup-execute',dict(token=plan['token']))
                fake.assert_called_once()
                with self.assertRaises(urllib.error.HTTPError):post('/api/cleanup-execute',dict(token=plan['token']))
        finally:http.shutdown();http.server_close()

    def test_http_progress_is_readable_during_recycle_and_failures_release_busy_state(self):
        import server,json,urllib.request,urllib.error,concurrent.futures
        from unittest.mock import patch
        from pathlib import Path
        result=self.fixture()
        for g in result['groups']:g['score']=1
        result.update(errors=[],options=dict(alpha=True),scanned=4,limited=False)
        state=dict(server.STATE);status=dict(server.CLEANUP_STATUS)
        server.STATE.update(result=result,running=False)
        http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        threading.Thread(target=http.serve_forever,daemon=True).start()
        base='http://127.0.0.1:'+str(http.server_port)
        entered=threading.Event();release=threading.Event()
        def request(path,data=None):
            req=urllib.request.Request(base+path,data=None if data is None else json.dumps(data).encode(),headers={'X-Token':server.TOKEN})
            with urllib.request.urlopen(req,timeout=5) as response:return json.load(response)
        def recycle(paths):
            entered.set()
            if not release.wait(5):raise TimeoutError('测试未释放回收操作')
            self.mock_recycle(paths)
        try:
            with patch('server.SAVE',self.folder),patch('server.execute',side_effect=lambda plan,progress:execute(plan,recycle=recycle,progress=progress)):
                plan=request('/api/cleanup-preview',dict(selections=[dict(group=1,keep=1)]))
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    task=pool.submit(request,'/api/cleanup-execute',dict(token=plan['token']))
                    try:
                        self.assertTrue(entered.wait(5))
                        progress=request('/api/cleanup-status')
                        self.assertTrue(progress['running'])
                        self.assertEqual((progress['done'],progress['total'],progress['files'],progress['token']),(3,5,2,plan['token']))
                        self.assertEqual(request('/api/status')['operation'],'cleanup')
                        for path,data in [('/api/scan',dict(roots=[str(self.folder)])),('/api/cleanup-preview',dict(selections=[dict(group=0,keep=0)])),('/api/move-select',dict(source=str(self.folder))),('/api/cleanup-execute',dict(token=plan['token']))]:
                            with self.assertRaises(urllib.error.HTTPError):request(path,data)
                    finally:release.set()
                    self.assertEqual(len(task.result(timeout=5)['completed']),2)
                progress=request('/api/cleanup-status')
                self.assertEqual((progress['running'],progress['done'],progress['total']),(False,5,5))
                self.assertFalse(request('/api/status')['running'])
                server.STATE.update(result=result)
                plan=request('/api/cleanup-preview',dict(selections=[dict(group=0,keep=0)]))
                Path(result['records'][0]['path']).write_bytes(b'changed')
                with self.assertRaises(urllib.error.HTTPError):request('/api/cleanup-execute',dict(token=plan['token']))
                self.assertFalse(request('/api/status')['running'])
                progress=request('/api/cleanup-status')
                self.assertFalse(progress['running'])
                self.assertIn('文件已变化',progress['error'])
        finally:
            release.set();http.shutdown();http.server_close()
            server.STATE.clear();server.STATE.update(state)
            server.CLEANUP_STATUS.clear();server.CLEANUP_STATUS.update(status);server.PLANS.clear()

if __name__=='__main__':unittest.main(verbosity=2)
