import sys
sys.dont_write_bytecode=True
import threading,time,unittest
from unittest.mock import patch
import numpy as np
from PIL import Image
import engine as e


class CompareBatchTests(unittest.TestCase):
    def setUp(self):
        self.folder=e.SAVE/('compare-batch-'+str(time.time_ns()));self.folder.mkdir()
        self.count=33
        for index in range(self.count):
            Image.new('RGBA',(8+index,8+index),(20,30,40,255)).save(self.folder/f'{index:03}.png')
        self.options=dict(ai=False,similar=False,resize=True,refresh=True)

    def scan(self,cancel=None):
        return e.scan([str(self.folder)],self.options,lambda *args:None,
                      cancel or threading.Event(),self.folder/'index.sqlite')

    def dense_candidates(self,hashes,target,radius):
        return np.arange(len(hashes),dtype=np.intp) if len(hashes)==self.count-1 else np.array([],dtype=np.intp)

    def test_sparse_candidates_from_different_images_run_concurrently(self):
        lock=threading.Lock();concurrent=threading.Event();active=0;peak=0
        def sparse_candidates(hashes,target,radius):
            n=len(hashes)
            return np.array([n-1] if n%2 else [],dtype=np.intp)
        def resize(a,b,*args,**kwargs):
            nonlocal active,peak
            with lock:
                active+=1;peak=max(peak,active)
                if active>1:concurrent.set()
            try:concurrent.wait(1)
            finally:
                with lock:active-=1
            return True,1.0
        with patch.object(e,'adaptive_workers',return_value=2),patch.object(e,'hash_candidates',new=sparse_candidates),patch.object(e,'resize_match',new=resize):
            result=self.scan()
        self.assertGreater(peak,1)
        self.assertEqual(len(result['groups']),self.count//2)
        self.assertEqual(result['errors'],[])

    def test_dense_image_queues_bounded_work_and_keeps_candidate_order(self):
        original=e.ThreadPoolExecutor;lock=threading.Lock();unconsumed=0;peak=0;submitted=0
        def resize(a,b,*args,**kwargs):return True,1.0
        class TrackingPool(original):
            def submit(self,fn,*args,**kwargs):
                nonlocal unconsumed,peak,submitted
                future=super().submit(fn,*args,**kwargs)
                if fn is not resize:return future
                with lock:
                    unconsumed+=1;submitted+=1;peak=max(peak,unconsumed)
                original_result=future.result;consumed=False
                def result(*args,**kwargs):
                    nonlocal unconsumed,consumed
                    try:return original_result(*args,**kwargs)
                    finally:
                        with lock:
                            if not consumed:unconsumed-=1;consumed=True
                future.result=result
                return future
        with patch.object(e,'adaptive_workers',return_value=2),patch.object(e,'hash_candidates',new=self.dense_candidates),patch.object(e,'resize_match',new=resize),patch.object(e,'ThreadPoolExecutor',new=TrackingPool):
            result=self.scan()
        self.assertEqual(submitted,self.count-1)
        self.assertGreater(peak,1)
        self.assertLessEqual(peak,2*4)
        self.assertEqual(unconsumed,0)
        self.assertEqual([group['ids'] for group in result['groups']],[[i,self.count-1] for i in range(self.count-1)])
        self.assertEqual(result['errors'],[])

    def test_cancel_during_comparison_skips_later_candidate_batches(self):
        cancel=threading.Event();lock=threading.Lock();started=0
        def resize(a,b,*args,**kwargs):
            nonlocal started
            with lock:started+=1
            cancel.set()
            return True,1.0
        with patch.object(e,'adaptive_workers',return_value=2),patch.object(e,'hash_candidates',new=self.dense_candidates),patch.object(e,'resize_match',new=resize):
            with self.assertRaises(e.Cancelled):self.scan(cancel)
        self.assertGreater(started,0)
        self.assertLessEqual(started,2*4)
        self.assertLess(started,self.count-1)


if __name__=='__main__':unittest.main()
