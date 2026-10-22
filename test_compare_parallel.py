import sys
sys.dont_write_bytecode=True
import unittest,threading,time,sqlite3
from pathlib import Path
from PIL import Image
from unittest.mock import patch
import engine as e

class CompareParallelTests(unittest.TestCase):
    def test_vector_hash_candidates_match_bk_tree(self):
        import numpy as np
        rng=np.random.default_rng(73)
        hashes=rng.integers(0,2**64,size=160,dtype=np.uint64)
        hashes[20:28]=hashes[3]
        tree=e.BKTree()
        for index,value in enumerate(hashes):tree.add(int(value),index)
        for target in (int(hashes[3]),int(hashes[90]),0xFFFFFFFFFFFFFFFF):
            for radius in (0,6,12):
                expected=sorted(tree.find(target,radius))
                actual=e.hash_candidates(hashes,target,radius).tolist()
                self.assertEqual(actual,expected)

    def test_resize_candidates_are_same_and_parallel(self):
        folder=e.SAVE/('compare-parallel-'+str(time.time_ns()));folder.mkdir()
        base=Image.new('RGBA',(180,120),(20,30,40,255));base.save(folder/'a.png')
        base.resize((360,240),Image.Resampling.NEAREST).save(folder/'b.png')
        base.resize((270,180),Image.Resampling.NEAREST).save(folder/'c.png')
        result=e.scan([str(folder)],dict(ai=False,similar=False,resize=True,refresh=True),lambda *a:None,threading.Event(),folder/'index.sqlite')
        kinds=[g['kind'] for g in result['groups']]
        self.assertEqual(kinds.count('等比例缩放候选'),3)
        self.assertEqual(result['errors'],[])

    def test_adaptive_worker_limits(self):
        with e._cpu_sample_lock:
            old=e._cpu_sample;e._cpu_sample=(0,0,0)
        try:
            self.assertGreaterEqual(e.adaptive_workers(100),4)
            self.assertLessEqual(e.adaptive_workers(100),24)
        finally:
            with e._cpu_sample_lock:e._cpu_sample=old

    def test_content_scoring_uses_adaptive_worker_count(self):
        folder=e.SAVE/('score-parallel-'+str(time.time_ns()));folder.mkdir()
        first=Image.new('RGBA',(64,64),(20,30,40,255));first.save(folder/'a.png')
        second=first.copy();second.putpixel((20,20),(21,30,40,255));second.save(folder/'b.png')
        records=[e.read_image(str(folder/name)) for name in ('a.png','b.png')]
        groups=[dict(kind='外观相似',ids=[0,1],score=.9) for _ in range(16)]
        result=dict(records=records,groups=groups,options={'alpha':True},errors=[])
        original=e.ThreadPoolExecutor
        with sqlite3.connect(':memory:') as db:
            with patch.object(e,'adaptive_workers',return_value=13):
                with patch.object(e,'ThreadPoolExecutor',wraps=original) as pool:
                    e.score_contents(result,db,lambda *a:None,threading.Event())
                self.assertEqual(pool.call_args.kwargs['max_workers'],13)

if __name__=='__main__':unittest.main()
