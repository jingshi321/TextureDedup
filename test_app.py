import sys
sys.dont_write_bytecode=True
from engine import *
import unittest, shutil, threading, urllib.request, re, io
from PIL.PngImagePlugin import PngInfo

class TextureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Keep test artifacts under save; never remove user files.
        cls.folder=SAVE/('test-'+str(time.time_ns())); cls.folder.mkdir()
        rng=np.random.default_rng(8)
        a=np.zeros((64,64,4),np.uint8); a[:,:,:3]=rng.integers(0,256,(64,64,3),dtype=np.uint8); a[:,:,3]=255
        cls.image=Image.fromarray(a); cls.image.save(cls.folder/'original.png')
        shutil.copyfile(cls.folder/'original.png',cls.folder/'copy.png')
        info=PngInfo(); info.add_text('test','different container, same pixels')
        cls.image.save(cls.folder/'reencoded.png',pnginfo=info,compress_level=0)
        b=a.copy(); b[:,:,3]=0; Image.fromarray(b).save(cls.folder/'alpha.png')
        cls.image.resize((128,128),Image.Resampling.NEAREST).save(cls.folder/'resized.png')
        c=a.copy(); c[:,:,:3]=255-c[:,:,:3]; Image.fromarray(c).save(cls.folder/'different.png')
        (cls.folder/'broken.png').write_bytes(b'broken')
        cls.options={'similar':True,'alpha':True,'distance':10,'color':.08}
    def runscan(self,options=None,roots=None):
        return scan(roots or [str(self.folder)],options or self.options,lambda *a:None,threading.Event(),SAVE/'test-index.sqlite')
    def test_duplicates_alpha_overlap_cache(self):
        result=self.runscan(roots=[str(self.folder),str(self.folder)])
        self.assertEqual(result['scanned'],7); self.assertEqual(len(result['errors']),1)
        kinds=[g['kind'] for g in result['groups']]
        self.assertIn('文件完全重复',kinds); self.assertIn('RGBA 像素重复',kinds)
        for g in result['groups']:
            names={Path(result['records'][i]['path']).name for i in g['ids']}
            self.assertFalse('alpha.png' in names and 'original.png' in names)
            self.assertFalse('different.png' in names and 'original.png' in names)
        r2=self.runscan(); self.assertEqual(result['groups'],r2['groups'])
        p=self.folder/'modified.png'; self.image.save(p); r1=read_image(str(p))
        Image.new('RGBA',(32,32),(255,1,2,255)).save(p)
        r3=self.runscan(); changed=next(r for r in r3['records'] if r['path']==str(p))
        self.assertNotEqual(r1['pixelhash'],changed['pixelhash'])
    def test_ai(self):
        model=FeatureModel(); v=model.encode(str(self.folder/'original.png'))
        self.assertEqual(v.shape,(1280,)); self.assertAlmostEqual(float(np.linalg.norm(v)),1,places=5)
        r=self.runscan(dict(self.options,ai=True,threshold=.8))
        self.assertTrue(any(g['kind']=='AI 近似' for g in r['groups']))
        self.assertFalse(any('AI ' in e for e in r['errors']))
    def test_cancel(self):
        event=threading.Event(); event.set()
        with self.assertRaises(Cancelled): scan([str(self.folder)],self.options,lambda *a:None,event,SAVE/'test-index.sqlite')
    def test_server(self):
        import server
        hidden=self.folder/'hidden-rgb.png'
        Image.new('RGBA',(768,768),(73,132,33,0)).save(hidden)
        server.STATE['result']=self.runscan()
        http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        thread=threading.Thread(target=http.serve_forever,daemon=True); thread.start()
        base='http://127.0.0.1:'+str(http.server_port)
        try:
            def get(path):
                with urllib.request.urlopen(base+path) as r: return r.read()
            page=get('/')
            self.assertIn(server.TOKEN.encode(),page)
            self.assertIn(b'<textarea id="roots" spellcheck="false"></textarea>',page)
            self.assertNotIn(b'id="includeSimilarCleanup"',page)
            self.assertIn(b'id="moveKeywords"',page)
            self.assertIn(b'id="clearMoveKeywords"',page)
            self.assertIn('外观相似（感知哈希）'.encode(),page)
            self.assertIn('RGBA 像素'.encode(),page)
            self.assertIn(b'<div id="moveMore" class="hint"',page)
            self.assertNotIn(b'<button id="moveMore">',page)
            app=get('/app.js').decode('utf-8')
            self.assertIn("root:$('moveList'),rootMargin:'200px'",app)
            self.assertIn("api('/api/cleanup-execute',{token:cleanupPlan.token})",app)
            self.assertNotIn("confirm:true});watchCleanup",app)
            self.assertNotIn(b'id="csv"',page)
            self.assertNotIn(b'id="json"',page)
            self.assertGreater(json.loads(get('/api/results'))['total'],0)
            self.assertTrue(get('/api/thumb?id=0&channel=alpha').startswith(b'\x89PNG'))
            hidden_id=next(i for i,r in enumerate(server.STATE['result']['records']) if r['path']==str(hidden))
            rgb=Image.open(io.BytesIO(get('/api/thumb?id='+str(hidden_id)+'&channel=rgb')))
            self.assertEqual(rgb.getpixel((0,0)),(73,132,33))
            for format in ('csv','json'):
                with self.assertRaises(urllib.error.HTTPError) as removed:
                    get('/api/export?format='+format)
                self.assertEqual(removed.exception.code,404)
            from unittest.mock import patch
            with patch('server.os.startfile') as launch:
                req=urllib.request.Request(base+'/api/open-image',data=json.dumps({'id':hidden_id}).encode(),headers={'X-Token':server.TOKEN},method='POST')
                with urllib.request.urlopen(req) as r:self.assertTrue(json.load(r)['ok'])
                launch.assert_called_once_with(str(hidden.resolve()),'open')
                req=urllib.request.Request(base+'/api/open-image',data=b'{"id":-1}',headers={'X-Token':server.TOKEN},method='POST')
                with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(req)
                self.assertEqual(launch.call_count,1)
            req=urllib.request.Request(base+'/api/cancel',data=b'{}',headers={'X-Token':server.TOKEN},method='POST')
            with urllib.request.urlopen(req) as r: self.assertTrue(json.load(r)['ok'])
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(urllib.request.Request(base+'/api/cancel',data=b'{}',method='POST'))
        finally: http.shutdown(); http.server_close()

if __name__=='__main__':
    import io
    unittest.main(verbosity=2)
