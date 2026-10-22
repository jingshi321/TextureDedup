import sys
sys.dont_write_bytecode=True
import unittest, time, threading, sqlite3
from unittest.mock import patch
import engine, server
from PIL import Image

class ContentScoreTests(unittest.TestCase):
    def setUp(self):
        self.folder=engine.SAVE/('content-score-test-'+str(time.time_ns()));self.folder.mkdir()
    def row(self,name,color,size=(32,32)):
        path=self.folder/(name+'.png');Image.new('RGBA',size,color).save(path)
        return engine.read_image(str(path))
    def score(self,a,b,strict=True):
        return engine.content_similarity(a,b,engine.ResizePixels(),strict)
    def test_content_boundary_and_alpha(self):
        a=self.row('a',(0,0,0,255))
        b=self.row('b',(2,2,2,255));c=self.row('c',(3,3,3,255))
        self.assertGreaterEqual(self.score(a,b),.99)
        self.assertLess(self.score(a,c),.99)
        self.assertAlmostEqual(self.score(a,c),1-3/255,places=7)
        white=self.row('white',(255,255,255,255))
        self.assertEqual(self.score(a,white),0)
        transparent=self.row('transparent',(0,0,0,0))
        self.assertEqual(self.score(a,transparent),0)
        self.assertEqual(self.score(a,transparent,False),1)
        hidden=self.row('hidden',(255,255,255,0))
        self.assertEqual(self.score(transparent,hidden),0)
    def test_scaled_and_stretched_content(self):
        im=Image.new('RGBA',(32,32))
        for y in range(32):
            for x in range(32):im.putpixel((x,y),(x*4,y*4,(x+y)*2,255))
        rows=[]
        for name,size in [('a',(32,32)),('b',(64,64)),('c',(64,96))]:
            path=self.folder/(name+'.png');im.resize(size,Image.Resampling.NEAREST).save(path);rows.append(engine.read_image(str(path)))
        self.assertEqual(self.score(rows[0],rows[1]),1)
        self.assertEqual(self.score(rows[0],rows[2]),1)
    def test_legacy_rescore_cache_and_changed_source(self):
        a=self.row('a',(0,0,0,255));b=self.row('b',(3,3,3,255))
        result=dict(options={'alpha':True},records=[a,b],groups=[dict(id=0,ids=[0,1],kind='外观相似',score=1)],errors=[])
        with sqlite3.connect(str(self.folder/'cache.sqlite')) as db:
            with patch('engine.content_similarity',wraps=engine.content_similarity) as metric:
                engine.score_contents(result,db,lambda *a:None,threading.Event())
                self.assertEqual(metric.call_count,1)
                self.assertEqual(result['groups'][0]['search_score'],1)
                self.assertEqual(server.filter_groups(result,{'min_similarity':['99']}),[])
                engine.score_contents(result,db,lambda *a:None,threading.Event())
                self.assertEqual(metric.call_count,1)
            self.assertEqual(result['score_version'],engine.CONTENT_SCORE_VERSION)
            Image.new('RGBA',(64,64),(255,0,0,255)).save(b['path'])
            engine.score_contents(result,db,lambda *a:None,threading.Event())
            self.assertIn('score_error',result['groups'][0])
            self.assertEqual(server.filter_groups(result,{}),[])

    def test_parallel_scores_match_serial_and_cancel(self):
        rows=[self.row(str(i),(i*4,i*3,i*2,255),size=(64,64)) for i in range(9)]
        groups=[dict(id=i,ids=[0,i+1],kind='外观相似',score=1.) for i in range(8)]
        result=dict(options={'alpha':True},records=rows,groups=groups,errors=[])
        expected=[self.score(rows[0],rows[i+1]) for i in range(8)]
        original=engine.content_similarity;active=0;peak=0;lock=threading.Lock()
        def measured(*args):
            nonlocal active,peak
            with lock:active+=1;peak=max(peak,active)
            try:
                time.sleep(.01)
                return original(*args)
            finally:
                with lock:active-=1
        with sqlite3.connect(str(self.folder/'parallel.sqlite')) as db:
            with patch('engine.content_similarity',side_effect=measured):
                with patch('engine.adaptive_workers',return_value=6):
                    engine.score_contents(result,db,lambda *a:None,threading.Event())
            self.assertGreater(peak,1);self.assertLessEqual(peak,6)
            self.assertEqual([g['score'] for g in groups],expected)
            self.assertEqual(result['errors'],[])
            cancel=threading.Event();cancel.set()
            with self.assertRaises(engine.Cancelled):engine.score_contents(result,db,lambda *a:None,cancel)

if __name__=='__main__':unittest.main()
