import sys
sys.dont_write_bytecode=True
import tempfile, threading, unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import engine as e


class ResizeBatchTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory(prefix='resize-batch-test-',dir=e.SAVE)
        self.addCleanup(self.folder.cleanup)

    def row(self,name):
        raw=e.np.random.default_rng(23).integers(0,256,(377,513,4),dtype=e.np.uint8)
        raw[:,:80,3]=0
        path=e.Path(self.folder.name)/(name+'.png')
        e.Image.fromarray(raw).save(path)
        return dict(path=str(path),pixelhash=name,w=513,h=377)

    def test_multi_size_uses_one_decode_and_exact_original_channels(self):
        row=self.row('a');sizes=[(256,256),(137,91),(63,77)]
        with e.Image.open(row['path']) as source:
            rgba=source.convert('RGBA')
            expected={size:e.box_channels(rgba,size).astype(e.np.float32)/255 for size in sizes}
            reduced=e.Image.fromarray(e.box_channels(rgba,(256,256)))
            self.assertFalse(e.np.array_equal(e.box_channels(reduced,sizes[-1]),expected[sizes[-1]]*255))
        cache=e.ResizePixels()
        requests=[(row,size) for size in sizes]+[(row,sizes[0])]
        with patch('engine.Image.open',wraps=e.Image.open) as opened,ThreadPoolExecutor(max_workers=4) as pool:
            cache.prepare_many(requests,pool,lambda:None)
            for size in sizes:e.np.testing.assert_array_equal(cache.get(row,size),expected[size])
            self.assertEqual(opened.call_count,1)
        value=expected[sizes[-1]]
        self.assertGreater(e.np.count_nonzero(value[:,:,:3][value[:,:,3]==0]),0)

    def test_single_missing_size_does_not_start_a_preparation_wave(self):
        row=self.row('a');cache=e.ResizePixels()
        with patch('engine.Image.open',wraps=e.Image.open) as opened,ThreadPoolExecutor(max_workers=2) as pool:
            cache.prepare_many([(row,(32,32)),(row,(32,32))],pool,lambda:None)
            self.assertEqual(opened.call_count,0)
            cache.get(row,(32,32))
            cache.prepare_many([(row,(32,32)),(row,(16,16))],pool,lambda:None)
            self.assertEqual(opened.call_count,1)
            cache.get(row,(16,16));self.assertEqual(opened.call_count,2)

    def test_budget_skips_large_groups_and_keeps_lru_bounded(self):
        a=self.row('a');b=self.row('b');c=self.row('c');cache=e.ResizePixels(limit=32768)
        requests=[(a,(40,40)),(a,(32,32)),(b,(20,20)),(b,(16,16)),(c,(20,20)),(c,(16,16))]
        with patch('engine.Image.open',wraps=e.Image.open) as opened,ThreadPoolExecutor(max_workers=3) as pool:
            cache.prepare_many(requests,pool,lambda:None)
            self.assertEqual([call.args[0] for call in opened.call_args_list],[b['path']])
            self.assertEqual(set(cache.items),{('b',(20,20)),('b',(16,16))})
            self.assertLessEqual(cache.bytes,cache.limit//2)
            cache.get(a,(40,40));self.assertLessEqual(cache.bytes,cache.limit)
            cache.get(c,(48,48));self.assertLessEqual(cache.bytes,cache.limit)
            self.assertNotIn(('c',(48,48)),cache.items)
            self.assertEqual(cache.bytes,sum(value.nbytes for value in cache.items.values()))

    def test_preparation_errors_are_reported_by_the_ordinary_read(self):
        row=dict(path=str(e.Path(self.folder.name)/'missing.png'),pixelhash='bad',w=512,h=512)
        cache=e.ResizePixels()
        with ThreadPoolExecutor(max_workers=2) as pool:
            cache.prepare_many([(row,(32,32)),(row,(16,16))],pool,lambda:None)
        self.assertEqual(cache.items,{})
        with self.assertRaises(OSError):cache.get(row,(32,32))

    def test_cancellation_before_and_during_preparation_propagates(self):
        row=self.row('a');requests=[(row,(32,32)),(row,(16,16))];cache=e.ResizePixels()
        cancelled=threading.Event()
        def check():
            if cancelled.is_set():raise e.Cancelled()
        cancelled.set()
        with patch('engine.Image.open',wraps=e.Image.open) as opened,ThreadPoolExecutor(max_workers=2) as pool:
            with self.assertRaises(e.Cancelled):cache.prepare_many(requests,pool,check)
            self.assertEqual(opened.call_count,0)
        cancelled.clear();original=e.box_channels
        def resized(*args):
            value=original(*args);cancelled.set();return value
        with patch('engine.box_channels',side_effect=resized) as resize,ThreadPoolExecutor(max_workers=2) as pool:
            with self.assertRaises(e.Cancelled):cache.prepare_many(requests,pool,check)
            self.assertEqual(resize.call_count,1)
        self.assertIn(('a',(32,32)),cache.items)
        self.assertNotIn(('a',(16,16)),cache.items)

    def test_concurrent_get_reuses_the_batch_decode(self):
        row=self.row('a');cache=e.ResizePixels();opened=threading.Event();release=threading.Event()
        original=e.Image.open
        def delayed(*args,**kwargs):
            source=original(*args,**kwargs);opened.set()
            if not release.wait(5):source.close();raise TimeoutError('test release timed out')
            return source
        with patch('engine.Image.open',side_effect=delayed) as image_open,ThreadPoolExecutor(max_workers=4) as pool:
            preparation=pool.submit(cache.prepare_many,[(row,(32,32)),(row,(16,16))],pool,lambda:None)
            try:
                self.assertTrue(opened.wait(5))
                ordinary=pool.submit(cache.get,row,(16,16))
            finally:release.set()
            preparation.result(timeout=5);ordinary.result(timeout=5)
            self.assertEqual(image_open.call_count,1)


if __name__=='__main__':unittest.main()
