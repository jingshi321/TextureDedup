import sys
sys.dont_write_bytecode=True
import sqlite3, tempfile, threading, unittest
from unittest.mock import patch
import engine as e


class ScoreReuseTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='score-reuse-test-',dir=e.SAVE)
        self.addCleanup(self.temporary.cleanup)
        self.folder=e.Path(self.temporary.name)

    def row(self,name,color,size=(32,24)):
        path=self.folder/(name+'.png');e.Image.new('RGBA',size,color).save(path)
        return e.read_image(str(path))

    def result(self,rows,strict=True):
        return dict(options=dict(alpha=strict,refresh=True),records=rows,
                    groups=[dict(id=i-1,ids=[0,i],kind='外观相似',score=.9) for i in range(1,len(rows))],errors=[])

    def database(self,name='scores'):
        db=sqlite3.connect(str(self.folder/(name+'.sqlite')))
        self.addCleanup(db.close)
        db.execute('CREATE TABLE content_scores(a TEXT,b TEXT,alpha INTEGER,score REAL,PRIMARY KEY(a,b,alpha))')
        return db

    def test_resize_scores_match_original_box_formula_for_alpha_and_hidden_rgb(self):
        raw=e.np.random.default_rng(91).integers(0,200,(24,32,4),dtype=e.np.uint8)
        raw[:,:8,3]=0;image=e.Image.fromarray(raw)
        rows=[]
        for name,size in [('a',(32,24)),('scaled',(64,48)),('stretched',(64,72))]:
            path=self.folder/(name+'.png');image.resize(size,e.Image.Resampling.NEAREST).save(path)
            rows.append(e.read_image(str(path)))
        transparent=self.row('transparent',(0,0,0,0))
        opaque=self.row('opaque',(0,0,0,255),(64,48))
        hidden=self.row('hidden',(255,80,40,0),(64,48))
        pairs=[(rows[0],rows[1],True),(rows[0],rows[2],True),
               (transparent,opaque,False),(transparent,hidden,False)]
        for a,b,matched in pairs:
            for strict in (True,False):
                with self.subTest(pair=(a['path'],b['path']),alpha=strict):
                    size=(min(a['w'],b['w'],256),min(a['h'],b['h'],256))
                    arrays=[]
                    for row in (a,b):
                        with e.Image.open(row['path']) as source:
                            arrays.append(e.box_channels(source.convert('RGBA'),size).astype(e.np.float32)/255)
                    diff=e.np.abs(arrays[0]-arrays[1])
                    expected=max(0.,1-max(float(diff[:,:,:3].mean(dtype=e.np.float64)),
                                         float(diff[:,:,3].mean(dtype=e.np.float64)) if strict else 0.))
                    actual=e.resize_match(a,b,with_score=True,strict_alpha=strict)
                    self.assertEqual(actual,(matched,expected))
                    self.assertEqual(e.resize_match(a,b),matched)
                    self.assertEqual(e.content_similarity(a,b,e.ResizePixels(),strict),expected)

    def test_refresh_reuses_fresh_zero_score_but_recalculates_old_database_scores(self):
        a=self.row('a',(0,0,0,0));b=self.row('b',(0,0,0,255),(64,48))
        c=self.row('c',(255,0,0,0),(64,48));result=self.result([a,b,c]);db=self.database()
        ab=e.content_pair(a,b,True);ac=e.content_pair(a,c,True)
        self.assertEqual(ab,e.content_pair(b,a,True))
        db.executemany('INSERT INTO content_scores VALUES(?,?,?,?)',[(*ab,.3),(*ac,.4)])
        expected=e.content_similarity(a,c,e.ResizePixels(),True)
        fresh={ab:0.,e.content_pair(a,b,False):1.}
        with patch('engine.content_similarity',wraps=e.content_similarity) as metric:
            e.score_contents(result,db,lambda *args:None,threading.Event(),fresh_scores=fresh)
        self.assertEqual(metric.call_count,1)
        self.assertEqual([g['score'] for g in result['groups']],[0.,expected])
        self.assertEqual([g['search_score'] for g in result['groups']],[.9,.9])
        self.assertEqual(result['errors'],[])
        self.assertEqual(db.execute('SELECT score FROM content_scores WHERE a=? AND b=? AND alpha=?',ab).fetchone(),(0.,))
        self.assertEqual(db.execute('SELECT score FROM content_scores WHERE a=? AND b=? AND alpha=?',ac).fetchone(),(expected,))

    def test_opposite_alpha_fresh_key_cannot_replace_the_requested_score(self):
        a=self.row('a',(0,0,0,0));b=self.row('b',(0,0,0,255),(64,48));db=self.database()
        for strict,expected,opposite_score in [(True,0.,1.),(False,1.,0.)]:
            with self.subTest(alpha=strict):
                result=self.result([a,b],strict)
                fresh={e.content_pair(a,b,not strict):opposite_score}
                with patch('engine.content_similarity',wraps=e.content_similarity) as metric:
                    e.score_contents(result,db,lambda *args:None,threading.Event(),fresh_scores=fresh)
                self.assertEqual(metric.call_count,1)
                self.assertEqual(result['groups'][0]['score'],expected)
                self.assertEqual(result['errors'],[])

    def test_source_change_is_reported_before_fresh_score_reuse(self):
        a=self.row('a',(10,20,30,255));b=self.row('b',(10,20,30,255),(64,48))
        pair=e.content_pair(a,b,True);result=self.result([a,b]);db=self.database()
        e.Image.new('RGBA',(65,49),(255,0,0,255)).save(b['path'])
        with patch('engine.content_similarity',wraps=e.content_similarity) as metric:
            e.score_contents(result,db,lambda *args:None,threading.Event(),fresh_scores={pair:1.})
        self.assertEqual(metric.call_count,0)
        self.assertIn('源文件已变化',result['groups'][0]['score_error'])
        self.assertEqual(result['groups'][0]['score'],0.)
        self.assertEqual(len(result['errors']),1)
        self.assertIsNone(db.execute('SELECT score FROM content_scores WHERE a=? AND b=? AND alpha=?',pair).fetchone())

    def test_source_change_between_batches_invalidates_later_fresh_groups(self):
        a=self.row('a',(10,20,30,255));b=self.row('b',(10,20,30,255),(64,48))
        result=self.result([a,b]);db=self.database()
        result['groups']=[dict(id=i,ids=[0,1],kind='等比例缩放候选',score=.9) for i in range(129)]
        def progress(stage,done,total):
            if done==128:e.Image.new('RGBA',(65,49),(255,0,0,255)).save(b['path'])
        with patch('engine.content_similarity',wraps=e.content_similarity) as metric:
            e.score_contents(result,db,progress,threading.Event(),fresh_scores={e.content_pair(a,b,True):1.})
        self.assertEqual(metric.call_count,0)
        self.assertTrue(all(g['score']==1. and 'score_error' not in g for g in result['groups'][:128]))
        self.assertIn('源文件已变化',result['groups'][128]['score_error'])
        self.assertEqual(result['groups'][128]['score'],0.)
        self.assertEqual(len(result['errors']),1)

    def test_scan_reuses_both_matching_and_rejected_resize_scores(self):
        y,x=e.np.indices((24,32));raw=e.np.empty((24,32,4),dtype=e.np.uint8)
        raw[:,:,0]=20+x*4;raw[:,:,1]=20+y*4;raw[:,:,2]=40+((x//8+y//8)%2)*100;raw[:,:,3]=255
        for delta,kind in [(0,'等比例缩放候选'),(15,'外观相似')]:
            with self.subTest(delta=delta):
                folder=self.folder/str(delta);folder.mkdir()
                e.Image.fromarray(raw).save(folder/'a.png')
                changed=raw.copy();changed[:,:,:3]+=delta
                e.Image.fromarray(changed).resize((64,48),e.Image.Resampling.NEAREST).save(folder/'b.png')
                options=dict(resize=True,similar=True,refresh=True,ai=False,distance=64,alpha=True)
                with patch('engine.content_similarity',wraps=e.content_similarity) as metric:
                    result=e.scan([str(folder)],options,lambda *args:None,threading.Event(),folder/'scan.sqlite')
                self.assertEqual(result['errors'],[])
                self.assertEqual(len(result['groups']),1)
                self.assertEqual(result['groups'][0]['kind'],kind)
                self.assertEqual(metric.call_count,0)
                a,b=result['records']
                self.assertEqual(result['groups'][0]['score'],e.content_similarity(a,b,e.ResizePixels(),True))


if __name__=='__main__':unittest.main()
