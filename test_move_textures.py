import sys
sys.dont_write_bytecode=True
import errno,time,unittest,json,threading,urllib.request,urllib.error
from unittest.mock import patch
from pathlib import Path
from PIL import Image
import engine,move_textures as moving

class MoveTests(unittest.TestCase):
    def setUp(self):
        self.folder=engine.SAVE/('move-test-'+str(time.time_ns()));self.folder.mkdir()
        self.src=self.folder/'source';self.src.mkdir();self.dst=self.folder/'target'
        self.logs=self.folder/'logs';self.logs.mkdir()
        self.override=patch.object(moving,'SAVE',self.logs);self.override.start();self.addCleanup(self.override.stop)
        (self.src/'fx').mkdir()
        Image.new('RGBA',(8,8),(1,2,3,4)).save(self.src/'fx'/'a.png')
        (self.src/'notes.txt').write_text('keep')
    def plan(self):return moving.make_move_plan(str(self.src),str(self.dst))
    def test_preview_and_move_preserves_structure_and_other_files(self):
        original=(self.src/'fx'/'a.png').read_bytes();p=self.plan()
        self.assertEqual(len(p['items']),1);self.assertFalse(self.dst.exists())
        outcome=moving.execute_move(p)
        self.assertEqual(outcome['completed'],1);self.assertEqual(outcome['errors'],[])
        self.assertEqual((self.dst/'fx'/'a.png').read_bytes(),original)
        self.assertTrue((self.src/'notes.txt').exists());self.assertTrue((self.src/'fx').is_dir())
    def test_conflicts_and_modified_source_stop_before_moving(self):
        (self.dst/'fx').mkdir(parents=True);(self.dst/'fx'/'a.png').write_bytes(b'original destination')
        p=self.plan();self.assertEqual(p['items'],[]);self.assertEqual(len(p['skipped']),1)
        Image.new('RGBA',(8,8),(5,6,7,8)).save(self.src/'b.png');p=self.plan()
        (self.src/'b.png').write_bytes(b'changed')
        with self.assertRaises(ValueError):moving.execute_move(p)
        self.assertEqual((self.dst/'fx'/'a.png').read_bytes(),b'original destination')
        self.assertTrue((self.src/'b.png').exists())
    def test_cross_volume_verifies_copy(self):
        original=(self.src/'fx'/'a.png').read_bytes()
        with patch.object(moving.os,'rename',side_effect=OSError(errno.EXDEV,'different volume')):
            result=moving.execute_move(self.plan())
        self.assertEqual(result['completed'],1);self.assertEqual(result['errors'],[])
        self.assertEqual((self.dst/'fx'/'a.png').read_bytes(),original)
    def test_new_conflict_and_nested_destination_rejected(self):
        for target in (self.src,self.src/'inside',self.folder):
            with self.assertRaises(ValueError):moving.make_move_plan(str(self.src),str(target))
        p=self.plan();(self.dst/'fx').mkdir(parents=True);(self.dst/'fx'/'a.png').write_bytes(b'new')
        with self.assertRaises(ValueError):moving.execute_move(p)
        self.assertTrue((self.src/'fx'/'a.png').exists())

    def test_selection_snapshot_excludes_later_files(self):
        selected=moving.select_textures(str(self.src))
        Image.new('RGBA',(8,8)).save(self.src/'later.png')
        plan=moving.make_move_plan(str(self.src),str(self.dst),selected)
        self.assertEqual(len(plan['items']),1)
        self.assertTrue(plan['items'][0]['source'].endswith('a.png'))

    def test_vfx_selection_does_not_move_skill_icons(self):
        icons=self.src/'skillicons';icons.mkdir()
        Image.new('RGBA',(8,8)).save(icons/'lightning.png')
        misc=self.src/'misc';misc.mkdir()
        Image.new('RGBA',(8,8)).save(misc/'beam_orb_color.png')
        Image.new('RGBA',(8,8)).save(misc/'barbed_lightning.png')
        selected=moving.select_textures(str(self.src),vfx_only=True)
        self.assertEqual(len(selected['items']),1)
        self.assertTrue(selected['items'][0]['source'].endswith('fx\\a.png'))
        self.assertGreaterEqual(selected['excluded'],2)
        self.assertEqual(selected['unknown'],1)
        result=moving.execute_move(moving.make_move_plan(str(self.src),str(self.dst),selected))
        self.assertEqual(result['completed'],1)
        self.assertTrue((icons/'lightning.png').exists())

    def test_keyword_filter_includes_unknown_files_but_keeps_exclusions(self):
        from move_textures import selection_items
        misc=self.src/'misc';misc.mkdir()
        Image.new('RGBA',(8,8)).save(misc/'fireball.png')
        Image.new('RGBA',(8,8)).save(misc/'noise_01.png')
        icons=self.src/'skillicons';icons.mkdir()
        Image.new('RGBA',(8,8)).save(icons/'fire.png')
        materials=self.src/'materials';materials.mkdir()
        Image.new('RGBA',(8,8)).save(materials/'noise.png')
        selected=moving.select_textures(str(self.src),vfx_only=True)
        tags={item['keyword']:item['count'] for item in selected['keywords']}
        self.assertIn('fire',tags);self.assertIn('noise',tags)
        fire=selection_items(selected,['fire'])
        self.assertEqual([Path(row['source']).name for row in fire],['fireball.png'])
        noise=selection_items(selected,['noise'])
        self.assertEqual([Path(row['source']).name for row in noise],['noise_01.png'])
        self.assertEqual(selection_items(selected,[]),[])

    def test_default_keywords_match_auto_selected_tags_and_new_matches_sort_first(self):
        from move_textures import selection_items
        particles=self.src/'particles';particles.mkdir()
        misc=self.src/'misc';misc.mkdir()
        Image.new('RGBA',(8,8)).save(particles/'fire.png')
        Image.new('RGBA',(8,8)).save(misc/'fireball.png')
        Image.new('RGBA',(8,8)).save(misc/'magic_circle.png')
        Image.new('RGBA',(8,8)).save(misc/'energy.png')
        selected=moving.select_textures(str(self.src),vfx_only=True)
        default=set(selected['default_keywords'])
        self.assertTrue({tag for row in selected['items'] for tag in row['tags']}<=default)
        self.assertIn('fire',default)
        self.assertNotIn('energy',default)
        self.assertEqual([Path(row['source']).name for row in selection_items(selected,['fire'])],
                         ['fireball.png','fire.png'])
        self.assertEqual(selection_items(selected,[]) ,[])
        prioritized=selection_items(selected,['fire','magic'],['magic'])
        self.assertEqual(Path(prioritized[0]['source']).name,'magic_circle.png')
        prioritized=selection_items(selected,['fire','magic','energy'],['energy','magic'])
        self.assertEqual([Path(row['source']).name for row in prioritized[:2]],['energy.png','magic_circle.png'])
        for keywords,priority in [([],['magic']),(['fire'],['magic']),(['missing'],[]),(['fire'],'fire')]:
            with self.assertRaises(ValueError):selection_items(selected,keywords,priority)

    def test_move_dialog_has_drag_handle_and_fixed_actions(self):
        root=Path(__file__).resolve().parent
        html=(root/'index.html').read_text(encoding='utf-8')
        js=(root/'app.js').read_text(encoding='utf-8')
        self.assertIn('id="moveTitle"',html)
        self.assertIn('class="move-actions"',html)
        self.assertIn('id="selectAllMoveKeywords"',html)
        self.assertIn('id="defaultMoveKeywords"',html)
        self.assertIn('id="moveProgress"',html)
        self.assertIn('#moveList{flex:1 1 auto',js)
        self.assertIn('moveHandle.onpointerdown=e=>',js)
        self.assertIn("$('selectAllMoveKeywords').onclick=",js)
        self.assertIn("$('defaultMoveKeywords').onclick=",js)
        self.assertIn("moveDialog.addEventListener('cancel'",js)
        self.assertIn("updateMoveProgress(state.done,state.total)",js)
        self.assertIn("moveSelection=await api('/api/move-select',{source})",js)
        self.assertIn('来源已刷新，可继续选择标签移动',js)
        self.assertNotIn('关闭窗口（移动继续）',js)

    def test_brand_name_and_small_byline(self):
        root=Path(__file__).resolve().parent
        html=(root/'index.html').read_text(encoding='utf-8')
        desktop=(root/'desktop.py').read_text(encoding='utf-8')
        self.assertIn('<title>TextureDedup By 月华</title>',html)
        self.assertIn('<h1>TextureDedup <small class="brand-by">By 月华</small></h1>',html)
        self.assertIn('.brand-by{font-size:14px',html)
        self.assertIn("create_window('TextureDedup'",desktop)

    def test_http_keyword_filter_drives_preview_and_move_plan(self):
        import server
        from pathlib import Path
        state=dict(server.STATE);move_selections=dict(server.MOVE_SELECTIONS);move_plans=dict(server.MOVE_PLANS)
        misc=self.src/'misc';misc.mkdir()
        Image.new('RGBA',(8,8)).save(misc/'fireball.png')
        Image.new('RGBA',(8,8)).save(misc/'magic_circle.png')
        Image.new('RGBA',(8,8)).save(misc/'noise_01.png')
        icons=self.src/'skillicons';icons.mkdir()
        Image.new('RGBA',(8,8)).save(icons/'fire.png')
        http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        threading.Thread(target=http.serve_forever,daemon=True).start()
        base='http://127.0.0.1:'+str(http.server_port)
        def post(endpoint,data):
            req=urllib.request.Request(base+endpoint,data=json.dumps(data).encode(),headers={'X-Token':server.TOKEN},method='POST')
            with urllib.request.urlopen(req) as response:return json.load(response)
        try:
            server.STATE['running']=False
            selected=post('/api/move-select',dict(source=str(self.src)))
            tags={row['keyword']:row['count'] for row in selected['keywords']}
            self.assertEqual(tags['fire'],1);self.assertEqual(tags['magic'],1)
            page=post('/api/move-selection',dict(selection=selected['selection'],offset=0,keywords=['fire','magic'],priority_keywords=['magic']))
            self.assertEqual(Path(page['items'][0]['source']).name,'magic_circle.png')
            empty=post('/api/move-selection',dict(selection=selected['selection'],offset=0,keywords=[]))
            self.assertEqual(empty['items'],[]);self.assertEqual(empty['total'],0);self.assertEqual(empty['bytes'],0)
            empty_plan=post('/api/move-preview',dict(selection=selected['selection'],source=str(self.src),target=str(self.dst),keywords=[]))
            self.assertEqual(empty_plan['total'],0);self.assertEqual(empty_plan['items'],[])
            preview=post('/api/move-preview',dict(selection=selected['selection'],source=str(self.src),target=str(self.dst),keywords=['fire','magic'],priority_keywords=['magic']))
            self.assertEqual(preview['total'],2)
            self.assertTrue(any(row['source'].endswith('fireball.png') for row in preview['items']))
        finally:
            http.shutdown();http.server_close();server.STATE.clear();server.STATE.update(state)
            server.MOVE_SELECTIONS.clear();server.MOVE_SELECTIONS.update(move_selections)
            server.MOVE_PLANS.clear();server.MOVE_PLANS.update(move_plans)

    def test_http_preview_confirm_and_progress(self):
        import server
        state=dict(server.STATE);move_state=dict(server.MOVE_STATUS)
        http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        threading.Thread(target=http.serve_forever,daemon=True).start()
        base='http://127.0.0.1:'+str(http.server_port)
        def post(endpoint,data,token=True):
            req=urllib.request.Request(base+endpoint,data=json.dumps(data).encode(),headers={'X-Token':server.TOKEN} if token else {},method='POST')
            with urllib.request.urlopen(req) as response:return json.load(response)
        try:
            server.STATE['running']=False
            with patch.object(server,'SAVE',self.logs):
                with self.assertRaises(urllib.error.HTTPError):post('/api/move-preview',{},False)
                selected=post('/api/move-select',dict(source=str(self.src)))
                self.assertEqual(selected['total'],1)
                page=post('/api/move-selection',dict(selection=selected['selection'],offset=0))
                self.assertEqual(len(page['items']),1)
                with urllib.request.urlopen(base+'/api/move-thumb?selection='+selected['selection']+'&id=0') as response:
                    self.assertEqual(response.headers['Content-Type'],'image/png')
                    self.assertTrue(response.read().startswith(b'\x89PNG'))
                self.assertEqual(post('/api/move-selection',dict(selection=selected['selection'],offset=100))['items'],[])
                p=post('/api/move-preview',dict(source=str(self.src),target=str(self.dst)))
                self.assertEqual(p['total'],1);self.assertTrue((self.src/'fx'/'a.png').exists())
                with self.assertRaises(urllib.error.HTTPError):post('/api/move-execute',dict(token=p['token']))
                post('/api/move-execute',dict(token=p['token'],confirm=True))
                for _ in range(200):
                    with urllib.request.urlopen(base+'/api/move-status') as response:s=json.load(response)
                    if not s['running']:break
                    time.sleep(.01)
                self.assertFalse(s['running']);self.assertEqual(s['outcome']['completed'],1)
                self.assertEqual(s['outcome']['errors'],[])
                with self.assertRaises(urllib.error.HTTPError):post('/api/move-execute',dict(token=p['token'],confirm=True))
        finally:
            http.shutdown();http.server_close();server.STATE.clear();server.STATE.update(state)
            server.MOVE_STATUS.clear();server.MOVE_STATUS.update(move_state)

if __name__=='__main__':unittest.main()
